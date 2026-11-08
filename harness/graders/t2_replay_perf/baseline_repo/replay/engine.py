"""Historical market-event replay: matching, positions, PnL, audit trail.

Processing model
----------------
Events are applied in ``(ts, seq)`` order.  A submit event is a limit order:

* it is first matched against the opposite side of the book using strict
  price-time priority (best price first, FIFO inside a price level);
* fills happen at the *resting* order's price (maker price), the taker pays
  or receives that price;
* if quantity remains it rests in the book, otherwise the order is done.

A cancel event removes the remaining quantity of a live order.  Cancelling an
order that is unknown, already filled or already cancelled is rejected.

Accounting
----------
Every fill moves ``qty`` from the seller's position to the buyer's and moves
``notional = price * qty`` in cash from buyer to seller.  Both sides pay a
maker/taker fee of ``notional * FEE_BPS // 10000``.  Positions are signed
quantities per ``(account, symbol)``.  Final PnL is::

    pnl = cash + sum(position[symbol] * mark[symbol])

where ``mark`` is the mid price of the symbol's book after the last event
(falling back to the last trade price, then to the single-sided quote, then
to zero).

Audit trail
-----------
After every event the engine appends one audit line::

    ts,seq,type,order_id,status,checksum,resting,net

``checksum`` is the total resting liquidity across all books
(``sum(price * remaining)`` over every live order), ``resting`` is the number
of live orders and ``net`` is ``SYMBOL:qty`` for each symbol in sorted order,
where ``qty`` is the open interest on the long side: the sum of all *positive*
account positions in that symbol (short positions do not reduce it).  The
audit digest
in the result is ``sha256("\\n".join(audit_lines)).hexdigest()[:16]``.
"""

import hashlib

from .book import Book, Order

SCHEMA = "replay.v1"
FEE_BPS = 2


class ReplayError(ValueError):
    """Raised when the event stream cannot be replayed."""


class Account(object):
    __slots__ = ("id", "cash", "fees", "traded_qty", "position")

    def __init__(self, account_id):
        self.id = account_id
        self.cash = 0
        self.fees = 0
        self.traded_qty = 0
        self.position = {}


