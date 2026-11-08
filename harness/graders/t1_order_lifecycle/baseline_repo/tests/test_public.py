"""Public lifecycle tests shipped with the repository.

These cover the documented happy paths only. The acceptance suite used for
grading exercises additional adversarial sequences.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v9 import Engine, RecordingBroker  # noqa: E402
from v9.engine import ACTIVE  # noqa: E402


def run(events):
    engine = Engine(RecordingBroker())
    engine.run(events)
    return engine


class HappyPathTests(unittest.TestCase):
    def test_long_entry_fill_then_protection_ack(self):
        engine = run([
            {"type": "SUBMIT_ENTRY", "seq": 1, "order_id": "E1", "symbol": "BTCUSD",
             "side": "BUY", "qty": 10, "price": 100.0, "stop_distance": 5.0},
            {"type": "ENTRY_ACK", "seq": 2, "order_id": "E1", "native_id": "N1", "accepted": True},
            {"type": "FILL", "seq": 3, "order_id": "E1", "fill_id": "F1", "qty": 10, "price": 100.0},
        ])
        self.assertEqual(engine.position("BTCUSD").qty, 10)
        prot = engine.protection_for("BTCUSD")
        self.assertIsNotNone(prot)
        self.assertEqual(prot.qty, 10)
        self.assertEqual(prot.stop_price, 95.0)
        engine.process({"type": "STOP_ACK", "seq": 4, "client_stop_id": prot.client_stop_id,
                        "native_id": "S1", "accepted": True})
        self.assertTrue(engine.is_protected("BTCUSD"))
        self.assertFalse(engine.halted)

    def test_duplicate_fill_is_not_double_counted(self):
        engine = run([
            {"type": "SUBMIT_ENTRY", "seq": 1, "order_id": "E1", "symbol": "ETHUSD",
             "side": "BUY", "qty": 4, "price": 50.0},
            {"type": "ENTRY_ACK", "seq": 2, "order_id": "E1", "native_id": "N9", "accepted": True},
            {"type": "FILL", "seq": 3, "order_id": "E1", "fill_id": "D1", "qty": 4, "price": 50.0},
            {"type": "FILL", "seq": 4, "order_id": "E1", "fill_id": "D1", "qty": 4, "price": 50.0},
        ])
        self.assertEqual(engine.position("ETHUSD").qty, 4)
        prot = engine.protection_for("ETHUSD")
        self.assertEqual(prot.qty, 4)
        engine.process({"type": "STOP_ACK", "seq": 5, "client_stop_id": prot.client_stop_id,
                        "accepted": True})
        self.assertTrue(engine.is_protected("ETHUSD"))

    def test_rejection_prevents_position(self):
        engine = run([
            {"type": "SUBMIT_ENTRY", "seq": 1, "order_id": "E1", "symbol": "SOLUSD",
             "side": "BUY", "qty": 3, "price": 20.0},
            {"type": "ENTRY_ACK", "seq": 2, "order_id": "E1", "accepted": False},
        ])
        self.assertEqual(engine.position("SOLUSD").qty, 0)
        self.assertFalse(engine.halted)

    def test_audit_digest_is_deterministic(self):
        events = [
            {"type": "SUBMIT_ENTRY", "seq": 1, "order_id": "E1", "symbol": "BTCUSD",
             "side": "BUY", "qty": 2, "price": 10.0},
            {"type": "ENTRY_ACK", "seq": 2, "order_id": "E1", "native_id": "N1", "accepted": True},
            {"type": "FILL", "seq": 3, "order_id": "E1", "fill_id": "F1", "qty": 2, "price": 10.0},
        ]
        a = run(events)
        b = run(events)
        self.assertEqual(a.audit.digest(), b.audit.digest())
        self.assertTrue(any(r["type"] == "PROTECTION_SUBMITTED" for r in a.audit.records))


if __name__ == "__main__":
    unittest.main()
