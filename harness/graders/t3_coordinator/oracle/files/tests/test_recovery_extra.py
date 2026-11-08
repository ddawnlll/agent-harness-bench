"""Additional recovery tests added with the reference implementation.

These exist so the benchmark can distinguish candidates that add their own
regression tests from candidates that only satisfy the hidden suite.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from coordinator import Coordinator, JobState


def _coord(**kwargs):
    directory = tempfile.mkdtemp(prefix="coord-extra-")
    return Coordinator(os.path.join(directory, "jobs.db"), **kwargs)


class TestLeaseRecovery(unittest.TestCase):
    def test_expired_lease_is_reclaimed(self):
        coord = _coord(lease_seconds=0.5)
        try:
            coord.add_job("recover-me", {"n": 1})
            first = coord.claim("worker-a")
            self.assertEqual(first.attempts, 1)
            time.sleep(0.7)
            second = coord.claim("worker-b")
            self.assertIsNotNone(second)
            self.assertEqual(second.job_id, "recover-me")
            self.assertEqual(second.attempts, 2)
            done = coord.complete("recover-me", "worker-b", {"ok": True})
            self.assertEqual(done.state, JobState.DONE)
            completed = [e for e in coord.audit("recover-me") if e.type == "COMPLETED"]
            self.assertEqual(len(completed), 1)
        finally:
            coord.close()

    def test_retry_budget_is_bounded(self):
        coord = _coord(lease_seconds=30.0, max_attempts=2)
        try:
            coord.add_job("doomed", {"n": 1})
            coord.claim("worker-a")
            self.assertEqual(coord.fail("doomed", "worker-a", "boom").state, JobState.RETRY)
            coord.claim("worker-a")
            self.assertEqual(coord.fail("doomed", "worker-a", "boom").state, JobState.FAILED)
            self.assertIsNone(coord.claim("worker-b"))
            self.assertLessEqual(coord.status("doomed").attempts, 2)
        finally:
            coord.close()


if __name__ == "__main__":
    unittest.main()
