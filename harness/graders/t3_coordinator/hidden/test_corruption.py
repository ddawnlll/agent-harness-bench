"""Hidden corrupt-state cases: the service must stay up and lose nothing.

A previous buggy release wrote invalid rows.  Opening the coordinator and using
it must not raise, must not delete jobs/results, and must repair deterministically.
"""

from __future__ import annotations

import sqlite3
import unittest

import harness as H


class TestCorruptState(unittest.TestCase):
    def setUp(self):
        self.dir, self.db = H.new_db("t3-corrupt")
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

    def _seed_and_corrupt(self):
        done_id = H.unique_id("done")
        leased_id = H.unique_id("leased")
        bogus_id = H.unique_id("bogus")
        truncated_id = H.unique_id("truncated")
        negative_id = H.unique_id("negative")
        self.ids = {
            "done": done_id,
            "leased": leased_id,
            "bogus": bogus_id,
            "truncated": truncated_id,
            "negative": negative_id,
        }
        coord = self.coord(lease_seconds=60.0)
        for key in ("done", "leased", "bogus", "truncated", "negative"):
            job_id = self.ids[key]
            coord.add_job(job_id, {"job": job_id, "key": key})
        finished = coord.claim("worker-a")
        self.assertIsNotNone(finished)
        self.assertEqual(finished.job_id, done_id)
        coord.complete(done_id, "worker-a", {"keep": True, "job": done_id})
        held = coord.claim("worker-a")
        self.assertIsNotNone(held)
        self.assertEqual(held.job_id, leased_id)
        coord.close()

        ghost_id = "ghost-" + H.unique_id("lease")
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE jobs SET state = 'B0GUS' WHERE job_id = ?", (bogus_id,))
            conn.execute("UPDATE jobs SET payload = '{truncated' WHERE job_id = ?", (truncated_id,))
            conn.execute("UPDATE jobs SET attempts = -5 WHERE job_id = ?", (negative_id,))
            conn.execute("UPDATE jobs SET attempts = 999 WHERE job_id = ?", (done_id,))
            conn.execute(
                "INSERT OR REPLACE INTO leases (job_id, worker_id, expires_at, heartbeat_at) "
                "VALUES (?, 'w-ghost', 0, 0)",
                (ghost_id,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO leases (job_id, worker_id, expires_at, heartbeat_at) "
                "VALUES (?, 'w-stale', 9999999999, 0)",
                (done_id,),
            )
            conn.execute(
                "INSERT INTO events (ts, job_id, worker_id, type, detail) "
                "VALUES (-1, 'missing-job', NULL, 'WEIRD_EVENT', 'not-json{')"
            )
            conn.commit()
        finally:
            conn.close()
        self.ghost_id = ghost_id

    def _db_counts(self):
        conn = sqlite3.connect(self.db)
        try:
            jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            ghost = conn.execute(
                "SELECT COUNT(*) FROM leases WHERE job_id = ?", (self.ghost_id,)
            ).fetchone()[0]
            done_leases = conn.execute(
                "SELECT COUNT(*) FROM leases WHERE job_id = ?", (self.ids["done"],)
            ).fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            conn.close()
        return {"jobs": jobs, "ghost_leases": ghost, "done_leases": done_leases,
                "events": events}

    def test_F10_corrupt_state_is_tolerated_and_repaired(self):
        """F10: corrupt rows never crash the service, are repaired deterministically, nothing is lost."""
        self._seed_and_corrupt()
        before = self._db_counts()

        # 1. auto_repair=False: reading must still be safe and non-destructive.
        tolerant = self.coord(lease_seconds=60.0, auto_repair=False)
        bogus = tolerant.status(self.ids["bogus"])
        self.assertEqual(
            bogus.state, "B0GUS",
            "status() must not crash on an unknown persisted state",
        )
        self.assertEqual(bogus.payload, {"job": self.ids["bogus"], "key": "bogus"})
        self.assertTrue(tolerant.list_jobs(), "list_jobs() crashed or returned nothing")
        tolerant.audit()
        tolerant.stats()
        tolerant.close()

        # 2. Default open: automatic, deterministic repair.
        coord = self.coord(lease_seconds=60.0)
        done = coord.status(self.ids["done"])
        self.assertEqual(done.state, "DONE", "repair changed a DONE job's state")
        self.assertEqual(done.result, {"keep": True, "job": self.ids["done"]},
                         "repair lost the stored result")
        self.assertLessEqual(done.attempts, done.max_attempts,
                             "repair did not clamp a corrupted attempt counter")

        repaired = coord.status(self.ids["bogus"])
        self.assertEqual(repaired.state, "FAILED",
                         "unknown persisted state must be quarantined to FAILED")
        self.assertEqual(repaired.payload, {"job": self.ids["bogus"], "key": "bogus"},
                         "repair lost the payload of the quarantined job")

        truncated = coord.status(self.ids["truncated"])
        self.assertEqual(truncated.payload, "{truncated",
                         "corrupt payload must be returned as raw text, not raised")

        negative = coord.status(self.ids["negative"])
        self.assertEqual(negative.attempts, 0, "negative attempt counter not repaired")

        leased = coord.status(self.ids["leased"])
        self.assertEqual(leased.state, "CLAIMED", "a live lease must not be disturbed")

        counts = self._db_counts()
        self.assertEqual(counts["jobs"], before["jobs"], "repair deleted job rows")
        self.assertEqual(counts["ghost_leases"], 0, "orphan lease was not removed")
        self.assertEqual(counts["done_leases"], 0, "lease on a DONE job was not removed")
        self.assertGreaterEqual(counts["events"], before["events"],
                               "audit receipts were deleted during repair")
        self.assertTrue(any(e.type == "WEIRD_EVENT" for e in coord.audit()),
                        "unknown audit receipt was hidden or dropped")
        self.assertTrue(any(e.type == "ORPHAN_LEASE_REMOVED" for e in coord.audit()),
                        "repair did not record an ORPHAN_LEASE_REMOVED receipt")

        # 3. Usable after repair: claim skips the bogus row, keeps payloads.
        claimed = coord.claim("worker-after")
        self.assertIsNotNone(claimed, "no job declined to be claimed after repair")
        self.assertNotEqual(claimed.job_id, self.ids["bogus"],
                            "quarantined (FAILED) job was claimed")
        self.assertIn(claimed.job_id, self.ids.values())
        self.assertIsNotNone(claimed.payload)

        # 4. repair() is idempotent and reopening is stable.
        summary = coord.repair()
        self.assertEqual(int(sum(0 if v is None else v for v in summary.values())), 0,
                         f"repair is not idempotent: {summary}")
        coord.close()
        for _ in range(2):
            again = self.coord(lease_seconds=60.0)
            self.assertEqual(len(again.list_jobs()), before["jobs"])
            self.assertEqual(again.status(self.ids["done"]).result,
                             {"keep": True, "job": self.ids["done"]})
            again.close()
        self.assertEqual(self._db_counts()["jobs"], before["jobs"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
