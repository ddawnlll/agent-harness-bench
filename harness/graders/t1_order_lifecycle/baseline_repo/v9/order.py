"""Order identity and lifecycle state machine."""
from __future__ import annotations

from dataclasses import dataclass, field

NEW = "NEW"
PENDING_ACK = "PENDING_ACK"
WORKING = "WORKING"
FILLED = "FILLED"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"

TERMINAL = {FILLED, CANCELLED, REJECTED}


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    status: str = NEW
    native_id: str | None = None
    filled_qty: float = 0.0
    fill_ids: set[str] = field(default_factory=set)
    cancel_requested: bool = False

    @property
    def remaining(self) -> float:
        return max(0.0, self.qty - self.filled_qty)

    def apply_fill(self, fill_id: str, qty: float) -> bool:
        """Apply a fill exactly once.

        Returns True when the fill is new and was applied, False when the same
        venue fill id was already seen (duplicate delivery).
        """
        if self.filled_qty > 0:
            return False
        self.fill_ids.add(fill_id)
        self.filled_qty = min(self.qty, self.filled_qty + float(qty))
        if self.filled_qty >= self.qty - 1e-9:
            self.status = FILLED
        elif self.status in (NEW, PENDING_ACK):
            # A fill can arrive before the acknowledgement; fills are
            # authoritative and must never be dropped.
            self.status = WORKING
        return True

    def acknowledge(self, native_id: str | None, accepted: bool, expected_native: str | None = None) -> str:
        """Apply an entry acknowledgement and return a result token."""
        if self.status not in (NEW, PENDING_ACK):
            return "IGNORED_TERMINAL"
        if not accepted:
            self.status = REJECTED
            return "REJECTED"
        self.native_id = native_id
        if self.status == PENDING_ACK:
            self.status = WORKING
        return "ACKED"

    def request_cancel(self) -> str:
        if self.status in TERMINAL:
            return "IGNORED_TERMINAL"
        self.cancel_requested = True
        return "CANCEL_SENT"

    def apply_cancel_result(self, accepted: bool) -> str:
        if self.status in TERMINAL:
            return "IGNORED_TERMINAL"
        if accepted:
            self.status = CANCELLED
            return "CANCELLED"
        self.cancel_requested = False
        return "CANCEL_REJECTED"
