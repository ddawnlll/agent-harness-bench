"""Crash-safe, durable job coordinator.

Design summary
--------------
* Every mutation runs inside ``BEGIN IMMEDIATE`` so SQLite serialises writers.
  Claiming is therefore atomic: two workers can never lease the same job.
* Ownership is represented by a row in ``leases`` with an absolute
  ``expires_at``. Heartbeats extend it. A lease that expired makes the job
  claimable again (crash recovery / fencing).
* ``attempts`` is incremented on every claim. A claim that would exceed
  ``max_attempts`` terminally fails the job instead, so retries are bounded
  even for workers that crash in a loop.
* Completion is idempotent: an already ``DONE`` job is a no-op returning the
  stored result, and at most one ``COMPLETED`` audit event exists per job.
* Every transition is appended to ``events`` (append-only, enforced by
  triggers) together with the decision that was taken.
* ``repair()`` deterministically sanitises corrupt persisted state
  (orphan leases, unknown job states, out-of-range attempt counters) without
  deleting jobs or results.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterable, List, Optional

from .errors import (
    CoordinatorError,
    DuplicateJobError,
    InvalidTransitionError,
    LeaseError,
    StorageError,
    UnknownJobError,
)
from .models import (
    ACTIVE_STATES,
    ALL_STATES,
    CLAIMABLE_STATES,
    TERMINAL_STATES,
    Event,
    Job,
    JobState,
    decode_json,
    encode_json,
)
from .schema import init_schema

#: Event types written to the audit trail. Unknown types found in the store
#: (corruption / forward compatibility) are surfaced verbatim by ``audit()``.
EVENT_ADDED = "ADDED"
EVENT_CLAIMED = "CLAIMED"
EVENT_STARTED = "STARTED"
EVENT_HEARTBEAT = "HEARTBEAT"
EVENT_COMPLETED = "COMPLETED"
EVENT_COMPLETION_REPEATED = "COMPLETION_REPEATED"
EVENT_RETRY_SCHEDULED = "RETRY_SCHEDULED"
EVENT_RETRIES_EXHAUSTED = "RETRIES_EXHAUSTED"
EVENT_FAILED = "FAILED"
EVENT_REPAIRED = "REPAIRED"
EVENT_ORPHAN_LEASE_REMOVED = "ORPHAN_LEASE_REMOVED"
EVENT_REJECTED = "REJECTED"

KNOWN_EVENT_TYPES = frozenset(
    {
        EVENT_ADDED,
        EVENT_CLAIMED,
        EVENT_STARTED,
        EVENT_HEARTBEAT,
        EVENT_COMPLETED,
        EVENT_COMPLETION_REPEATED,
        EVENT_RETRY_SCHEDULED,
        EVENT_RETRIES_EXHAUSTED,
        EVENT_FAILED,
        EVENT_REPAIRED,
        EVENT_ORPHAN_LEASE_REMOVED,
        EVENT_REJECTED,
    }
)


def _default_clock() -> float:
    return time.time()


class Coordinator:
    """Durable job queue with atomic claiming, leases and bounded retries.

    Parameters
    ----------
    db_path:
        Path of the SQLite database file. ``":memory:"`` is **not** supported
        for durable operation and is rejected.
    max_attempts:
        Default retry budget per job (number of successful claims allowed).
    lease_seconds:
        Length of a worker lease. Must be > 0.
    busy_timeout:
        Seconds to wait for the SQLite write lock before failing.
    auto_repair:
        Run :meth:`repair` on open so a process restarted after corruption
        comes up in a sane state instead of crashing.
    """

    def __init__(
        self,
        db_path: str,
        *,
        max_attempts: int = 3,
        lease_seconds: float = 30.0,
        busy_timeout: float = 10.0,
        auto_repair: bool = True,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if not db_path:
            raise ValueError("db_path is required")
        if str(db_path) == ":memory:":
            raise ValueError("in-memory databases are not durable; use a file path")
        if int(max_attempts) < 1:
            raise ValueError("max_attempts must be >= 1")
        if float(lease_seconds) <= 0:
            raise ValueError("lease_seconds must be > 0")

        self.db_path = str(db_path)
        self.max_attempts = int(max_attempts)
        self.lease_seconds = float(lease_seconds)
        self.busy_timeout = float(busy_timeout)
        self._clock = clock or _default_clock
        self._lock = threading.RLock()
        self._closed = False

        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)

        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=self.busy_timeout,
                isolation_level=None,  # explicit transactions only
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = %d" % int(self.busy_timeout * 1000))
            # WAL keeps readers non-blocking and survives process crashes.
            self._conn.execute("PRAGMA journal_mode = WAL")
            # NORMAL is durable across process crashes (the failure mode we
            # care about) and much faster than FULL for high claim rates.
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute("PRAGMA foreign_keys = OFF")
            init_schema(self._conn, self._clock())
        except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
            raise StorageError(f"cannot open coordinator database {self.db_path}: {exc}") from exc

        if auto_repair:
            self.repair()

    # ------------------------------------------------------------------ utils
    def __enter__(self) -> "Coordinator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Flush and close the connection. Idempotent."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                pass

    def _now(self) -> float:
        return float(self._clock())

    @contextmanager
    def _write_tx(self):
        """Serialised, atomic write transaction."""

        with self._lock:
            if self._closed:
                raise StorageError("coordinator is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - defensive
                    pass
                raise
            else:
                self._conn.execute("COMMIT")

    def _read(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            if self._closed:
                raise StorageError("coordinator is closed")
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def _event(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        job_id: Optional[str],
        worker_id: Optional[str],
        detail: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> None:
        conn.execute(
            "INSERT INTO events (ts, job_id, worker_id, type, detail) VALUES (?, ?, ?, ?, ?)",
            (self._now() if now is None else now, job_id, worker_id, event_type, encode_json(detail)),
        )

    def _job_from_row(self, row: sqlite3.Row) -> Job:
        keys = row.keys()
        max_attempts = row["max_attempts"]
        return Job(
            job_id=row["job_id"],
            state=row["state"] if row["state"] is not None else "",
            payload=decode_json(row["payload"]),
            attempts=int(row["attempts"] or 0),
            max_attempts=int(max_attempts) if max_attempts is not None else self.max_attempts,
            worker_id=row["lease_worker"] if "lease_worker" in keys else None,
            lease_expires_at=row["lease_expires"] if "lease_expires" in keys else None,
            result=decode_json(row["result"]),
            error=row["error"],
            created_at=float(row["created_at"] or 0.0),
            updated_at=float(row["updated_at"] or 0.0),
        )

    _JOB_SELECT = (
        "SELECT j.*, l.worker_id AS lease_worker, l.expires_at AS lease_expires "
        "FROM jobs j LEFT JOIN leases l ON l.job_id = j.job_id "
    )

    def _get_job_row(self, conn: sqlite3.Connection, job_id: str) -> Optional[sqlite3.Row]:
        row = conn.execute(self._JOB_SELECT + "WHERE j.job_id = ?", (job_id,)).fetchone()
        return row

    def _require_job(self, conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = self._get_job_row(conn, job_id)
        if row is None:
            raise UnknownJobError(job_id)
        return row

    # ------------------------------------------------------------------- API
    def init_schema(self) -> None:
        """Create/migrate the schema. Safe to call repeatedly and concurrently."""

        with self._lock:
            init_schema(self._conn, self._now())

    def add_job(
        self,
        job_id: str,
        payload: Any = None,
        *,
        max_attempts: Optional[int] = None,
    ) -> Job:
        """Persist a new PENDING job.

        Raises :class:`DuplicateJobError` if ``job_id`` already exists; adding a
        job is never silently deduplicated because callers rely on the error to
        detect replay.
        """

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        budget = self.max_attempts if max_attempts is None else int(max_attempts)
        if budget < 1:
            raise ValueError("max_attempts must be >= 1")
        now = self._now()
        with self._write_tx() as conn:
            try:
                conn.execute(
                    "INSERT INTO jobs (job_id, payload, state, attempts, max_attempts, "
                    "created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?, ?)",
                    (job_id, encode_json(payload), JobState.PENDING, budget, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateJobError(job_id) from exc
            self._event(conn, EVENT_ADDED, job_id, None, {"max_attempts": budget}, now)
        return self.status(job_id)

    def claim(self, worker_id: str) -> Optional[Job]:
        """Atomically lease one claimable job to ``worker_id``.

        Returns ``None`` when nothing is claimable. Claimable means: state is
        PENDING/RETRY, or the job is active but its lease has expired (crash
        recovery). Claims increment ``attempts``; a claim that would exceed the
        job's retry budget terminally fails the job instead of returning it.
        """

        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string")
        now = self._now()
        with self._write_tx() as conn:
            while True:
                row = conn.execute(
                    self._JOB_SELECT
                    + "WHERE j.state IN (?, ?, ?, ?) "
                    "AND (j.state IN (?, ?) OR l.job_id IS NULL OR l.expires_at <= ?) "
                    "ORDER BY j.seq ASC LIMIT 1",
                    (
                        JobState.PENDING,
                        JobState.RETRY,
                        JobState.CLAIMED,
                        JobState.RUNNING,
                        JobState.PENDING,
                        JobState.RETRY,
                        now,
                    ),
                ).fetchone()
                if row is None:
                    return None

                job_id = row["job_id"]
                budget = int(row["max_attempts"] or self.max_attempts)
                attempts = int(row["attempts"] or 0) + 1

                if attempts > budget:
                    # Retry budget exhausted: terminal, never claimable again.
                    conn.execute(
                        "UPDATE jobs SET state = ?, attempts = ?, error = COALESCE(error, ?), "
                        "updated_at = ? WHERE job_id = ?",
                        (
                            JobState.FAILED,
                            budget,
                            "retry budget exhausted",
                            now,
                            job_id,
                        ),
                    )
                    conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
                    self._event(
                        conn,
                        EVENT_RETRIES_EXHAUSTED,
                        job_id,
                        worker_id,
                        {"attempts": budget, "max_attempts": budget},
                        now,
                    )
                    continue

                conn.execute(
                    "UPDATE jobs SET state = ?, attempts = ?, updated_at = ? WHERE job_id = ?",
                    (JobState.CLAIMED, attempts, now, job_id),
                )
                conn.execute(
                    "INSERT INTO leases (job_id, worker_id, expires_at, heartbeat_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(job_id) DO UPDATE SET worker_id = excluded.worker_id, "
                    "expires_at = excluded.expires_at, heartbeat_at = excluded.heartbeat_at",
                    (job_id, worker_id, now + self.lease_seconds, now),
                )
                self._event(
                    conn,
                    EVENT_CLAIMED,
                    job_id,
                    worker_id,
                    {
                        "attempt": attempts,
                        "max_attempts": budget,
                        "reclaimed": row["state"] in ACTIVE_STATES,
                        "lease_expires_at": now + self.lease_seconds,
                    },
                    now,
                )
                return self.status(job_id)

    def _check_lease(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        worker_id: str,
        now: float,
    ) -> None:
        if row["state"] in TERMINAL_STATES:
            raise InvalidTransitionError(f"job {row['job_id']} is already {row['state']}")
        if row["state"] not in ACTIVE_STATES:
            raise InvalidTransitionError(
                f"job {row['job_id']} is not claimed (state={row['state']})"
            )
        lease_worker = row["lease_worker"]
        if lease_worker is None:
            raise LeaseError(f"job {row['job_id']} has no lease")
        if lease_worker != worker_id:
            raise LeaseError(
                f"job {row['job_id']} is leased by {lease_worker}, not {worker_id}"
            )
        if row["lease_expires"] is None or float(row["lease_expires"]) <= now:
            raise LeaseError(f"lease for job {row['job_id']} has expired")

    def start(self, job_id: str, worker_id: str) -> Job:
        """Transition CLAIMED -> RUNNING (optional step, lease owner only)."""

        now = self._now()
        with self._write_tx() as conn:
            row = self._require_job(conn, job_id)
            self._check_lease(conn, row, worker_id, now)
            if row["state"] != JobState.CLAIMED:
                raise InvalidTransitionError(
                    f"job {job_id} cannot start from state {row['state']}"
                )
            conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (JobState.RUNNING, now, job_id),
            )
            self._event(conn, EVENT_STARTED, job_id, worker_id, None, now)
        return self.status(job_id)

    def heartbeat(self, job_id: str, worker_id: str) -> Job:
        """Extend the lease of a job owned by ``worker_id``.

        An expired lease cannot be revived: the job may already have been
        reclaimed by another worker, so the heartbeat is rejected (fencing).
        """

        now = self._now()
        with self._write_tx() as conn:
            row = self._require_job(conn, job_id)
            self._check_lease(conn, row, worker_id, now)
            expires = now + self.lease_seconds
            conn.execute(
                "UPDATE leases SET expires_at = ?, heartbeat_at = ? WHERE job_id = ?",
                (expires, now, job_id),
            )
            conn.execute("UPDATE jobs SET updated_at = ? WHERE job_id = ?", (now, job_id))
            self._event(
                conn,
                EVENT_HEARTBEAT,
                job_id,
                worker_id,
                {"lease_expires_at": expires},
                now,
            )
        return self.status(job_id)

    def complete(self, job_id: str, worker_id: str, result: Any = None) -> Job:
        """Mark a leased job DONE and persist ``result``.

        Idempotent: completing a job that is already DONE is a no-op that
        returns the stored job (and records ``COMPLETION_REPEATED``). The first
        successful completion wins; a late/duplicate message can never
        overwrite the stored result or add a second result row.
        """

        now = self._now()
        with self._write_tx() as conn:
            row = self._require_job(conn, job_id)
            if row["state"] == JobState.DONE:
                self._event(
                    conn,
                    EVENT_COMPLETION_REPEATED,
                    job_id,
                    worker_id,
                    {"stored_worker": row["lease_worker"], "accepted": False},
                    now,
                )
                return self._job_from_row(self._get_job_row(conn, job_id))
            self._check_lease(conn, row, worker_id, now)
            conn.execute(
                "UPDATE jobs SET state = ?, result = ?, error = NULL, updated_at = ? "
                "WHERE job_id = ?",
                (JobState.DONE, encode_json(result), now, job_id),
            )
            conn.execute(
                "INSERT INTO results (job_id, worker_id, result, completed_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO NOTHING",
                (job_id, worker_id, encode_json(result), now),
            )
            conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
            self._event(
                conn,
                EVENT_COMPLETED,
                job_id,
                worker_id,
                {"attempts": int(row["attempts"] or 0), "result": result},
                now,
            )
        return self.status(job_id)

    def fail(self, job_id: str, worker_id: str, error: str = "failed") -> Job:
        """Report a failed attempt.

        Moves to RETRY (claimable immediately) while attempts remain, otherwise
        to terminal FAILED. Bounded: ``attempts`` can never exceed
        ``max_attempts``, and a FAILED job can never be claimed again.
        """

        now = self._now()
        with self._write_tx() as conn:
            row = self._require_job(conn, job_id)
            if row["state"] == JobState.FAILED:
                self._event(
                    conn,
                    EVENT_COMPLETION_REPEATED,
                    job_id,
                    worker_id,
                    {"stored_error": row["error"], "accepted": False, "kind": "fail"},
                    now,
                )
                return self._job_from_row(self._get_job_row(conn, job_id))
            self._check_lease(conn, row, worker_id, now)
            budget = int(row["max_attempts"] or self.max_attempts)
            attempts = int(row["attempts"] or 0)
            exhausted = attempts >= budget
            new_state = JobState.FAILED if exhausted else JobState.RETRY
            conn.execute(
                "UPDATE jobs SET state = ?, error = ?, updated_at = ? WHERE job_id = ?",
                (new_state, str(error) if error is not None else None, now, job_id),
            )
            conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
            self._event(
                conn,
                EVENT_RETRIES_EXHAUSTED if exhausted else EVENT_RETRY_SCHEDULED,
                job_id,
                worker_id,
                {"attempts": attempts, "max_attempts": budget, "error": error},
                now,
            )
            if exhausted:
                self._event(conn, EVENT_FAILED, job_id, worker_id, {"error": error}, now)
        return self.status(job_id)

    def status(self, job_id: str) -> Job:
        """Return the current state of a job. Raises :class:`UnknownJobError`."""

        rows = self._read(self._JOB_SELECT + "WHERE j.job_id = ?", (job_id,))
        if not rows:
            raise UnknownJobError(job_id)
        return self._job_from_row(rows[0])

    def list_jobs(self, state: Optional[str] = None, limit: Optional[int] = None) -> List[Job]:
        """List jobs in creation order, optionally filtered by state."""

        sql = self._JOB_SELECT
        params: List[Any] = []
        if state is not None:
            sql += "WHERE j.state = ? "
            params.append(state)
        sql += "ORDER BY j.seq ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [self._job_from_row(row) for row in self._read(sql, params)]

    def audit(self, job_id: Optional[str] = None, limit: Optional[int] = None) -> List[Event]:
        """Return the append-only audit trail (optionally for one job).

        Corrupt event rows never raise: unknown types are returned verbatim and
        non-JSON details are returned as raw text.
        """

        sql = "SELECT event_id, ts, job_id, worker_id, type, detail FROM events"
        params: List[Any] = []
        if job_id is not None:
            sql += " WHERE job_id = ?"
            params.append(job_id)
        sql += " ORDER BY event_id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        events = []
        for row in self._read(sql, params):
            try:
                ts = float(row["ts"]) if row["ts"] is not None else 0.0
            except (TypeError, ValueError):
                ts = 0.0
            event_type = row["type"] if row["type"] is not None else "UNKNOWN"
            events.append(
                Event(
                    event_id=int(row["event_id"]),
                    ts=ts,
                    type=str(event_type),
                    job_id=row["job_id"],
                    worker_id=row["worker_id"],
                    detail=decode_json(row["detail"]),
                )
            )
        return events

    def stats(self) -> Dict[str, Any]:
        """Aggregate counters.

        Guaranteed keys: ``jobs``, ``results`` (number of distinct jobs
        completed successfully), ``by_state``, ``events``, ``attempts``,
        ``workers``.
        """

        by_state: Dict[str, int] = {state: 0 for state in ALL_STATES}
        total = 0
        attempts = 0
        for row in self._read("SELECT state, COUNT(*) AS n, SUM(attempts) AS a FROM jobs GROUP BY state"):
            state = row["state"] if row["state"] is not None else "UNKNOWN"
            by_state[state] = int(row["n"])
            total += int(row["n"])
            attempts += int(row["a"] or 0)
        results = int(self._read("SELECT COUNT(*) AS n FROM results")[0]["n"])
        events = int(self._read("SELECT COUNT(*) AS n FROM events")[0]["n"])
        workers = int(
            self._read(
                "SELECT COUNT(DISTINCT worker_id) AS n FROM events WHERE worker_id IS NOT NULL"
            )[0]["n"]
        )
        return {
            "jobs": total,
            "results": results,
            "by_state": by_state,
            "events": events,
            "attempts": attempts,
            "workers": workers,
            "claimable": by_state.get(JobState.PENDING, 0) + by_state.get(JobState.RETRY, 0),
        }

    # -------------------------------------------------------------- recovery
    def repair(self) -> Dict[str, int]:
        """Deterministically sanitise corrupt persisted state.

        Rules (idempotent -- a second run reports zero changes):

        1. Leases whose job row no longer exists are removed.
        2. Jobs with an unknown persisted state are quarantined to FAILED while
           keeping payload/result/error/timestamps.
        3. Leases attached to terminal (DONE/FAILED) jobs are removed.
        4. Negative attempt counters are reset to 0; counters above the retry
           budget are clamped to the budget.
        5. Missing/invalid timestamps are replaced with "now" (never dropped).
        6. Jobs are never deleted and results are never rewritten.
        """

        summary = {
            "orphan_leases_removed": 0,
            "invalid_jobs_quarantined": 0,
            "terminal_leases_removed": 0,
            "attempts_clamped": 0,
            "timestamps_fixed": 0,
        }
        now = self._now()
        with self._write_tx() as conn:
            # 1. orphan leases -------------------------------------------------
            orphans = conn.execute(
                "SELECT job_id, worker_id FROM leases "
                "WHERE job_id NOT IN (SELECT job_id FROM jobs)"
            ).fetchall()
            for row in orphans:
                conn.execute("DELETE FROM leases WHERE job_id = ?", (row["job_id"],))
                self._event(
                    conn,
                    EVENT_ORPHAN_LEASE_REMOVED,
                    row["job_id"],
                    row["worker_id"],
                    {"reason": "lease references missing job"},
                    now,
                )
                summary["orphan_leases_removed"] += 1

            # 2. unknown job states -------------------------------------------
            rows = conn.execute("SELECT job_id, state, attempts, max_attempts, created_at, updated_at FROM jobs").fetchall()
            for row in rows:
                job_id = row["job_id"]
                state = row["state"]
                changed = False
                if state not in ALL_STATES:
                    conn.execute(
                        "UPDATE jobs SET state = ?, error = COALESCE(error, ?), updated_at = ? "
                        "WHERE job_id = ?",
                        (JobState.FAILED, f"repaired:invalid-state:{state!r}", now, job_id),
                    )
                    conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
                    self._event(
                        conn,
                        EVENT_REPAIRED,
                        job_id,
                        None,
                        {"action": "quarantined", "invalid_state": state},
                        now,
                    )
                    summary["invalid_jobs_quarantined"] += 1
                    changed = True

                budget = int(row["max_attempts"] or self.max_attempts)
                attempts = int(row["attempts"] or 0)
                if attempts < 0:
                    conn.execute("UPDATE jobs SET attempts = 0 WHERE job_id = ?", (job_id,))
                    summary["attempts_clamped"] += 1
                    changed = True
                elif attempts > budget:
                    conn.execute(
                        "UPDATE jobs SET attempts = ? WHERE job_id = ?", (budget, job_id)
                    )
                    summary["attempts_clamped"] += 1
                    changed = True

                if not row["created_at"] or not row["updated_at"]:
                    conn.execute(
                        "UPDATE jobs SET created_at = COALESCE(created_at, ?), "
                        "updated_at = COALESCE(updated_at, ?) WHERE job_id = ?",
                        (now, now, job_id),
                    )
                    summary["timestamps_fixed"] += 1
                    changed = True

                if changed:
                    conn.execute("UPDATE jobs SET updated_at = ? WHERE job_id = ?", (now, job_id))

            # 3. leases on terminal jobs --------------------------------------
            stuck = conn.execute(
                "SELECT l.job_id, l.worker_id FROM leases l JOIN jobs j ON j.job_id = l.job_id "
                "WHERE j.state IN (?, ?)",
                (JobState.DONE, JobState.FAILED),
            ).fetchall()
            for row in stuck:
                conn.execute("DELETE FROM leases WHERE job_id = ?", (row["job_id"],))
                self._event(
                    conn,
                    EVENT_REPAIRED,
                    row["job_id"],
                    row["worker_id"],
                    {"action": "removed_lease_on_terminal_job"},
                    now,
                )
                summary["terminal_leases_removed"] += 1

        return summary
