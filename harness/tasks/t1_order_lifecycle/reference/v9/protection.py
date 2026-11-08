"""Protective-stop state machine.

States (strict):
  PENDING_ACK  submitted to the venue, acknowledgement not yet received
  ACTIVE       venue acknowledged the stop
  FAILED       venue rejected the stop
  CANCELLED    stop cancelled by us or reported cancelled by the venue
  TRIGGERED    stop executed

Only ACTIVE protection may be treated as protection. PENDING_ACK is a
transitional state and must stay fail-closed for new exposure.
"""
from __future__ import annotations

from dataclasses import dataclass

PENDING_ACK = "PENDING_ACK"
ACTIVE = "ACTIVE"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
TRIGGERED = "TRIGGERED"

COVERING_STATES = {PENDING_ACK, ACTIVE}


@dataclass
class Protection:
    client_stop_id: str
    order_id: str
    symbol: str
    qty: float
    stop_price: float
    status: str = PENDING_ACK
    native_id: str | None = None
    version: int = 0

    @property
    def is_active(self) -> bool:
        return self.status == ACTIVE

    def acknowledge(self, native_id: str | None, accepted: bool) -> str:
        if self.status not in (PENDING_ACK,):
            return "IGNORED_TERMINAL"
        if not accepted:
            self.status = FAILED
            return "REJECTED"
        self.native_id = native_id
        self.status = ACTIVE
        self.version += 1
        return "ACTIVE"

    def apply_callback(self, version: int, state: str) -> str:
        """Apply a venue callback, rejecting stale/duplicate versions."""
        if version <= self.version:
            return "IGNORED_STALE"
        self.version = version
        if state == "ACTIVE":
            if self.status == PENDING_ACK:
                self.status = ACTIVE
            return "ACTIVE"
        if state in ("CANCELLED", "REJECTED", "FAILED"):
            self.status = CANCELLED if state == "CANCELLED" else FAILED
            return "LOST"
        if state == "TRIGGERED":
            self.status = TRIGGERED
            return "TRIGGERED"
        return "IGNORED_UNKNOWN_STATE"

    def cancel(self) -> str:
        if self.status in (CANCELLED, TRIGGERED):
            return "IGNORED_TERMINAL"
        self.status = CANCELLED
        return "CANCELLED"
