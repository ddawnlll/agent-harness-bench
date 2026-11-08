"""Public tests for the coordinator baseline.

These tests describe the behaviour the rest of the system already relies on.
They must keep passing. Hidden tests (not shipped) additionally exercise
concurrency, crashes, restart recovery, corrupted state and false input.

Run with::

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from coordinator import Coordinator, JobState
from coordinator.errors import DuplicateJobError, LeaseError, UnknownJobError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_coordinator(**kwargs):
    tmp = tempfile.mkdtemp(prefix="coord-public-")
    return Coordinator(os.path.join(tmp, "jobs.db"), **kwargs)


class TestBasics(unittest.TestCase):
    def setUp(self):
        self.coord = make_coordinator(lease_seconds=30.0)

    def tearDown(self):
        self.coord.close()

    def test_add_and_status_roundtrip(self):
        job = self.coord.add_job("job-1", {"cmd": "make test"})
        self.assertEqual(job.job_id, "job-1")
        self.assertEqual(job.state, JobState.PENDING)
        self.assertEqual(job.payload, {"cmd": "make test"})
        self.assertEqual(self.coord.status("job-1").state, JobState.PENDING)

    def test_duplicate_add_raises(self):
        self.coord.add_job("dup", 1)
        with self.assertRaises(DuplicateJobError):
            self.coord.add_job("dup", 2)

    def test_claim_and_complete_happy_path(self):
        self.coord.add_job("job-1", {"n": 1})
        job = self.coord.claim("worker-a")
        self.assertIsNotNone(job)
        self.assertEqual(job.job_id, "job-1")
        self.assertEqual(job.state, JobState.CLAIMED)
        self.assertEqual(job.worker_id, "worker-a")
        self.assertEqual(job.attempts, 1)
        done = self.coord.complete(job.job_id, "worker-a", {"ok": True})
        self.assertEqual(done.state, JobState.DONE)
        self.assertEqual(done.result, {"ok": True})
        self.assertIsNone(self.coord.claim("worker-b"))

    def test_unknown_job_raises(self):
        with self.assertRaises(UnknownJobError):
            self.coord.status("does-not-exist")
        with self.assertRaises(UnknownJobError):
            self.coord.complete("does-not-exist", "worker-a")

    def test_wrong_worker_cannot_complete(self):
        self.coord.add_job("job-1")
        self.coord.claim("worker-a")
        with self.assertRaises(LeaseError):
            self.coord.complete("job-1", "worker-b", {"ok": True})

    def test_list_jobs_filter(self):
        for index in range(3):
            self.coord.add_job(f"job-{index}")
        job = self.coord.claim("worker-a")
        self.coord.complete(job.job_id, "worker-a", {"ok": True})
        pending = self.coord.list_jobs(JobState.PENDING)
        self.assertEqual(len(pending), 2)
        self.assertEqual(len(self.coord.list_jobs(JobState.DONE)), 1)
        self.assertEqual(len(self.coord.list_jobs()), 3)

    def test_stats_counts_results(self):
        self.coord.add_job("job-1")
        self.coord.add_job("job-2")
        job = self.coord.claim("worker-a")
        self.coord.complete(job.job_id, "worker-a", {"ok": True})
        stats = self.coord.stats()
        self.assertEqual(stats["jobs"], 2)
        self.assertEqual(stats["results"], 1)
        self.assertEqual(stats["by_state"][JobState.DONE], 1)
        self.assertEqual(stats["by_state"][JobState.PENDING], 1)

    def test_audit_records_events(self):
        self.coord.add_job("job-1")
        job = self.coord.claim("worker-a")
        self.coord.complete(job.job_id, "worker-a", {"ok": True})
        types = [event.type for event in self.coord.audit("job-1")]
        self.assertEqual(types[0], "ADDED")
        self.assertIn("CLAIMED", types)
        self.assertIn("COMPLETED", types)
        self.assertTrue(all(event.job_id == "job-1" for event in self.coord.audit("job-1")))


class TestPersistence(unittest.TestCase):
    def test_jobs_survive_reopen(self):
        tmp = tempfile.mkdtemp(prefix="coord-public-")
        db = os.path.join(tmp, "jobs.db")
        coord = Coordinator(db)
        coord.add_job("job-1", {"n": 1})
        coord.add_job("job-2", {"n": 2})
        claimed = coord.claim("worker-a")
        coord.close()

        reopened = Coordinator(db)
        try:
            self.assertEqual(reopened.status("job-1").state, JobState.CLAIMED)
            self.assertEqual(reopened.status("job-1").attempts, 1)
            self.assertEqual(reopened.status("job-1").worker_id, "worker-a")
            self.assertEqual(reopened.status("job-2").state, JobState.PENDING)
            self.assertEqual(len(reopened.list_jobs()), 2)
            self.assertTrue(reopened.audit("job-1"))
            reopened.complete(claimed.job_id, "worker-a", {"ok": True})
            self.assertEqual(reopened.status(claimed.job_id).state, JobState.DONE)
        finally:
            reopened.close()


class TestCli(unittest.TestCase):
    def test_cli_add_and_status(self):
        tmp = tempfile.mkdtemp(prefix="coord-public-")
        db = os.path.join(tmp, "jobs.db")
        env = dict(os.environ)
        env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        add = subprocess.run(
            [sys.executable, "-m", "coordinator.cli", "--db", db, "add", "cli-job",
             "--payload", json.dumps({"via": "cli"})],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertEqual(json.loads(add.stdout)["state"], "PENDING")
        status = subprocess.run(
            [sys.executable, "-m", "coordinator.cli", "--db", db, "status", "cli-job"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["payload"], {"via": "cli"})


class TestStorageShape(unittest.TestCase):
    def test_schema_is_created_and_audit_is_append_only(self):
        tmp = tempfile.mkdtemp(prefix="coord-public-")
        db = os.path.join(tmp, "jobs.db")
        coord = Coordinator(db)
        coord.add_job("job-1")
        coord.close()
        conn = sqlite3.connect(db)
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertTrue({"jobs", "leases", "events", "results", "meta"} <= tables)
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM events")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
