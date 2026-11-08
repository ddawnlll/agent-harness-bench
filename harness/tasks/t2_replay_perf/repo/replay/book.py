"""Price-level order book with strict price-time priority.

The book stores resting (unfilled) limit orders only.  Matching is driven by
``replay.engine``; the book itself does not know about accounts or trades.

A book is single-symbol.  Prices are integer ticks (cents), quantities are
integer units.  All arithmetic is exact integer arithmetic.
"""


class Order(object):
    """A resting limit order.

    ``remaining`` is the unfilled quantity.  Orders are never mutated after
    they leave the book, so trade records can safely reference them.
    """

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
    """Order book for one symbol.

    ``bids`` and ``asks`` map price -> FIFO list of live orders.  ``orders``
    holds every live resting order so that state summaries can be produced
    from a single traversal.
    """

    def __init__(self, symbol):
        self.symbol = symbol
        self.bids = {}
        self.asks = {}
        self.orders = []

    # -- placement ---------------------------------------------------------
    def rest(self, order):
        """Add a live order to the book."""
        levels = self.bids if order.side == "buy" else self.asks
        level = levels.get(order.price)
        if level is None:
            level = []
            levels[order.price] = level
        level.append(order)
        self.orders.append(order)

    def remove(self, order):
        """Remove a live order from the book.  Returns True if it was live."""
        found = self.find(order.id)
        if found is None:
            return False
        self.orders.remove(found)
        levels = self.bids if found.side == "buy" else self.asks
        level = levels[found.price]
        level.remove(found)
        if not level:
            del levels[found.price]
        return True

    # -- lookup ------------------------------------------------------------
    def find(self, order_id):
        for order in self.orders:
            if order.id == order_id:
                return order
        return None

    def level_orders(self, side, price):
        """FIFO list of live orders at ``side``/``price`` (may be empty)."""
        levels = self.bids if side == "buy" else self.asks
        return list(levels.get(price, ()))

    # -- top of book -------------------------------------------------------
    def best_bid(self):
        if not self.bids:
            return None
        return max(self.bids)

    def best_ask(self):
        if not self.asks:
            return None
        return min(self.asks)

    # -- aggregates --------------------------------------------------------
    def resting_count(self):
        return len(self.orders)

    def open_qty(self):
        return sum(order.remaining for order in self.orders)

    def checksum(self):
        """Integer checksum of all resting liquidity: sum(price * remaining)."""
        return sum(order.price * order.remaining for order in self.orders)

    def is_crossed(self):
        bid = self.best_bid()
        ask = self.best_ask()
        return bid is not None and ask is not None and bid >= ask
