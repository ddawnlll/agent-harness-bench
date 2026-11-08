"""Position accounting with deterministic average-cost PnL."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def apply_fill(self, side: str, qty: float, price: float) -> None:
        """Apply a signed fill. BUY adds, SELL subtracts."""
        signed = float(qty) if side == "BUY" else -float(qty)
        if self.qty == 0 or (self.qty > 0) == (signed > 0):
            new_qty = self.qty + signed
            if new_qty != 0:
                self.avg_price = (
                    abs(self.qty) * self.avg_price + abs(signed) * float(price)
                ) / abs(new_qty)
            self.qty = new_qty
            return
        # Reducing or flipping: realize PnL on the closed quantity.
        closing = min(abs(self.qty), abs(signed))
        direction = 1.0 if self.qty > 0 else -1.0
        self.realized_pnl += (float(price) - self.avg_price) * closing * direction
        new_qty = self.qty + signed
        if abs(new_qty) < 1e-12:
            self.qty = 0.0
            self.avg_price = 0.0
        elif (new_qty > 0) != (self.qty > 0):
            # Flip: remaining exposure is priced at the fill price.
            self.qty = new_qty
            self.avg_price = float(price)
        else:
            self.qty = new_qty

    @property
    def is_open(self) -> bool:
        return abs(self.qty) > 1e-9
