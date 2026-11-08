"""Independent acceptance suite for Task 1 (protective coverage).

Only the documented public API/contract in TASK.md is exercised. No internal
attribute of the candidate implementation is assumed beyond the documented
``Engine``/``Protection`` surface.
"""
import unittest


def ev(**kw):
    kw.setdefault("seq", ev.seq)
    ev.seq += 1
    return kw


ev.seq = 1


class Task1Acceptance(unittest.TestCase):
    def engine(self):
        from v9 import Engine, RecordingBroker
        return Engine(RecordingBroker())

    def submit(self, e, order_id="E1", side="BUY", qty=10, price=100.0, symbol="BTCUSD", stop=5.0):
        e.process(ev(type="SUBMIT_ENTRY", order_id=order_id, symbol=symbol, side=side,
                     qty=qty, price=price, stop_distance=stop))

    def ack(self, e, order_id="E1", native="N1", accepted=True):
        e.process(ev(type="ENTRY_ACK", order_id=order_id, native_id=native, accepted=accepted))

    def fill(self, e, fill_id="F1", qty=10, order_id="E1", price=100.0):
        e.process(ev(type="FILL", order_id=order_id, fill_id=fill_id, qty=qty, price=price))

    def stop_ack(self, e, cid, accepted=True, native="S1"):
        e.process(ev(type="STOP_ACK", client_stop_id=cid, native_id=native, accepted=accepted))

    def types(self, e):
        return [r["type"] for r in e.audit.records]

    # ---------------------------------------------------------------- safety
    def test_h01_protection_is_pending_before_venue_ack(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        prot = e.protection_for("BTCUSD")
        self.assertIsNotNone(prot)
        self.assertEqual(prot.status, "PENDING_ACK")
        self.assertFalse(e.is_protected("BTCUSD"))
        self.assertFalse(e.can_enter("BTCUSD"))

    def test_h02_accepted_stop_ack_activates_protection(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        prot = e.protection_for("BTCUSD")
        self.stop_ack(e, prot.client_stop_id)
        self.assertEqual(prot.status, "ACTIVE")
        self.assertTrue(e.is_protected("BTCUSD"))
        self.assertFalse(e.halted)

    def test_h03_rejected_stop_ack_fails_closed(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        prot = e.protection_for("BTCUSD")
        self.stop_ack(e, prot.client_stop_id, accepted=False)
        self.assertTrue(e.halted)
        self.assertFalse(e.is_protected("BTCUSD"))
        self.assertIn("RISK_UNPROTECTED", self.types(e))
        self.assertFalse(e.can_enter("BTCUSD"))
        self.submit(e, order_id="E2")
        self.assertNotIn("E2", e.orders)
        self.assertIn("RISK_BLOCK_ENTRY", self.types(e))

    def test_h07_stale_callback_cannot_downgrade_active_protection(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        prot = e.protection_for("BTCUSD")
        self.stop_ack(e, prot.client_stop_id)
        e.process(ev(type="STOP_CALLBACK", client_stop_id=prot.client_stop_id,
                     version=0, state="CANCELLED"))
        self.assertEqual(prot.status, "ACTIVE")
        self.assertTrue(e.is_protected("BTCUSD"))
        self.assertFalse(e.halted)

    def test_h08_protection_loss_callback_halts(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        prot = e.protection_for("BTCUSD")
        self.stop_ack(e, prot.client_stop_id)
        e.process(ev(type="STOP_CALLBACK", client_stop_id=prot.client_stop_id,
                     version=99, state="CANCELLED"))
        self.assertFalse(e.is_protected("BTCUSD"))
        self.assertTrue(e.halted)
        self.assertIn("RISK_PROTECTION_LOST", self.types(e))
        self.assertFalse(e.can_enter("BTCUSD"))

    def test_h09_restart_does_not_promote_pending_protection(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        prot = e.protection_for("BTCUSD")
        cid = prot.client_stop_id
        e.process(ev(type="RESTART"))
        prot2 = e.protection_for("BTCUSD")
        self.assertIsNotNone(prot2)
        self.assertEqual(prot2.client_stop_id, cid)
        self.assertEqual(prot2.status, "PENDING_ACK")
        self.assertFalse(e.is_protected("BTCUSD"))
        self.stop_ack(e, cid)
        self.assertTrue(e.is_protected("BTCUSD"))

    def test_h13_native_identity_mismatch_fails_closed(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        self.ack(e, native="GHOST")
        self.assertTrue(e.halted)
        self.assertIn("RISK_IDENTITY_MISMATCH", self.types(e))
        self.assertFalse(e.can_enter("BTCUSD"))

    # --------------------------------------------------- fill attribution
    def test_h04_partial_fills_accumulate(self):
        e = self.engine()
        self.submit(e, qty=10); self.ack(e)
        self.fill(e, fill_id="F1", qty=4)
        self.fill(e, fill_id="F2", qty=6)
        self.assertAlmostEqual(e.position("BTCUSD").qty, 10.0)
        prot = e.protection_for("BTCUSD")
        self.assertAlmostEqual(prot.qty, 10.0)

    def test_h05_duplicate_fill_applied_once(self):
        e = self.engine()
        self.submit(e, qty=10); self.ack(e)
        self.fill(e, fill_id="F1", qty=4)
        self.fill(e, fill_id="F1", qty=4)
        self.fill(e, fill_id="F2", qty=6)
        self.assertAlmostEqual(e.position("BTCUSD").qty, 10.0)
        self.assertIn("FILL_DUPLICATE_IGNORED", self.types(e))

    def test_h06_protection_resized_after_additional_fill(self):
        e = self.engine()
        self.submit(e, qty=15); self.ack(e)
        self.fill(e, fill_id="F1", qty=10)
        prot = e.protection_for("BTCUSD")
        self.stop_ack(e, prot.client_stop_id)
        self.assertTrue(e.is_protected("BTCUSD"))
        self.fill(e, fill_id="F2", qty=5)
        self.assertAlmostEqual(e.position("BTCUSD").qty, 15.0)
        new_prot = e.protection_for("BTCUSD")
        self.assertIsNotNone(new_prot)
        self.assertEqual(new_prot.status, "PENDING_ACK")
        self.assertAlmostEqual(new_prot.qty, 15.0)
        self.assertFalse(e.is_protected("BTCUSD"))
        self.stop_ack(e, new_prot.client_stop_id)
        self.assertTrue(e.is_protected("BTCUSD"))
        self.assertAlmostEqual(e.protection_for("BTCUSD").qty, 15.0)

    def test_h12_fill_before_ack_is_applied(self):
        e = self.engine()
        self.submit(e, qty=10)
        self.fill(e, fill_id="F1", qty=4)
        self.assertAlmostEqual(e.position("BTCUSD").qty, 4.0)
        self.ack(e)
        self.assertEqual(e.orders["E1"].status, "WORKING")
        self.assertFalse(e.is_protected("BTCUSD"))

    # ---------------------------------------------------------- cancel race
    def test_h10_accepted_cancel_keeps_protection_for_open_position(self):
        e = self.engine()
        self.submit(e, qty=10); self.ack(e); self.fill(e, qty=4)
        prot = e.protection_for("BTCUSD")
        self.stop_ack(e, prot.client_stop_id)
        e.process(ev(type="CANCEL_REQUEST", order_id="E1"))
        e.process(ev(type="CANCEL_RESULT", order_id="E1", accepted=True))
        self.assertAlmostEqual(e.position("BTCUSD").qty, 4.0)
        self.assertTrue(e.is_protected("BTCUSD"))

    def test_h11_rejected_cancel_keeps_protection(self):
        e = self.engine()
        self.submit(e, qty=10); self.ack(e); self.fill(e, qty=4)
        prot = e.protection_for("BTCUSD")
        self.stop_ack(e, prot.client_stop_id)
        e.process(ev(type="CANCEL_REQUEST", order_id="E1"))
        e.process(ev(type="CANCEL_RESULT", order_id="E1", accepted=False))
        self.assertTrue(e.is_protected("BTCUSD"))
        self.assertFalse(e.halted)

    # --------------------------------------------------------- entry gating
    def test_h14_entry_blocked_while_protection_unresolved(self):
        e = self.engine()
        self.submit(e); self.ack(e); self.fill(e)
        self.submit(e, order_id="E2")
        self.assertNotIn("E2", e.orders)
        self.assertIn("RISK_BLOCK_ENTRY", self.types(e))

    def test_h15_risk_reducing_exit_allowed_while_unresolved(self):
        e = self.engine()
        self.submit(e, qty=10); self.ack(e); self.fill(e, qty=10)
        self.submit(e, order_id="X1", side="SELL", qty=5)
        self.assertIn("X1", e.orders)
        self.ack(e, order_id="X1", native="N2")
        self.fill(e, fill_id="F9", order_id="X1", qty=5)
        self.assertAlmostEqual(e.position("BTCUSD").qty, 5.0)
        prot = e.protection_for("BTCUSD")
        self.assertIsNotNone(prot)
        self.assertAlmostEqual(prot.qty, 5.0)

    # --------------------------------------------------------- determinism
    def test_h16_audit_deterministic_and_consistent(self):
        events = [
            {"type": "SUBMIT_ENTRY", "seq": 1, "order_id": "E1", "symbol": "BTCUSD",
             "side": "BUY", "qty": 10, "price": 100.0, "stop_distance": 5.0},
            {"type": "ENTRY_ACK", "seq": 2, "order_id": "E1", "native_id": "N1", "accepted": True},
            {"type": "FILL", "seq": 3, "order_id": "E1", "fill_id": "F1", "qty": 4, "price": 100.0},
        ]
        from v9 import Engine, RecordingBroker
        a = Engine(RecordingBroker()).run([dict(x) for x in events])
        prot = a.protection_for("BTCUSD")
        events += [
            {"type": "STOP_ACK", "seq": 4, "client_stop_id": prot.client_stop_id,
             "native_id": "S1", "accepted": True},
            {"type": "FILL", "seq": 5, "order_id": "E1", "fill_id": "F2", "qty": 6, "price": 100.0},
        ]
        a = Engine(RecordingBroker()).run([dict(x) for x in events])
        b = Engine(RecordingBroker()).run([dict(x) for x in events])
        self.assertEqual(a.audit.digest(), b.audit.digest())
        accepted = sum(1 for r in a.audit.records
                       if r["type"] == "STOP_ACK" and r.get("result") == "ACTIVE")
        submitted = sum(1 for r in a.audit.records if r["type"] == "PROTECTION_SUBMITTED")
        self.assertGreaterEqual(accepted, 1)
        self.assertLessEqual(accepted, submitted)

if __name__ == "__main__":
    unittest.main()
