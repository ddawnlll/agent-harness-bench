"""Price-level order book with strict price-time priority.

Optimised implementation (oracle): price levels are kept in sorted lists so
the top of book is O(1), levels are deques so FIFO consumption is O(1), live
orders are indexed by id, and the liquidity checksum / open quantity are
maintained incrementally instead of being rescanned.

The public behaviour (method names, return values) is identical to the
baseline implementation.
"""

from bisect import insort, bisect_left
from collections import deque


class Order(object):
    __slots__ = ("id", "account", "symbol", "side", "price", "remaining")

    def __init__(self, order_id, account, symbol, side, price, remaining):
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell': %r" % (side,))
        if price <= 0:
            raise ValueError("price must be positive: %r" % (price,))
        if remaining <= 0:
            raise ValueError("quantity must be positive: %r" % (remaining,))
        self.id = order_id
        self.account = account
        self.symbol = symbol
        self.side = side
        self.price = price
        self.remaining = remaining

    def __repr__(self):
        return "Order(id=%r, %s %s %s @ %d x %d)" % (
            self.id, self.account, self.side, self.symbol,
            self.price, self.remaining,
        )


class Book(object):
    def __init__(self, symbol):
        self.symbol = symbol
        self.bids = {}
        self.asks = {}
        self.bid_prices = []
        self.ask_prices = []
        self.orders = {}
        self._checksum = 0
        self._open_qty = 0

    # -- placement ---------------------------------------------------------
    def rest(self, order):
        if order.side == "buy":
            levels, prices = self.bids, self.bid_prices
        else:
            levels, prices = self.asks, self.ask_prices
        level = levels.get(order.price)
        if level is None:
            levels[order.price] = deque((order,))
            insort(prices, order.price)
        else:
            level.append(order)
        self.orders[order.id] = order
        self._checksum += order.price * order.remaining
        self._open_qty += order.remaining

    def remove(self, order):
        found = self.orders.pop(order.id, None)
        if found is None:
            return False
        if found.side == "buy":
            levels, prices = self.bids, self.bid_prices
        else:
            levels, prices = self.asks, self.ask_prices
        level = levels[found.price]
        try:
            level.remove(found)
        except ValueError:
            pass
        if not level:
            del levels[found.price]
            del prices[bisect_left(prices, found.price)]
        self._checksum -= found.price * found.remaining
        self._open_qty -= found.remaining
        return True

    def note_fill(self, order, qty):
        """Account for ``qty`` units of ``order`` having just been traded."""
        self._checksum -= order.price * qty
        self._open_qty -= qty

    # -- lookup ------------------------------------------------------------
    def find(self, order_id):
        return self.orders.get(order_id)

    def level_orders(self, side, price):
        levels = self.bids if side == "buy" else self.asks
        level = levels.get(price)
        if level is None:
            return []
        return list(level)

    def head(self, side, price):
        levels = self.bids if side == "buy" else self.asks
        level = levels.get(price)
        if not level:
            return None
        return level[0]

    # -- top of book -------------------------------------------------------
    def best_bid(self):
        prices = self.bid_prices
        return prices[-1] if prices else None

    def best_ask(self):
        prices = self.ask_prices
        return prices[0] if prices else None

    # -- aggregates --------------------------------------------------------
    def resting_count(self):
        return len(self.orders)

    def open_qty(self):
        return self._open_qty

    def checksum(self):
        return self._checksum

    def is_crossed(self):
        bid = self.best_bid()
        ask = self.best_ask()
        return bid is not None and ask is not None and bid >= ask
