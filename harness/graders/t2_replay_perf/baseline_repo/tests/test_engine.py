"""Behavioural tests for the replay engine.

These cover the economic contract: price-time priority, maker-price
execution, partial fills, fees, position/PnL accounting, order lifecycle
counters and the canonical result document.
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from replay.engine import FEE_BPS, Engine  # noqa: E402
from replay.events import load_events  # noqa: E402

DATA = os.path.join(ROOT, "data")
GOLDEN = os.path.join(ROOT, "tests", "golden")

# Digest of data/medium.jsonl after a full replay.  Pinned so that any change
# in matching, tie-breaking or accounting shows up as a test failure.
MEDIUM_DIGEST = "cc8280e53df17747"


def submit(ts, seq, order_id, account, symbol, side, price, qty):
    return {"ts": ts, "seq": seq, "type": "submit", "order_id": order_id,
            "account": account, "symbol": symbol, "side": side,
            "price": price, "qty": qty}


def cancel(ts, seq, order_id):
    return {"ts": ts, "seq": seq, "type": "cancel", "order_id": order_id}


def run(events, symbols=("AAA",)):
    engine = Engine(symbols)
    engine.process(events)
    return engine.result()


class MatchingTests(unittest.TestCase):
    def test_resting_order_is_not_a_trade(self):
        result = run([submit(1, 1, 1, "a", "AAA", "buy", 100, 10)])
        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(result["symbols"]["AAA"]["best_bid"], 100)
        self.assertIsNone(result["symbols"]["AAA"]["best_ask"])
        self.assertEqual(result["lifecycle"]["submitted"], 1)
        self.assertEqual(result["lifecycle"]["open_at_end"], 1)

    def test_crossing_trade_executes_at_maker_price(self):
        result = run([
            submit(1, 1, 1, "maker", "AAA", "sell", 100, 10),
            submit(2, 2, 2, "taker", "AAA", "buy", 105, 10),
        ])
        self.assertEqual(result["trade_count"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["price"], 100)
        self.assertEqual(trade["qty"], 10)
        self.assertEqual(trade["maker"], 1)
        self.assertEqual(trade["taker"], 2)
        self.assertEqual(trade["maker_account"], "maker")
        self.assertEqual(trade["taker_account"], "taker")

    def test_price_priority(self):
        result = run([
            submit(1, 1, 1, "a", "AAA", "sell", 105, 10),
            submit(1, 2, 2, "b", "AAA", "sell", 100, 10),
            submit(2, 3, 3, "t", "AAA", "buy", 105, 10),
        ])
        self.assertEqual(result["trade_count"], 1)
        self.assertEqual(result["trades"][0]["maker"], 2)
        self.assertEqual(result["trades"][0]["price"], 100)

    def test_time_priority(self):
        result = run([
            submit(1, 1, 1, "a", "AAA", "sell", 100, 10),
            submit(1, 2, 2, "b", "AAA", "sell", 100, 10),
            submit(2, 3, 3, "t", "AAA", "buy", 100, 10),
        ])
        self.assertEqual(result["trades"][0]["maker"], 1)
        self.assertEqual(result["symbols"]["AAA"]["open_orders"], 1)
        self.assertEqual(result["symbols"]["AAA"]["open_qty"], 10)

    def test_partial_fill_rests_remainder(self):
        result = run([
            submit(1, 1, 1, "maker", "AAA", "sell", 100, 10),
            submit(2, 2, 2, "taker", "AAA", "buy", 100, 25),
        ])
        self.assertEqual(result["trade_count"], 1)
        self.assertEqual(result["trades"][0]["qty"], 10)
        self.assertEqual(result["symbols"]["AAA"]["best_bid"], 100)
        self.assertEqual(result["symbols"]["AAA"]["open_qty"], 15)
        self.assertEqual(result["lifecycle"]["open_at_end"], 1)

    def test_one_taker_sweeps_multiple_makers(self):
        result = run([
            submit(1, 1, 1, "a", "AAA", "sell", 100, 10),
            submit(1, 2, 2, "b", "AAA", "sell", 101, 10),
            submit(1, 3, 3, "c", "AAA", "sell", 102, 10),
            submit(2, 4, 4, "t", "AAA", "buy", 102, 25),
        ])
        self.assertEqual(result["trade_count"], 3)
        self.assertEqual([t["price"] for t in result["trades"]],
                         [100, 101, 102])
        self.assertEqual([t["qty"] for t in result["trades"]], [10, 10, 5])
        self.assertEqual(result["symbols"]["AAA"]["open_qty"], 5)

    def test_non_crossing_limit_rests(self):
        result = run([
            submit(1, 1, 1, "a", "AAA", "sell", 110, 10),
            submit(2, 2, 2, "t", "AAA", "buy", 105, 10),
        ])
        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(result["symbols"]["AAA"]["best_bid"], 105)
        self.assertEqual(result["symbols"]["AAA"]["best_ask"], 110)

    def test_duplicate_order_id_is_rejected(self):
        result = run([
            submit(1, 1, 1, "a", "AAA", "buy", 100, 10),
            submit(2, 2, 1, "b", "AAA", "buy", 100, 10),
        ])
        self.assertEqual(result["lifecycle"]["submitted"], 1)
        self.assertEqual(result["lifecycle"]["rejected"], 1)
        self.assertEqual(result["symbols"]["AAA"]["open_qty"], 10)

    def test_unknown_symbol_is_rejected(self):
        result = run([submit(1, 1, 1, "a", "ZZZ", "buy", 100, 10)])
        self.assertEqual(result["lifecycle"]["rejected"], 1)
        self.assertEqual(result["lifecycle"]["submitted"], 0)

    def test_cancel_removes_liquidity(self):
        result = run([
            submit(1, 1, 1, "a", "AAA", "buy", 100, 10),
            cancel(2, 2, 1),
        ])
        self.assertEqual(result["lifecycle"]["cancelled"], 1)
        self.assertEqual(result["symbols"]["AAA"]["open_orders"], 0)
        self.assertIsNone(result["symbols"]["AAA"]["best_bid"])

    def test_cancel_of_filled_order_is_rejected(self):
        result = run([
            submit(1, 1, 1, "a", "AAA", "sell", 100, 10),
            submit(2, 2, 2, "b", "AAA", "buy", 100, 10),
            cancel(3, 3, 1),
        ])
        self.assertEqual(result["lifecycle"]["cancelled"], 0)
        self.assertEqual(result["lifecycle"]["rejected"], 1)

    def test_cancel_of_unknown_order_is_rejected(self):
        result = run([cancel(1, 1, 99)])
        self.assertEqual(result["lifecycle"]["rejected"], 1)
        self.assertEqual(result["lifecycle"]["cancelled"], 0)

    def test_self_trade_is_allowed_and_nets_flat(self):
        result = run([
            submit(1, 1, 1, "same", "AAA", "sell", 100, 10),
            submit(2, 2, 2, "same", "AAA", "buy", 100, 10),
        ])
        self.assertEqual(result["trade_count"], 1)
        account = result["accounts"]["same"]
        self.assertEqual(account["positions"], {})
        self.assertEqual(account["traded_qty"], 20)


class AccountingTests(unittest.TestCase):
    def test_fees_and_cash_flow(self):
        price, qty = 100000, 10
        notional = price * qty
        fee = notional * FEE_BPS // 10000
        result = run([
            submit(1, 1, 1, "maker", "AAA", "sell", price, qty),
            submit(2, 2, 2, "taker", "AAA", "buy", price, qty),
        ])
        self.assertEqual(result["accounts"]["taker"]["cash"],
                         -notional - fee)
        self.assertEqual(result["accounts"]["maker"]["cash"],
                         notional - fee)
        self.assertEqual(result["accounts"]["taker"]["fees"], fee)
        self.assertEqual(result["accounts"]["maker"]["fees"], fee)
        self.assertEqual(result["accounts"]["taker"]["positions"]["AAA"], qty)
        self.assertEqual(result["accounts"]["maker"]["positions"]["AAA"], -qty)

    def test_pnl_marks_open_positions(self):
        # Buy 10 at 100, then a second trade sets the mark at 110.
        result = run([
            submit(1, 1, 1, "seller1", "AAA", "sell", 100, 10),
            submit(2, 2, 2, "buyer", "AAA", "buy", 100, 10),
            submit(3, 3, 3, "seller2", "AAA", "sell", 110, 10),
            submit(4, 4, 4, "other", "AAA", "buy", 110, 10),
        ])
        mark = result["symbols"]["AAA"]["mark"]
        account = result["accounts"]["buyer"]
        self.assertEqual(account["pnl"],
                         account["cash"] + account["positions"]["AAA"] * mark)

    def test_cash_conservation(self):
        # Traded notional nets to zero across accounts; only fees leave the
        # system, and every fill charges the fee to both counterparties.
        events = load_events(os.path.join(DATA, "small.jsonl"))
        result = run(events, symbols=("AAA", "BBB", "CCC"))
        total_cash = sum(account["cash"]
                         for account in result["accounts"].values())
        total_fees = sum(account["fees"]
                         for account in result["accounts"].values())
        self.assertEqual(total_cash, -total_fees)
        expected_fees = sum(trade["price"] * trade["qty"] * FEE_BPS // 10000
                            for trade in result["trades"]) * 2
        self.assertEqual(total_fees, expected_fees)

    def test_positions_net_to_flat(self):
        events = load_events(os.path.join(DATA, "adversarial_small.jsonl"))
        result = run(events, symbols=("AAA", "BBB", "CCC"))
        for symbol in ("AAA", "BBB", "CCC"):
            net = sum(account["positions"].get(symbol, 0)
                      for account in result["accounts"].values())
            self.assertEqual(net, 0)

    def test_traded_quantity_is_double_counted_across_accounts(self):
        events = load_events(os.path.join(DATA, "medium.jsonl"))
        result = run(events, symbols=("AAA", "BBB", "CCC"))
        per_account = sum(account["traded_qty"]
                          for account in result["accounts"].values())
        self.assertEqual(per_account, 2 * result["lifecycle"]["traded_qty"])
        self.assertEqual(
            result["lifecycle"]["traded_qty"],
            sum(trade["qty"] for trade in result["trades"]))


class ResultDocumentTests(unittest.TestCase):
    def test_small_fixture_matches_golden_document(self):
        events = load_events(os.path.join(DATA, "small.jsonl"))
        result = run(events, symbols=("AAA", "BBB", "CCC"))
        text = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        path = os.path.join(GOLDEN, "small.json")
        with open(path, "r", encoding="utf-8") as handle:
            expected = handle.read()
        self.assertEqual(text, expected)

    def test_medium_fixture_digest(self):
        events = load_events(os.path.join(DATA, "medium.jsonl"))
        result = run(events, symbols=("AAA", "BBB", "CCC"))
        self.assertEqual(result["audit"]["digest"], MEDIUM_DIGEST)
        self.assertEqual(result["event_count"], len(events))
        self.assertEqual(result["audit"]["entries"], len(events))

    def test_result_is_deterministic(self):
        events = load_events(os.path.join(DATA, "adversarial_small.jsonl"))
        first = run(events, symbols=("AAA", "BBB", "CCC"))
        second = run(list(events), symbols=("AAA", "BBB", "CCC"))
        self.assertEqual(first, second)

    def test_audit_digest_shape(self):
        result = run([cancel(1, 1, 1)])
        digest = result["audit"]["digest"]
        self.assertEqual(len(digest), 16)
        int(digest, 16)

    def test_schema_and_keys(self):
        result = run([submit(1, 1, 1, "a", "AAA", "buy", 100, 10)])
        self.assertEqual(result["schema"], "replay.v1")
        for key in ("event_count", "trade_count", "trades", "accounts",
                    "symbols", "lifecycle", "audit"):
            self.assertIn(key, result)
        self.assertEqual(result["symbols"]["AAA"]["mark"], 100)


if __name__ == "__main__":
    unittest.main()
