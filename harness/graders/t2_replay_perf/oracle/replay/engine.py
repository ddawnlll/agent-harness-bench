"""Historical market-event replay: matching, positions, PnL, audit trail.

Optimised implementation (oracle).  The externally visible behaviour, the
event semantics and the result document are identical to the baseline; only
the data structures and the amount of work per event differ:

* the book is indexed by order id and keeps sorted price lists plus
  incremental liquidity counters, so the audit snapshot is O(1) per book;
* open interest (long side) is maintained incrementally per symbol instead of
  being recomputed from all accounts after every event;
* per-symbol traded quantity is accumulated during matching;
* the per-event book state is not materialised as history: the max/mean open
  interest is folded into running accumulators.
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
        self.live = {}
        self.trades = []
        self.audit = []
        self.last_trade_price = {sym: None for sym in self.symbols}
        self.open_interest = {sym: 0 for sym in self.symbols}
        self.oi_total = {sym: 0 for sym in self.symbols}
        self.oi_max = {sym: 0 for sym in self.symbols}
        self.traded_qty = {sym: 0 for sym in self.symbols}
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
            symbols[sym] = {
                "best_bid": book.best_bid(),
                "best_ask": book.best_ask(),
                "checksum": book.checksum(),
                "mark": marks[sym],
                "max_open_interest": self.oi_max[sym],
                "mean_open_interest": (self.oi_total[sym] // len(self.audit)
                                       if self.audit else 0),
                "open_orders": book.resting_count(),
                "open_qty": book.open_qty(),
                "traded_qty": self.traded_qty[sym],
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
            self.live[order_id] = taker
            return "partial" if filled else "rested"
        return "filled"

    def _cancel(self, event):
        order_id = event["order_id"]
        found = self.live.get(order_id)
        if found is None:
            self.lifecycle["rejected"] += 1
            return "rejected_unknown"
        self.books[found.symbol].remove(found)
        del self.live[order_id]
        self.lifecycle["cancelled"] += 1
        return "cancelled"

    def _match(self, taker, ts, seq):
        book = self.books[taker.symbol]
        if taker.side == "buy":
            opposite = "sell"
            best = book.best_ask
            limit = taker.price
            crosses = lambda price: price <= limit  # noqa: E731
        else:
            opposite = "buy"
            best = book.best_bid
            limit = taker.price
            crosses = lambda price: price >= limit  # noqa: E731

        filled = 0
        while taker.remaining > 0:
            price = best()
            if price is None or not crosses(price):
                break
            maker = book.head(opposite, price)
            if maker is None:
                break
            qty = taker.remaining
            if maker.remaining < qty:
                qty = maker.remaining
            self._apply_fill(ts, seq, taker, maker, price, qty)
            taker.remaining -= qty
            maker.remaining -= qty
            filled += qty
            book.note_fill(maker, qty)
            if maker.remaining == 0:
                book.remove(maker)
                del self.live[maker.id]
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

        sym = taker.symbol
        self._bump(buyer, sym, qty)
        self._bump(seller, sym, -qty)

        self.trades.append({
            "ts": ts,
            "seq": seq,
            "symbol": sym,
            "price": price,
            "qty": qty,
            "taker": taker.id,
            "maker": maker.id,
            "taker_account": taker.account,
            "maker_account": maker.account,
        })
        self.last_trade_price[sym] = price
        self.lifecycle["trade_count"] += 1
        self.lifecycle["traded_qty"] += qty
        self.lifecycle["traded_notional"] += notional
        self.traded_qty[sym] += qty

    def _bump(self, account, sym, delta):
        position = account.position
        old = position.get(sym, 0)
        new = old + delta
        if new:
            position[sym] = new
        elif old:
            del position[sym]
        interest = self.open_interest
        if old > 0:
            interest[sym] -= old
        if new > 0:
            interest[sym] += new

    # -- audit -------------------------------------------------------------
    def _record_audit(self, event, status):
        interest = self.open_interest
        oi_total = self.oi_total
        oi_max = self.oi_max
        net_parts = []
        for sym in self.symbols:
            value = interest[sym]
            oi_total[sym] += value
            if value > oi_max[sym]:
                oi_max[sym] = value
            net_parts.append("%s:%d" % (sym, value))

        checksum = 0
        resting = 0
        for sym in self.symbols:
            book = self.books[sym]
            checksum += book.checksum()
            resting += book.resting_count()
        if resting > self.lifecycle["max_open_orders"]:
            self.lifecycle["max_open_orders"] = resting

        self.audit.append("%d,%d,%s,%d,%s,%d,%d,%s" % (
            event["ts"], event["seq"], event["type"], event["order_id"],
            status, checksum, resting, ";".join(net_parts)))
