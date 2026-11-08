"""Deterministic engine coordinating orders, positions and protection."""
from __future__ import annotations

import copy

from .audit import Audit
from .events import validate_event
from .gateway import BrokerError, RecordingBroker
from .order import (
    CANCELLED,
    NEW,
    PENDING_ACK,
    REJECTED,
    WORKING,
    Order,
)
from .position import Position
from .protection import ACTIVE, PENDING_ACK as PROT_PENDING, Protection

RISK_UNPROTECTED = "RISK_UNPROTECTED"
RISK_PROTECTION_LOST = "RISK_PROTECTION_LOST"
RISK_IDENTITY_MISMATCH = "RISK_IDENTITY_MISMATCH"
RISK_BROKER_REJECT = "RISK_BROKER_REJECT"


class Engine:
    """Coordinator for order lifecycle, position and protective stop state.

    Contract (see TASK.md):
      * A position is protected only when an ACTIVE protective stop covers
        exactly the absolute position quantity for that symbol.
      * Protective stops are ACTIVE only after an accepted STOP_ACK.
      * Any unresolved protection on an open position blocks new entries.
      * Protective-stop loss, rejection or identity mismatch on an open
        position halts the engine (fail-closed).
    """

    def __init__(self, broker: RecordingBroker | None = None) -> None:
        self.broker = broker or RecordingBroker()
        self.orders: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.protections: dict[str, Protection] = {}
        self.protection_for_symbol: dict[str, str] = {}
        self.halted = False
        self.halt_reason: str | None = None
        self.audit = Audit()

    # ------------------------------------------------------------------ API
    def process(self, raw: dict) -> None:
        ev = validate_event(raw)
        etype = ev["type"]
        self.audit.append("EVENT", event_type=etype, event_seq=ev["seq"])
        handler = {
            "SUBMIT_ENTRY": self._on_submit,
            "ENTRY_ACK": self._on_ack,
            "FILL": self._on_fill,
            "CANCEL_REQUEST": self._on_cancel_request,
            "CANCEL_RESULT": self._on_cancel_result,
            "STOP_ACK": self._on_stop_ack,
            "STOP_CALLBACK": self._on_stop_callback,
            "RESTART": self._on_restart,
            "END": lambda e: None,
        }[etype]
        handler(ev)
        self._snapshot()

    def run(self, events: list[dict]) -> "Engine":
        for ev in events:
            self.process(ev)
        return self

    def position(self, symbol: str) -> Position:
        return self.positions.setdefault(symbol, Position(symbol))

    def protection_for(self, symbol: str) -> Protection | None:
        cid = self.protection_for_symbol.get(symbol)
        return self.protections.get(cid) if cid else None

    def is_protected(self, symbol: str) -> bool:
        pos = self.position(symbol)
        prot = self.protection_for(symbol)
        if not pos.is_open:
            return True
        if prot is None or prot.status != ACTIVE:
            return False
        return abs(prot.qty - abs(pos.qty)) <= 1e-9

    def unprotected_symbols(self) -> list[str]:
        return [s for s, p in self.positions.items() if p.is_open and not self.is_protected(s)]

    def can_enter(self, symbol: str) -> bool:
        if self.halted:
            return False
        if not self.is_protected(symbol):
            return False
        return not any(
            s != symbol and p.is_open and not self.is_protected(s) for s, p in self.positions.items()
        )

    # ------------------------------------------------------------- handlers
    def _on_submit(self, ev: dict) -> None:
        symbol = ev["symbol"]
        if ev["order_id"] in self.orders:
            self.audit.append("ORDER_DUPLICATE_REJECTED", order_id=ev["order_id"])
            return
        if not self.can_enter(symbol) and not self._is_risk_reducing(
            ev["side"], symbol, float(ev["qty"])
        ):
            self.audit.append(
                "RISK_BLOCK_ENTRY",
                order_id=ev["order_id"],
                symbol=symbol,
                reason=self.halt_reason or "UNPROTECTED_EXPOSURE",
            )
            return
        order = Order(
            order_id=ev["order_id"],
            symbol=symbol,
            side=ev["side"],
            qty=float(ev["qty"]),
            price=float(ev["price"]),
        )
        order.stop_distance = float(ev.get("stop_distance", 5.0))
        order.status = PENDING_ACK
        self.orders[order.order_id] = order
        self.broker.submit_order(order.order_id, symbol, order.side, order.qty, order.price)
        self.audit.append(
            "ORDER_SUBMITTED",
            order_id=order.order_id,
            symbol=symbol,
            side=order.side,
            qty=order.qty,
            price=order.price,
        )

    def _on_ack(self, ev: dict) -> None:
        order = self.orders.get(ev["order_id"])
        if order is None:
            self.audit.append("UNKNOWN_ORDER_ACK", order_id=ev["order_id"])
            return
        native = ev.get("native_id")
        expected = ev.get("expected_native_id")
        result = order.acknowledge(native, bool(ev["accepted"]), expected_native=expected)
        self.audit.append("ORDER_ACK", order_id=order.order_id, result=result, native_id=native)
        if result == "IDENTITY_MISMATCH":
            self.audit.append("RISK_IDENTITY_MISMATCH", order_id=order.order_id, native_id=native)
            if order.status in (NEW, PENDING_ACK):
                order.status = REJECTED
            if self.protected_open_positions():
                self._halt(RISK_IDENTITY_MISMATCH)

    def _on_fill(self, ev: dict) -> None:
        order = self.orders.get(ev["order_id"])
        if order is None:
            self.audit.append("UNKNOWN_FILL", order_id=ev["order_id"], fill_id=ev["fill_id"])
            return
        applied = order.apply_fill(ev["fill_id"], float(ev["qty"]))
        if not applied:
            self.audit.append(
                "FILL_DUPLICATE_IGNORED", order_id=order.order_id, fill_id=ev["fill_id"]
            )
            return
        pos = self.position(order.symbol)
        pos.apply_fill(order.side, float(ev["qty"]), float(ev["price"]))
        self.audit.append(
            "FILL_APPLIED",
            order_id=order.order_id,
            fill_id=ev["fill_id"],
            qty=float(ev["qty"]),
            price=float(ev["price"]),
            position_qty=pos.qty,
        )
        self._reconcile_protection(order)

    def _on_cancel_request(self, ev: dict) -> None:
        order = self.orders.get(ev["order_id"])
        if order is None:
            self.audit.append("UNKNOWN_CANCEL_REQUEST", order_id=ev["order_id"])
            return
        result = order.request_cancel()
        if result == "CANCEL_SENT":
            self.broker.cancel_order(order.order_id)
        self.audit.append("CANCEL_REQUESTED", order_id=order.order_id, result=result)

    def _on_cancel_result(self, ev: dict) -> None:
        order = self.orders.get(ev["order_id"])
        if order is None:
            self.audit.append("UNKNOWN_CANCEL_RESULT", order_id=ev["order_id"])
            return
        result = order.apply_cancel_result(bool(ev["accepted"]))
        self.audit.append("CANCEL_RESULT", order_id=order.order_id, result=result)
        if result == "CANCELLED" and self.position(order.symbol).is_open:
            prot = self.protection_for(order.symbol)
            if prot is not None:
                self.broker.cancel_stop(prot.client_stop_id)
                prot.cancel()
                self.protection_for_symbol.pop(order.symbol, None)
        elif self.position(order.symbol).is_open:
            self._reconcile_protection(order)

    def _on_stop_ack(self, ev: dict) -> None:
        prot = self.protections.get(ev["client_stop_id"])
        if prot is None:
            self.audit.append("UNKNOWN_STOP_ACK", client_stop_id=ev["client_stop_id"])
            return
        result = prot.acknowledge(ev.get("native_id"), bool(ev["accepted"]))
        self.audit.append(
            "STOP_ACK",
            client_stop_id=prot.client_stop_id,
            result=result,
            native_id=ev.get("native_id"),
        )
        if result == "REJECTED" and self.position(prot.symbol).is_open:
            self.audit.append(
                "RISK_UNPROTECTED", client_stop_id=prot.client_stop_id, symbol=prot.symbol
            )
            self._halt(RISK_UNPROTECTED)

    def _on_stop_callback(self, ev: dict) -> None:
        prot = self.protections.get(ev["client_stop_id"])
        if prot is None:
            self.audit.append("UNKNOWN_STOP_CALLBACK", client_stop_id=ev["client_stop_id"])
            return
        result = prot.apply_callback(int(ev["version"]), str(ev["state"]))
        self.audit.append(
            "STOP_CALLBACK",
            client_stop_id=prot.client_stop_id,
            result=result,
            version=int(ev["version"]),
            state=ev["state"],
        )
        if result == "LOST" and self.position(prot.symbol).is_open:
            self.audit.append(
                "RISK_PROTECTION_LOST", client_stop_id=prot.client_stop_id, symbol=prot.symbol
            )

    # ------------------------------------------------------------- internals
    def _is_risk_reducing(self, side: str, symbol: str, qty: float) -> bool:
        pos = self.position(symbol)
        if not pos.is_open:
            return False
        if pos.qty > 0:
            return side == "SELL" and qty <= abs(pos.qty) + 1e-9
        return side == "BUY" and qty <= abs(pos.qty) + 1e-9

    def protected_open_positions(self) -> list[str]:
        return [s for s, p in self.positions.items() if p.is_open]

    def _reconcile_protection(self, order: Order) -> None:
        symbol = order.symbol
        pos = self.position(symbol)
        prot = self.protection_for(symbol)
        if not pos.is_open:
            if prot is not None and prot.status in (PROT_PENDING, ACTIVE):
                self.broker.cancel_stop(prot.client_stop_id)
                prot.cancel()
                self.protection_for_symbol.pop(symbol, None)
                self.audit.append(
                    "PROTECTION_CANCELLED", client_stop_id=prot.client_stop_id, reason="flat"
                )
            return
        desired_qty = abs(pos.qty)
        if prot is not None:
            return
            if prot.status in (PROT_PENDING, ACTIVE):
                self.broker.cancel_stop(prot.client_stop_id)
                prot.cancel()
                self.audit.append(
                    "PROTECTION_CANCELLED", client_stop_id=prot.client_stop_id, reason="resize"
                )
        stop_price = (
            pos.avg_price - order.stop_distance
            if pos.qty > 0
            else pos.avg_price + order.stop_distance
        )
        try:
            cid = self.broker.submit_protective_stop(
                order.order_id, symbol, desired_qty, round(stop_price, 8)
            )
        except BrokerError as exc:
            self.audit.append(
                "RISK_UNPROTECTED", symbol=symbol, reason="BROKER_REJECT", detail=str(exc)
            )
            self._halt(RISK_BROKER_REJECT)
            return
        p = Protection(
            client_stop_id=cid,
            order_id=order.order_id,
            symbol=symbol,
            qty=desired_qty,
            stop_price=round(stop_price, 8),
            status=ACTIVE,
        )
        self.protections[cid] = p
        self.protection_for_symbol[symbol] = cid
        self.audit.append(
            "PROTECTION_SUBMITTED",
            client_stop_id=cid,
            symbol=symbol,
            qty=desired_qty,
            stop_price=p.stop_price,
        )

    def _halt(self, reason: str) -> None:
        if not self.halted:
            self.halted = True
            self.halt_reason = reason
            self.audit.append("HALTED", reason=reason)

    def _snapshot(self) -> None:
        state = {
            "orders": {k: self._order_state(v) for k, v in self.orders.items()},
            "positions": {k: vars(v) for k, v in self.positions.items()},
            "protections": {k: self._protection_state(v) for k, v in self.protections.items()},
            "protection_for_symbol": dict(self.protection_for_symbol),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }
        self.audit.append("STATE_SNAPSHOT", state=copy.deepcopy(state))

    @staticmethod
    def _order_state(o: Order) -> dict:
        d = vars(o).copy()
        d["fill_ids"] = sorted(o.fill_ids)
        return d

    @staticmethod
    def _protection_state(p: Protection) -> dict:
        return vars(p).copy()

    def _on_restart(self, ev: dict) -> None:
        snapshot = self.audit.last_snapshot()
        if snapshot is None:
            self.audit.append("RESTART_NO_STATE")
            return
        self.orders = {}
        for oid, d in snapshot["orders"].items():
            o = Order(
                order_id=d["order_id"],
                symbol=d["symbol"],
                side=d["side"],
                qty=d["qty"],
                price=d["price"],
            )
            o.status = d["status"]
            o.native_id = d["native_id"]
            o.filled_qty = d["filled_qty"]
            o.fill_ids = set(d["fill_ids"])
            o.cancel_requested = d["cancel_requested"]
            o.stop_distance = d.get("stop_distance", 5.0)
            self.orders[oid] = o
        self.positions = {}
        for sym, d in snapshot["positions"].items():
            p = Position(sym)
            p.qty = d["qty"]
            p.avg_price = d["avg_price"]
            p.realized_pnl = d["realized_pnl"]
            self.positions[sym] = p
        self.protections = {}
        for cid, d in snapshot["protections"].items():
            p = Protection(
                client_stop_id=d["client_stop_id"],
                order_id=d["order_id"],
                symbol=d["symbol"],
                qty=d["qty"],
                stop_price=d["stop_price"],
                status=ACTIVE if d["status"] == PROT_PENDING else d["status"],
                native_id=d["native_id"],
                version=d["version"],
            )
            self.protections[cid] = p
        self.protection_for_symbol = dict(snapshot["protection_for_symbol"])
        self.halted = snapshot["halted"]
        self.halt_reason = snapshot["halt_reason"]
        self.audit.append("RESTARTED", halted=self.halted, open_positions=len(self.positions))
