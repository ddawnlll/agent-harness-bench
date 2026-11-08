"""Hidden concurrency and crash-recovery cases.

Every case uses real OS processes and (where relevant) real ``SIGKILL``.  The
verdict is deterministic: workers are started around a shared future timestamp,
all waits are bounded and poll-based, and a correct coordinator always
satisfies the invariants regardless of exact scheduling.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import time
import unittest

import harness as H


def _kill_group(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir, self.db = H.new_db("t3-recovery")
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

    def path(self, name):
        return os.path.join(self.dir, name)


class TestConcurrency(_Base):
    STRESS_JOBS = 200
    STRESS_WORKERS = 8

    def test_C1_parallel_claiming_has_exactly_one_owner(self):
        """C1: 8 processes x 200 jobs -- each job claimed exactly once and completed once."""
        evidence = []
        for round_index in range(2):
            directory, db = H.new_db(f"t3-stress-r{round_index}")
            metrics = {
                "round": round_index,
                "passed": False,
                "claimed": 0,
                "duplicates": 0,
                "errors": 0,
                "done": 0,
                "results": 0,
                "jobs": self.STRESS_JOBS,
                "workers": self.STRESS_WORKERS,
            }
            try:
                self._stress_round(round_index, directory, db, metrics)
                metrics["passed"] = True
            except AssertionError as exc:
                metrics["failure"] = str(exc)[:800]
                raise
            finally:
                evidence.append(metrics)
                H.write_evidence("c1_stress", {"rounds": evidence})
                H.cleanup(directory)

    def _stress_round(self, round_index, directory, db, metrics):
        token = os.urandom(4).hex()
        ids = [f"stress-{token}-{index:04d}" for index in range(self.STRESS_JOBS)]
        seed = H.make_coord(db, lease_seconds=60.0)
        try:
            for index, job_id in enumerate(ids):
                seed.add_job(job_id, {"job": job_id, "n": index})
        finally:
            seed.close()

        start_at = time.time() + 0.8
        procs, logs = [], []
        for worker_index in range(self.STRESS_WORKERS):
            log_path = os.path.join(directory, f"log-{worker_index}.jsonl")
            logs.append(log_path)
            procs.append(
                H.spawn_child(
                    [
                        "--db", db, "--out", log_path,
                        "--worker", f"w{worker_index}", "--lease", "60",
                        "stress",
                        "--start-at", repr(start_at),
                        "--work", "0",
                        "--max-jobs", str(self.STRESS_JOBS * 4),
                    ]
                )
            )
        for proc in procs:
            H.reap(proc, timeout=45)

        claims = {}
        errors = []
        for log_path in logs:
            for record in H.read_jsonl(log_path):
                if record.get("op") == "claim":
                    claims.setdefault(record["job"], []).append(record)
                elif record.get("op") in ("error", "complete_error", "fatal"):
                    errors.append(record)
        duplicate = {job: recs for job, recs in claims.items() if len(recs) > 1}
        metrics.update(claimed=len(claims), duplicates=len(duplicate), errors=len(errors))
        self.assertEqual(errors, [], f"workers reported errors: {errors[:5]}")
        self.assertEqual(
            set(claims), set(ids),
            f"round {round_index}: claimed {len(claims)} of {len(ids)} jobs",
        )
        self.assertEqual(
            duplicate, {},
            f"round {round_index}: duplicate ownership detected: {list(duplicate)[:5]}",
        )
        self.assertTrue(
            all(record["payload_ok"] for recs in claims.values() for record in recs),
            "claim returned a job whose payload does not match its id",
        )

        inspector = H.make_coord(db, lease_seconds=60.0)
        try:
            stats = inspector.stats()
            metrics["done"] = stats["by_state"].get("DONE", 0)
            metrics["results"] = stats["results"]
            self.assertEqual(stats["jobs"], self.STRESS_JOBS)
            self.assertEqual(
                stats["by_state"].get("DONE"), self.STRESS_JOBS,
                f"round {round_index}: not all jobs completed: {stats['by_state']}",
            )
            self.assertEqual(stats["results"], self.STRESS_JOBS)
            for job in inspector.list_jobs():
                self.assertEqual(job.state, "DONE", f"{job.job_id} not DONE")
                self.assertEqual(job.attempts, 1, f"{job.job_id} claimed {job.attempts} times")
                self.assertEqual(
                    H.completed_event_count(inspector, job.job_id), 1,
                    f"{job.job_id} has duplicate COMPLETED receipts",
                )
                self.assertIsInstance(job.result, dict)
                self.assertEqual(job.result.get("by"), claims[job.job_id][0]["worker"],
                                 "result was not written by the claiming worker")
        finally:
            inspector.close()


class TestCrashRecovery(_Base):
    def _crash_worker(self, lease_seconds, worker="crash-w", timeout=30):
        out = self.path(f"crash-{os.urandom(3).hex()}.json")
        _, _, _ = H.run_child(
            ["--db", self.db, "--out", out, "--worker", worker,
             "--lease", repr(lease_seconds), "claim-die"],
            timeout=timeout, check=False,
        )
        return H.read_json(out), out

    def test_C2_sigkill_mid_ownership_then_lease_expiry_reclaim(self):
        """C2: SIGKILLed owner's job is not reclaimed early, is reclaimed after expiry."""
        ids = [H.unique_id("c2") for _ in range(3)]
        seed = self.coord(lease_seconds=60.0)
        for job_id in ids:
            seed.add_job(job_id, {"job": job_id})
        seed.close()

        report, _ = self._crash_worker(1.2)
        self.assertIsNotNone(report.get("job"), "crashed worker failed to claim a job")
        claimed_at = float(report["claimed_at"])
        job_id = report["job"]["job_id"]
        self.assertIn(job_id, ids, "crashed worker claimed a job that was never added")
        self.assertEqual(report["job"]["state"], "CLAIMED")
        self.assertEqual(report["job"]["attempts"], 1)

        inspector = self.coord(lease_seconds=60.0)
        stored = inspector.status(job_id)
        self.assertEqual(stored.state, "CLAIMED")
        self.assertEqual(stored.attempts, 1)
        self.assertEqual(stored.worker_id, "crash-w")

        # The lease is still valid: another worker must get a different job.
        early = inspector.claim("worker-early")
        self.assertIsNotNone(early)
        self.assertNotEqual(
            early.job_id, job_id,
            "job was reclaimed before its lease expired (premature reclaim)",
        )

        H.wait_until(
            lambda: time.time() >= claimed_at + 1.2 + 0.3,
            timeout=15, message="crashed worker's lease to expire",
        )
        reclaimed = inspector.claim("worker-after")
        self.assertIsNotNone(reclaimed, "expired job was never reclaimed (crash recovery missing)")
        self.assertEqual(reclaimed.job_id, job_id)
        self.assertEqual(reclaimed.attempts, 2, "reclaim must increment attempts")
        self.assertEqual(reclaimed.worker_id, "worker-after")
        done = inspector.complete(job_id, "worker-after", {"by": "worker-after"})
        self.assertEqual(done.state, "DONE")
        self.assertEqual(H.completed_event_count(inspector, job_id), 1)
        self.assertEqual(inspector.stats()["results"], 1)

    def test_C3_late_duplicate_completion_after_reclaim(self):
        """C3: completion after reclaim is not lost and a late duplicate cannot corrupt it."""
        job_id = H.unique_id("c3")
        seed = self.coord(lease_seconds=60.0)
        seed.add_job(job_id, {"job": job_id})
        seed.close()

        report, _ = self._crash_worker(1.2)
        self.assertIsNotNone(report.get("job"))
        self.assertEqual(report["job"]["job_id"], job_id)
        claimed_at = float(report["claimed_at"])

        inspector = self.coord(lease_seconds=60.0)
        H.wait_until(
            lambda: time.time() >= claimed_at + 1.2 + 0.3,
            timeout=15, message="lease expiry before reclaim",
        )
        reclaimed = inspector.claim("worker-b")
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed.job_id, job_id)
        stored = inspector.complete(job_id, "worker-b", {"winner": "worker-b"})
        self.assertEqual(stored.state, "DONE")
        self.assertEqual(stored.result, {"winner": "worker-b"})

        # The dead worker's late acknowledgement arrives from a new process.
        late_out = self.path("late.json")
        H.run_child(
            ["--db", self.db, "--out", late_out, "--worker", "crash-w",
             "complete", "--job", job_id, "--result", '{"winner": "crash-w"}'],
            timeout=30,
        )
        late = H.read_json(late_out)
        self.assertTrue(
            late.get("ok"),
            f"duplicate completion of a DONE job must be a no-op success, got {late}",
        )
        final = inspector.status(job_id)
        self.assertEqual(final.result, {"winner": "worker-b"},
                         "late duplicate completion overwrote the stored result")
        self.assertEqual(H.completed_event_count(inspector, job_id), 1,
                         "late duplicate completion produced a second COMPLETED receipt")
        self.assertEqual(inspector.stats()["results"], 1)
        # And again in-process from the winner.
        again = inspector.complete(job_id, "worker-b", {"winner": "worker-b"})
        self.assertEqual(again.result, {"winner": "worker-b"})
        self.assertEqual(inspector.stats()["results"], 1)
        self.assertEqual(final.attempts, 2)

    def test_C4_state_survives_process_restart(self):
        """C4: jobs/attempts/leases/results persist across graceful and abrupt restarts."""
        first_out = self.path("persist-first.json")
        H.run_child(
            ["--db", self.db, "--out", first_out, "--worker", "restart-w",
             "--lease", "60", "add-claim-close", "--count", "5", "--prefix", "persist"],
            timeout=30,
        )
        first = H.read_json(first_out)
        self.assertEqual(len(first["jobs"]), 5)
        self.assertIsNotNone(first["job"])
        claimed_id = first["job"]["job_id"]

        read_out = self.path("persist-read1.json")
        H.run_child(["--db", self.db, "--out", read_out, "read"], timeout=30)
        read1 = H.read_json(read_out)
        self.assertEqual(read1["stats"]["jobs"], 5, "jobs did not survive process exit")
        self.assertEqual(read1["stats"]["by_state"].get("CLAIMED"), 1)
        self.assertEqual(read1["stats"]["by_state"].get("PENDING"), 4)
        claimed = [job for job in read1["jobs"] if job["job_id"] == claimed_id][0]
        self.assertEqual(claimed["attempts"], 1, "attempt counter lost across restart")
        self.assertEqual(claimed["worker_id"], "restart-w", "lease owner lost across restart")
        self.assertTrue(read1["events"], "audit trail empty after restart")

        # Abrupt death of a process that had just added + claimed work.
        die_out = self.path("persist-die.json")
        H.run_child(
            ["--db", self.db, "--out", die_out, "--worker", "abrupt-w",
             "--lease", "60", "add-claim-die", "--count", "2", "--prefix", "abrupt"],
            timeout=30, check=False,
        )
        dead = H.read_json(die_out)
        self.assertEqual(len(dead["jobs"]), 2)
        self.assertIsNotNone(dead["job"])

        read_out2 = self.path("persist-read2.json")
        H.run_child(["--db", self.db, "--out", read_out2, "read"], timeout=30)
        read2 = H.read_json(read_out2)
        self.assertEqual(read2["stats"]["jobs"], 7,
                         "work added before SIGKILL was lost (non-durable storage)")
        self.assertEqual(read2["stats"]["by_state"].get("CLAIMED"), 2)

        # The database file itself must contain the work.
        self.assertTrue(os.path.exists(self.db), "database file missing")
        self.assertGreater(os.path.getsize(self.db), 0, "database file is empty")
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            done_rows = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE state = 'DONE'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(rows, 7, "persistent jobs table does not contain all jobs")
        self.assertEqual(done_rows, 0)

        inspector = self.coord(lease_seconds=60.0)
        self.assertEqual(len(inspector.list_jobs()), 7)
        self.assertEqual(inspector.status(claimed_id).worker_id, "restart-w")
        self.assertEqual(inspector.status(claimed_id).state, "CLAIMED")

    def test_C5_kill_worker_mid_flight_survivors_recover(self):
        """C5: SIGKILL a worker that owns a job; survivors reclaim it and finish everything."""
        total = 200
        token = os.urandom(4).hex()
        ids = [f"mid-{token}-{index:04d}" for index in range(total)]
        seed = self.coord(lease_seconds=60.0)
        for index, job_id in enumerate(ids):
            seed.add_job(job_id, {"job": job_id, "n": index})
        seed.close()

        # Victim: a real process that claims a job and then holds it.
        victim_out = self.path("victim.json")
        victim = H.spawn_child(
            [
                "--db", self.db, "--out", victim_out,
                "--worker", "victim-w", "--lease", "1.0",
                "claim-hold", "--seconds", "8",
            ]
        )
        info = H.read_json(victim_out, timeout=25)
        self.assertIsNotNone(info.get("job"), "victim failed to claim a job")
        victim_job = info["job"]["job_id"]
        self.assertEqual(info["job"]["state"], "CLAIMED")

        # Survivors start after the victim owns its job, and have enough work
        # that they are still claiming when the victim's lease expires.
        start_at = time.time() + 0.6
        procs, logs = [], []
        for worker_index in range(2):
            log_path = self.path(f"flight-{worker_index}.jsonl")
            logs.append(log_path)
            procs.append(
                H.spawn_child(
                    [
                        "--db", self.db, "--out", log_path,
                        "--worker", f"flight-w{worker_index}", "--lease", "0.8",
                        "stress",
                        "--start-at", repr(start_at),
                        "--work", "0.015",
                        "--max-jobs", str(total),
                    ]
                )
            )
        H.wait_until(lambda: time.time() >= start_at - 0.2, timeout=15,
                     message="survivors to start")
        _kill_group(victim)
        victim_rc = victim.wait(timeout=15)
        self.assertEqual(victim_rc, -signal.SIGKILL, "victim worker was not SIGKILLed")

        for proc in procs:
            H.reap(proc, timeout=90)

        inspector = self.coord(lease_seconds=60.0)
        H.wait_until(
            lambda: inspector.stats()["by_state"].get("DONE") == total,
            timeout=40, message="survivors to reclaim and complete all jobs",
        )
        stats = inspector.stats()
        self.assertEqual(stats["results"], total,
                         "results were lost when a worker was killed mid-ownership")
        jobs = {job.job_id: job for job in inspector.list_jobs()}
        self.assertEqual(set(jobs), set(ids))
        for job_id, job in jobs.items():
            self.assertEqual(job.state, "DONE", f"{job_id} stuck in {job.state}")
            self.assertGreaterEqual(job.attempts, 1)
            self.assertLessEqual(job.attempts, 3, f"{job_id} attempts exceed max_attempts")
            self.assertEqual(H.completed_event_count(inspector, job_id), 1,
                             f"{job_id} has duplicate COMPLETED receipts")
        reclaimed = jobs[victim_job]
        self.assertEqual(reclaimed.attempts, 2,
                         "the killed worker's job must be reclaimed exactly once")
        self.assertIsNotNone(reclaimed.result, "the killed worker's job lost its completion")

        # Survivors must never have owned the same job at the same time, and
        # must not have reported errors.
        survivor_claims = {}
        errors = []
        for log_path in logs:
            for record in H.read_jsonl(log_path):
                if record.get("op") == "claim":
                    per_worker = survivor_claims.setdefault(record["worker"], {})
                    self.assertNotIn(
                        record["job"], per_worker,
                        f"{record['worker']} claimed {record['job']} twice: "
                        f"duplicate ownership",
                    )
                    per_worker[record["job"]] = record["attempts"]
                elif record.get("op") in ("error", "complete_error", "fatal"):
                    errors.append(record)
        self.assertEqual(errors, [], f"surviving workers reported errors: {errors[:5]}")
        for worker, per_worker in survivor_claims.items():
            for job_id, attempt in per_worker.items():
                self.assertGreaterEqual(attempt, 1)
                self.assertLessEqual(
                    attempt, jobs[job_id].attempts,
                    f"{worker} recorded attempt {attempt} for {job_id} but the "
                    f"store says {jobs[job_id].attempts}",
                )

    def test_C6_expired_lease_is_reclaimed_and_counted(self):
        """C6: an expired lease makes the job claimable again with an incremented attempt."""
        coord = self.coord(lease_seconds=1.0)
        job_id = H.unique_id("c6")
        coord.add_job(job_id, {"job": job_id})
        first = coord.claim("worker-a")
        self.assertIsNotNone(first)
        self.assertEqual(first.attempts, 1)
        self.assertIsNone(coord.claim("worker-b"),
                          "active lease must not be reclaimable")
        time.sleep(1.25)
        again = coord.claim("worker-b")
        self.assertIsNotNone(again, "expired lease was never reclaimed")
        self.assertEqual(again.job_id, job_id)
        self.assertEqual(again.attempts, 2)
        self.assertEqual(again.worker_id, "worker-b")
        done = coord.complete(job_id, "worker-b", {"ok": True})
        self.assertEqual(done.state, "DONE")
        self.assertEqual(H.completed_event_count(coord, job_id), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