class Engine(object):
    def __init__(self, symbols):
        self.symbols = sorted(symbols)
        if not self.symbols:
            raise ReplayError("at least one symbol is required")
        self.books = {sym: Book(sym) for sym in self.symbols}
        self.accounts = {}
        self.seen = set()
        self.trades = []
        self.audit = []
        self.state_history = []
        self.last_trade_price = {sym: None for sym in self.symbols}
        self.lifecycle = {
            "submitted": 0,
            "cancelled": 0,
            "rejected": 0,
            "filled_orders": 0,
            "open_at_end": 0,
            "open_qty_at_end": 0,
            "trade_count": 0,
            "traded_qty": 0,
            "traded_notional": 0,
            "max_open_orders": 0,
        }

    # -- public API --------------------------------------------------------
    def process(self, events):
        for event in events:
            self.apply(event)
        return self

    def apply(self, event):
        etype = event["type"]
        if etype == "submit":
            status = self._submit(event)
        elif etype == "cancel":
            status = self._cancel(event)
        else:
            raise ReplayError("unknown event type %r" % (etype,))
        self._record_audit(event, status)

    def result(self):
        marks = {}
        for sym in self.symbols:
            book = self.books[sym]
            bid = book.best_bid()
            ask = book.best_ask()
            last = self.last_trade_price[sym]
            if bid is not None and ask is not None:
                mark = (bid + ask) // 2
            elif last is not None:
                mark = last
            elif ask is not None:
                mark = ask
            elif bid is not None:
                mark = bid
            else:
                mark = 0
            marks[sym] = mark

        accounts = {}
        for account_id in sorted(self.accounts):
            account = self.accounts[account_id]
            value = 0
            for sym, qty in account.position.items():
                if qty:
                    value += qty * marks[sym]
            accounts[account_id] = {
                "cash": account.cash,
                "fees": account.fees,
                "traded_qty": account.traded_qty,
                "pnl": account.cash + value,
                "positions": {sym: qty for sym, qty in
                              sorted(account.position.items()) if qty},
            }

        symbols = {}
        for sym in self.symbols:
            book = self.books[sym]
            history = self.state_history
            absolute = [abs(snapshot[sym]) for snapshot in history]
            symbols[sym] = {
                "best_bid": book.best_bid(),
                "best_ask": book.best_ask(),
                "checksum": book.checksum(),
                "mark": marks[sym],
                "max_open_interest": max(absolute) if absolute else 0,
                "mean_open_interest": (sum(absolute) // len(absolute)
                                       if absolute else 0),
                "open_orders": book.resting_count(),
                "open_qty": book.open_qty(),
                "traded_qty": sum(
                    trade["qty"] for trade in self.trades
                    if trade["symbol"] == sym),
            }

        lifecycle = dict(self.lifecycle)
        lifecycle["open_at_end"] = sum(
            self.books[sym].resting_count() for sym in self.symbols)
        lifecycle["open_qty_at_end"] = sum(
            self.books[sym].open_qty() for sym in self.symbols)

        digest = hashlib.sha256(
            "\n".join(self.audit).encode("utf-8")).hexdigest()[:16]

        return {
            "schema": SCHEMA,
            "event_count": len(self.audit),
            "trade_count": len(self.trades),
            "trades": self.trades,
            "accounts": accounts,
            "symbols": symbols,
            "lifecycle": lifecycle,
            "audit": {"digest": digest, "entries": len(self.audit)},
        }

    # -- event handling ----------------------------------------------------
    def _account(self, account_id):
        account = self.accounts.get(account_id)
        if account is None:
            account = Account(account_id)
            self.accounts[account_id] = account
        return account

    def _submit(self, event):
        order_id = event["order_id"]
        if order_id in self.seen:
            self.lifecycle["rejected"] += 1
            return "rejected_duplicate"
        self.seen.add(order_id)
        sym = event["symbol"]
        if sym not in self.books:
            self.lifecycle["rejected"] += 1
            return "rejected_unknown_symbol"
        self.lifecycle["submitted"] += 1
        self._account(event["account"])
        taker = Order(order_id, event["account"], sym, event["side"],
                      event["price"], event["qty"])
        filled = self._match(taker, event["ts"], event["seq"])
        if taker.remaining > 0:
            self.books[sym].rest(taker)
            return "partial" if filled else "rested"
        return "filled"

    def _cancel(self, event):
        order_id = event["order_id"]
        found = None
        for sym in self.symbols:
            found = self.books[sym].find(order_id)
            if found is not None:
                break
        if found is None:
            self.lifecycle["rejected"] += 1
            return "rejected_unknown"
        self.books[found.symbol].remove(found)
        self.lifecycle["cancelled"] += 1
        return "cancelled"

    def _match(self, taker, ts, seq):
        book = self.books[taker.symbol]
        if taker.side == "buy":
            opposite = "sell"
            crosses = lambda price: price <= taker.price  # noqa: E731
            best = book.best_ask
        else:
            opposite = "buy"
            crosses = lambda price: price >= taker.price  # noqa: E731
            best = book.best_bid

        filled = 0
        while taker.remaining > 0:
            price = best()
            if price is None or not crosses(price):
                break
            level = book.level_orders(opposite, price)
            if not level:
                break
            maker = level[0]
            qty = taker.remaining
            if maker.remaining < qty:
                qty = maker.remaining
            self._apply_fill(ts, seq, taker, maker, price, qty)
            remaining = taker.remaining - qty
            taker.remaining = remaining
            maker.remaining -= qty
            filled += qty
            if maker.remaining == 0:
                book.remove(maker)
                self.lifecycle["filled_orders"] += 1
        if taker.remaining == 0:
            self.lifecycle["filled_orders"] += 1
        return filled

    def _apply_fill(self, ts, seq, taker, maker, price, qty):
        notional = price * qty
        fee = notional * FEE_BPS // 10000
        if taker.side == "buy":
            buyer = self._account(taker.account)
            seller = self._account(maker.account)
        else:
            buyer = self._account(maker.account)
            seller = self._account(taker.account)

        buyer.cash -= notional + fee
        buyer.fees += fee
        buyer.traded_qty += qty
        seller.cash += notional - fee
        seller.fees += fee
        seller.traded_qty += qty

        buyer.position[taker.symbol] = buyer.position.get(taker.symbol, 0) + qty
        seller.position[taker.symbol] = (
            seller.position.get(taker.symbol, 0) - qty)

        self.trades.append({
            "ts": ts,
            "seq": seq,
            "symbol": taker.symbol,
            "price": price,
            "qty": qty,
            "taker": taker.id,
            "maker": maker.id,
            "taker_account": taker.account,
            "maker_account": maker.account,
        })
        self.last_trade_price[taker.symbol] = price
        self.lifecycle["trade_count"] += 1
        self.lifecycle["traded_qty"] += qty
        self.lifecycle["traded_notional"] += notional

    # -- audit -------------------------------------------------------------
    def _record_audit(self, event, status):
        net = {}
        for sym in self.symbols:
            total = 0
            for account in self.accounts.values():
                held = account.position.get(sym, 0)
                if held > 0:
                    total += held
            net[sym] = total

        checksum = 0
        resting = 0
        for sym in self.symbols:
            book = self.books[sym]
            checksum += book.checksum()
            resting += book.resting_count()
        if resting > self.lifecycle["max_open_orders"]:
            self.lifecycle["max_open_orders"] = resting

        netstr = ";".join("%s:%d" % (sym, net[sym]) for sym in self.symbols)
        line = "%d,%d,%s,%d,%s,%d,%d,%s" % (
            event["ts"], event["seq"], event["type"], event["order_id"],
            status, checksum, resting, netstr)
        self.audit.append(line)
        self.state_history.append(net)
