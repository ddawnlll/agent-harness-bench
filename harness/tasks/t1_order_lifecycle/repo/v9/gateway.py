"""Deterministic recording broker.

The broker models the exchange boundary. It never touches the network: it
allocates stable identifiers and records submitted intents so tests and the
audit trail can assert exactly what the engine asked the venue to do.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class BrokerError(RuntimeError):
    pass


@dataclass
class StopIntent:
    client_stop_id: str
    order_id: str
    symbol: str
    qty: float
    stop_price: float


@dataclass
class RecordingBroker:
    stop_sequence: int = 0
    stops: list[StopIntent] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    order_cancels: list[str] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    reject_next_stop: bool = False

    def submit_order(self, order_id, symbol, side, qty, price) -> str:
        self.orders.append(
            {"order_id": order_id, "symbol": symbol, "side": side, "qty": qty, "price": price}
        )
        return order_id

    def submit_protective_stop(self, order_id, symbol, qty, stop_price) -> str:
        if self.reject_next_stop:
            self.reject_next_stop = False
            raise BrokerError("venue rejected protective stop")
        self.stop_sequence += 1
        cid = f"STOP-{order_id}-{self.stop_sequence}"
        self.stops.append(StopIntent(cid, order_id, symbol, float(qty), float(stop_price)))
        return cid

    def cancel_stop(self, client_stop_id: str) -> None:
        self.cancels.append(client_stop_id)

    def cancel_order(self, order_id: str) -> None:
        self.order_cancels.append(order_id)
