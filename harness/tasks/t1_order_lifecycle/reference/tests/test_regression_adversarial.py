"""Regression coverage for the fixed execution-safety defects."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from v9 import Engine, RecordingBroker  # noqa: E402


def seq_events():
    s = {"n": 0}

    def ev(**kw):
        s["n"] += 1
        kw["seq"] = s["n"]
        return kw

    return ev


class ProtectionRegression(unittest.TestCase):
    def test_pending_before_ack_then_active(self):
        ev = seq_events()
        e = Engine(RecordingBroker())
        e.process(ev(type="SUBMIT_ENTRY", order_id="E1", symbol="BTCUSD", side="BUY",
                     qty=10, price=100.0, stop_distance=5.0))
        e.process(ev(type="ENTRY_ACK", order_id="E1", native_id="N1", accepted=True))
        e.process(ev(type="FILL", order_id="E1", fill_id="F1", qty=10, price=100.0))
        prot = e.protection_for("BTCUSD")
        self.assertEqual(prot.status, "PENDING_ACK")
        self.assertFalse(e.is_protected("BTCUSD"))
        e.process(ev(type="STOP_ACK", client_stop_id=prot.client_stop_id,
                     native_id="S1", accepted=True))
        self.assertTrue(e.is_protected("BTCUSD"))

    def test_partial_fill_resize_and_duplicate_rejection(self):
        ev = seq_events()
        e = Engine(RecordingBroker())
        e.process(ev(type="SUBMIT_ENTRY", order_id="E1", symbol="BTCUSD", side="BUY",
                     qty=15, price=100.0, stop_distance=5.0))
        e.process(ev(type="ENTRY_ACK", order_id="E1", native_id="N1", accepted=True))
        e.process(ev(type="FILL", order_id="E1", fill_id="F1", qty=10, price=100.0))
        e.process(ev(type="FILL", order_id="E1", fill_id="F1", qty=10, price=100.0))
        self.assertEqual(e.position("BTCUSD").qty, 10.0)
        prot = e.protection_for("BTCUSD")
        e.process(ev(type="STOP_ACK", client_stop_id=prot.client_stop_id,
                     native_id="S1", accepted=True))
        e.process(ev(type="FILL", order_id="E1", fill_id="F2", qty=5, price=100.0))
        new = e.protection_for("BTCUSD")
        self.assertEqual(new.qty, 15.0)
        self.assertEqual(new.status, "PENDING_ACK")

    def test_rejected_stop_halts_and_blocks_entries(self):
        ev = seq_events()
        e = Engine(RecordingBroker())
        e.process(ev(type="SUBMIT_ENTRY", order_id="E1", symbol="BTCUSD", side="BUY",
                     qty=1, price=10.0))
        e.process(ev(type="ENTRY_ACK", order_id="E1", native_id="N1", accepted=True))
        e.process(ev(type="FILL", order_id="E1", fill_id="F1", qty=1, price=10.0))
        prot = e.protection_for("BTCUSD")
        e.process(ev(type="STOP_ACK", client_stop_id=prot.client_stop_id, accepted=False))
        self.assertTrue(e.halted)
        e.process(ev(type="SUBMIT_ENTRY", order_id="E2", symbol="BTCUSD", side="BUY",
                     qty=1, price=10.0))
        self.assertNotIn("E2", e.orders)


if __name__ == "__main__":
    unittest.main()
