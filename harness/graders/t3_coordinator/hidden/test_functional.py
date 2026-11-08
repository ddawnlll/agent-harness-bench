"""Hidden functional cases: queue semantics, retries, idempotency, inputs.

Case ids are declared in the first line of each test docstring; grade.py maps
them to scoring weights and P0 gates.
"""

from __future__ import annotations

import json
import time
import unittest

import harness as H


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir, self.db = H.new_db("t3-func")
        self.coords = []

    def coord(self, **kwargs):
        coord = H.make_coord(self.db, **kwargs)
        self.coords.append(coord)
        return coord

    def tearDown(self):
        for coord in self.coords:
            try:
                coord.close()
            except Exception:  # noqa: BLE001
                pass
        H.cleanup(self.dir)


class TestFunctional(_Base):
    def test_F1_add_and_status_roundtrip(self):
        """F1: add_job persists payload/state; duplicate id raises DuplicateJobError."""
        dup_error = H.error_class("DuplicateJobError")
        coord = self.coord(lease_seconds=30.0)
        job_id = H.unique_id("f1")
        payload = {"job": job_id, "n": 7, "nested": {"a": [1, 2, 3]}}
        job = coord.add_job(job_id, payload)
        self.assertEqual(job.job_id, job_id)
        self.assertEqual(job.state, "PENDING")
        self.assertEqual(job.payload, payload)
        self.assertEqual(job.attempts, 0)
        self.assertEqual(job.max_attempts, 3)
        self.assertIsNone(job.worker_id)
        again = coord.status(job_id)
        self.assertEqual(again.state, "PENDING")
        self.assertEqual(again.payload, payload)
        with self.assertRaises(dup_error):
            coord.add_job(job_id, {"different": True})
        with self.assertRaises(ValueError):
            coord.add_job("", {"empty": True})
        stats = coord.stats()
        self.assertEqual(stats["jobs"], 1)
        self.assertEqual(stats["by_state"].get("PENDING"), 1)

    def test_F2_atomic_claim_visibility(self):
        """F2: claim leases one job, sets owner/attempts/lease; no second owner."""
        coord = self.coord(lease_seconds=30.0)
        job_id = H.unique_id("f2")
        coord.add_job(job_id, {"job": job_id})
        before = time.time()
        job = coord.claim("worker-a")
        self.assertIsNotNone(job, "claim returned None with a PENDING job available")
        self.assertEqual(job.job_id, job_id)
        self.assertEqual(job.state, "CLAIMED")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.worker_id, "worker-a")
        self.assertIsNotNone(job.lease_expires_at, "claim must set lease_expires_at")
        self.assertGreaterEqual(job.lease_expires_at, before + 20.0,
                                "lease_expires_at must be about now + lease_seconds")
        self.assertIsNone(coord.claim("worker-b"),
                          "an active lease must not be handed to a second worker")
        second_id = H.unique_id("f2b")
        coord.add_job(second_id, {"job": second_id})
        job2 = coord.claim("worker-b")
        self.assertIsNotNone(job2)
        self.assertEqual(job2.job_id, second_id)
        self.assertEqual(job2.worker_id, "worker-b")
        self.assertEqual(coord.status(job_id).attempts, 1)

    def test_F3_completion_persists_result_and_releases_lease(self):
        """F3: complete stores the result once, marks DONE and releases the lease."""
        coord = self.coord(lease_seconds=30.0)
        job_id = H.unique_id("f3")
        payload = {"job": job_id, "cmd": "make test"}
        coord.add_job(job_id, payload)
        claimed = coord.claim("worker-a")
        result = {"ok": True, "tests": 12, "job": job_id}
        done = coord.complete(job_id, "worker-a", result)
        self.assertEqual(done.state, "DONE")
        self.assertEqual(done.result, result)
        self.assertIsNone(done.worker_id, "lease owner must be cleared after completion")
        self.assertIsNone(done.lease_expires_at, "lease must be released after completion")
        stored = coord.status(job_id)
        self.assertEqual(stored.state, "DONE")
        self.assertEqual(stored.result, result)
        self.assertEqual(stored.payload, payload)
        stats = coord.stats()
        self.assertEqual(stats["results"], 1)
        self.assertEqual(stats["by_state"].get("DONE"), 1)
        done_jobs = coord.list_jobs("DONE")
        self.assertEqual([job.job_id for job in done_jobs], [job_id])
        self.assertEqual(H.completed_event_count(coord, job_id), 1)
        self.assertIsNone(coord.claim("worker-b"), "DONE jobs must not be claimable")
        self.assertEqual(claimed.attempts, 1)

    def test_F4_completion_is_idempotent(self):
        """F4: repeated completion (same/other worker) never double-counts or overwrites."""
        coord = self.coord(lease_seconds=30.0)
        job_id = H.unique_id("f4")
        coord.add_job(job_id, {"job": job_id})
        coord.claim("worker-a")
        first = coord.complete(job_id, "worker-a", {"v": 1, "winner": "worker-a"})
        self.assertEqual(first.state, "DONE")
        self.assertEqual(H.completed_event_count(coord, job_id), 1)

        repeat = coord.complete(job_id, "worker-a", {"v": 1, "winner": "worker-a"})
        self.assertEqual(repeat.state, "DONE")
        self.assertEqual(repeat.result, {"v": 1, "winner": "worker-a"})

        # A late duplicate acknowledgement from a different worker must be a
        # no-op success, not a lease error and not a second result.
        late = coord.complete(job_id, "worker-b", {"v": 2, "winner": "worker-b"})
        self.assertEqual(late.state, "DONE")
        self.assertEqual(late.result, {"v": 1, "winner": "worker-a"},
                         "duplicate completion must never overwrite the stored result")

        again = coord.complete(job_id, "worker-a", {"v": 3})
        self.assertEqual(again.result, {"v": 1, "winner": "worker-a"})

        self.assertEqual(coord.stats()["results"], 1)
        self.assertEqual(coord.stats()["by_state"].get("DONE"), 1)
        self.assertEqual(len(coord.list_jobs("DONE")), 1)
        self.assertEqual(H.completed_event_count(coord, job_id), 1,
                         "exactly one COMPLETED audit receipt per job")

    def test_F5_retries_are_bounded_and_terminal(self):
        """F5: attempts never exceed max_attempts; exhaustion is terminal FAILED."""
        coord = self.coord(lease_seconds=0.8, max_attempts=3)
        job_id = H.unique_id("f5")
        coord.add_job(job_id, {"job": job_id})
        observed = []
        for expected_attempt in (1, 2, 3):
            job = coord.claim("worker-a")
            self.assertIsNotNone(job, f"claim returned None at attempt {expected_attempt}")
            self.assertEqual(job.job_id, job_id)
            self.assertEqual(job.attempts, expected_attempt)
            after = coord.fail(job_id, "worker-a", f"boom-{expected_attempt}")
            observed.append(after.state)
            self.assertLessEqual(after.attempts, 3)
        self.assertEqual(observed, ["RETRY", "RETRY", "FAILED"],
                         "third failed attempt must be terminal FAILED")
        failed = coord.status(job_id)
        self.assertEqual(failed.state, "FAILED")
        self.assertEqual(failed.attempts, 3)
        self.assertIsNone(coord.claim("worker-b"), "FAILED job must never be claimable")
        self.assertIsNone(coord.claim("worker-a"))

        overridden = H.unique_id("f5b")
        coord.add_job(overridden, {"job": overridden}, max_attempts=2)
        states = []
        for _ in range(2):
            job = coord.claim("worker-a")
            self.assertIsNotNone(job)
            self.assertEqual(job.job_id, overridden)
            states.append(coord.fail(overridden, "worker-a", "nope").state)
        self.assertEqual(states, ["RETRY", "FAILED"])
        self.assertEqual(coord.status(overridden).attempts, 2)
        self.assertIsNone(coord.claim("worker-a"))

        # Crash loop: a worker that dies without calling fail() must also be
        # bounded.  max_attempts=1 -> next claim may not succeed.
        crashy = H.unique_id("f5c")
        coord.add_job(crashy, {"job": crashy}, max_attempts=1)
        claimed = coord.claim("worker-crash")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.attempts, 1)
        self.assertIsNone(coord.claim("worker-b"))
        time.sleep(1.0)  # let the abandoned lease expire
        self.assertIsNone(coord.claim("worker-b"),
                          "a crashed job with an exhausted budget must not be claimable")
        crashed = coord.status(crashy)
        self.assertEqual(crashed.state, "FAILED")
        self.assertLessEqual(crashed.attempts, 1)

    def test_F7_heartbeat_extends_the_lease(self):
        """F7: heartbeat extends lease_expires_at and keeps ownership past the original expiry."""
        coord = self.coord(lease_seconds=1.0)
        job_id = H.unique_id("f7")
        coord.add_job(job_id, {"job": job_id})
        claimed = coord.claim("worker-a")
        original_expiry = claimed.lease_expires_at
        self.assertIsNotNone(original_expiry)

        time.sleep(0.5)
        first = coord.heartbeat(job_id, "worker-a")
        self.assertIsNotNone(first.lease_expires_at)
        self.assertGreaterEqual(
            first.lease_expires_at, time.time() + 0.5,
            "heartbeat must push lease_expires_at ~lease_seconds into the future",
        )
        # Sleep past the ORIGINAL expiry; the extended lease must still hold.
        time.sleep(0.7)
        self.assertIsNone(coord.claim("worker-b"),
                          "heartbeat must keep the job leased past the original expiry")
        second = coord.heartbeat(job_id, "worker-a")
        self.assertGreaterEqual(second.lease_expires_at, time.time() + 0.4)
        done = coord.complete(job_id, "worker-a", {"ok": True})
        self.assertEqual(done.state, "DONE")
        self.assertEqual(H.completed_event_count(coord, job_id), 1)

    def test_F8_invalid_input_is_rejected_cleanly(self):
        """F8: unknown job / foreign worker / expired lease / bad transition raise documented errors."""
        unknown = H.error_class("UnknownJobError")
        lease_error = H.error_class("LeaseError")
        transition_error = H.error_class("InvalidTransitionError")
        coord = self.coord(lease_seconds=0.8)
        missing = H.unique_id("f8-missing")
        with self.assertRaises(unknown):
            coord.status(missing)
        with self.assertRaises(unknown):
            coord.complete(missing, "worker-a")
        with self.assertRaises(unknown):
            coord.heartbeat(missing, "worker-a")
        with self.assertRaises(unknown):
            coord.fail(missing, "worker-a")

        job_id = H.unique_id("f8")
        coord.add_job(job_id, {"job": job_id})
        with self.assertRaises(transition_error):
            coord.start(job_id, "worker-a")  # not claimed yet
        coord.claim("worker-a")
        with self.assertRaises(lease_error):
            coord.complete(job_id, "worker-b", {"ok": True})
        with self.assertRaises(lease_error):
            coord.heartbeat(job_id, "worker-b")
        with self.assertRaises(lease_error):
            coord.fail(job_id, "worker-b", "nope")

        # Lease expiry: the owner itself may no longer act on the job.
        other = H.unique_id("f8b")
        coord.add_job(other, {"job": other})
        held = coord.claim("worker-a")
        self.assertIsNotNone(held)
        time.sleep(1.1)
        with self.assertRaises(lease_error, msg="expired lease must not complete"):
            coord.complete(held.job_id, "worker-a", {"ok": True})
        with self.assertRaises(lease_error, msg="expired lease must not heartbeat"):
            coord.heartbeat(held.job_id, "worker-a")

        done_id = H.unique_id("f8c")
        coord.add_job(done_id, {"job": done_id})
        picked = None
        for _ in range(10):
            picked = coord.claim("worker-a")
            if picked is None or picked.job_id == done_id:
                break
        self.assertIsNotNone(picked)
        self.assertEqual(picked.job_id, done_id)
        coord.complete(done_id, "worker-a", {"ok": True})
        with self.assertRaises(transition_error):
            coord.fail(done_id, "worker-a", "too late")
        with self.assertRaises(transition_error):
            coord.start(done_id, "worker-a")

    def test_F9_audit_trail_is_ordered_and_filterable(self):
        """F9: audit receipts are ordered, per-job filterable and reconstruct history."""
        coord = self.coord(lease_seconds=30.0)
        job_id = H.unique_id("f9")
        other_id = H.unique_id("f9b")
        coord.add_job(job_id, {"job": job_id})
        coord.add_job(other_id, {"job": other_id})
        types = [event.type for event in coord.audit(job_id)]
        self.assertEqual(types[0], "ADDED")
        coord.claim("worker-a")
        coord.complete(job_id, "worker-a", {"ok": True})
        events = coord.audit(job_id)
        types = [event.type for event in events]
        self.assertEqual(types[0], "ADDED")
        self.assertIn("CLAIMED", types)
        self.assertIn("COMPLETED", types)
        self.assertLess(types.index("ADDED"), types.index("CLAIMED"))
        self.assertLess(types.index("CLAIMED"), types.index("COMPLETED"))
        self.assertTrue(all(event.job_id == job_id for event in events))
        ids = [event.event_id for event in events]
        self.assertEqual(ids, sorted(ids))
        self.assertTrue(all(isinstance(event.ts, (int, float)) for event in events))
        everything = coord.audit()
        self.assertGreaterEqual(len(everything), len(events))
        self.assertTrue(any(event.job_id == other_id for event in everything))
        self.assertGreater(len(everything), len(events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
