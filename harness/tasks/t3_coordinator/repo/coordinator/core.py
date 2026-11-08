"""Job coordinator -- PARTIAL IMPLEMENTATION (the hard parts are missing).

What already works
------------------
* SQLite schema bootstrap and migrations (:mod:`coordinator.schema`).
* ``add_job`` / ``status`` / ``list_jobs`` / ``audit`` / ``stats``.
* A single-worker happy path: claim -> complete.

What is NOT implemented / is incorrect (this is the exercise)
-------------------------------------------------------------
* ``claim`` is not atomic (check-then-act) and never considers lease expiry.
* Leases are written but never expire and ``heartbeat`` does not extend them.
* ``complete`` is not idempotent; repeated messages overwrite the result and
  append another ``COMPLETED`` receipt.
* Retries are unbounded: ``max_attempts`` is stored but never enforced.
* There is no crash recovery and no repair of corrupt persisted state.

See ``TASK.md`` for the required behaviour and the exact public contract.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .errors import (
    DuplicateJobError,
    InvalidTransitionError,
    LeaseError,
    StorageError,
    UnknownJobError,
)
from .models import (
    ACTIVE_STATES,
    ALL_STATES,
    TERMINAL_STATES,
    Event,
    Job,
    JobState,
    decode_json,
    encode_json,
)
from .schema import init_schema

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


class Coordinator:
    """Durable job queue (durability and the happy path are implemented).

    Parameters
    ----------
    db_path:
        Path of the SQLite database file used for persistence.
    max_attempts:
        Retry budget per job. Stored with every job (currently not enforced).
    lease_seconds:
        Length of a worker lease in seconds.
    """

    def __init__(
        self,
        db_path: str,
        *,
        max_attempts: int = 3,
        lease_seconds: float = 30.0,
    ) -> None:
        if not db_path:
            raise ValueError("db_path is required")
        if str(db_path) == ":memory:":
            raise ValueError("in-memory databases are not durable; use a file path")
        self.db_path = str(db_path)
        self.max_attempts = int(max_attempts)
        self.lease_seconds = float(lease_seconds)
        self._lock = threading.RLock()
        self._closed = False

        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)

        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=10.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 10000")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            init_schema(self._conn, self._now())
        except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
            raise StorageError(f"cannot open coordinator database {self.db_path}: {exc}") from exc

    # ------------------------------------------------------------------ utils
    def __enter__(self) -> "Coordinator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection. Idempotent."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                pass

    def _now(self) -> float:
        return time.time()

    def _read(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            if self._closed:
                raise StorageError("coordinator is closed")
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def _event(
        self,
        event_type: str,
        job_id: Optional[str],
        worker_id: Optional[str],
        detail: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO events (ts, job_id, worker_id, type, detail) VALUES (?, ?, ?, ?, ?)",
            (
                self._now() if now is None else now,
                job_id,
                worker_id,
                event_type,
                encode_json(detail),
            ),
        )

    _JOB_SELECT = (
        "SELECT j.*, l.worker_id AS lease_worker, l.expires_at AS lease_expires "
        "FROM jobs j LEFT JOIN leases l ON l.job_id = j.job_id "
    )

    def _job_from_row(self, row: sqlite3.Row) -> Job:
        state = row["state"]
        if state not in ALL_STATES:
            # Persisted state is validated here; there is no repair path yet.
            raise ValueError(f"corrupt job state for {row['job_id']}: {state!r}")
        max_attempts = row["max_attempts"]
        return Job(
            job_id=row["job_id"],
            state=state,
            payload=decode_json(row["payload"]),
            attempts=int(row["attempts"] or 0),
            max_attempts=int(max_attempts) if max_attempts is not None else self.max_attempts,
            worker_id=row["lease_worker"],
            lease_expires_at=row["lease_expires"],
            result=decode_json(row["result"]),
            error=row["error"],
            created_at=float(row["created_at"] or 0.0),
            updated_at=float(row["updated_at"] or 0.0),
        )

    def _get_job_row(self, job_id: str) -> Optional[sqlite3.Row]:
        rows = self._read(self._JOB_SELECT + "WHERE j.job_id = ?", (job_id,))
        return rows[0] if rows else None

    def _require_job(self, job_id: str) -> sqlite3.Row:
        row = self._get_job_row(job_id)
        if row is None:
            raise UnknownJobError(job_id)
        return row

    # ------------------------------------------------------------------- API
    def init_schema(self) -> None:
        """Create/migrate the schema (idempotent)."""

        with self._lock:
            init_schema(self._conn, self._now())

    def add_job(
        self,
        job_id: str,
        payload: Any = None,
        *,
        max_attempts: Optional[int] = None,
    ) -> Job:
        """Persist a new PENDING job. Duplicate ids raise DuplicateJobError."""

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        budget = self.max_attempts if max_attempts is None else int(max_attempts)
        now = self._now()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO jobs (job_id, payload, state, attempts, max_attempts, "
                    "created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?, ?)",
                    (job_id, encode_json(payload), JobState.PENDING, budget, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateJobError(job_id) from exc
            self._event(EVENT_ADDED, job_id, None, {"max_attempts": budget}, now)
        return self.status(job_id)

    def claim(self, worker_id: str) -> Optional[Job]:
        """Lease the next PENDING/RETRY job to ``worker_id`` (None when idle).

        NOTE: the check and the update below are separate statements, and the
        lease expiry is not inspected at all.
        """

        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string")
        now = self._now()
        rows = self._read(
            self._JOB_SELECT + "WHERE j.state IN (?, ?) ORDER BY j.seq ASC LIMIT 1",
            (JobState.PENDING, JobState.RETRY),
        )
        if not rows:
            return None
        row = rows[0]
        job_id = row["job_id"]
        # Yield briefly so competing workers are not starved.
        time.sleep(0.001)
        attempts = int(row["attempts"] or 0) + 1
        budget = int(row["max_attempts"] or self.max_attempts)
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET state = ?, attempts = ?, updated_at = ? WHERE job_id = ?",
                (JobState.CLAIMED, attempts, now, job_id),
            )
            self._conn.execute(
                "INSERT INTO leases (job_id, worker_id, expires_at, heartbeat_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET worker_id = excluded.worker_id, "
                "expires_at = excluded.expires_at, heartbeat_at = excluded.heartbeat_at",
                (job_id, worker_id, now + self.lease_seconds, now),
            )
            self._event(EVENT_CLAIMED, job_id, worker_id, {"attempt": attempts}, now)
        return self.status(job_id)

    def _check_lease(self, row: sqlite3.Row, worker_id: str) -> None:
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

    def start(self, job_id: str, worker_id: str) -> Job:
        """Transition CLAIMED -> RUNNING (lease owner only)."""

        now = self._now()
        with self._lock:
            row = self._require_job(job_id)
            self._check_lease(row, worker_id)
            if row["state"] != JobState.CLAIMED:
                raise InvalidTransitionError(
                    f"job {job_id} cannot start from state {row['state']}"
                )
            self._conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (JobState.RUNNING, now, job_id),
            )
            self._event(EVENT_STARTED, job_id, worker_id, None, now)
        return self.status(job_id)

    def heartbeat(self, job_id: str, worker_id: str) -> Job:
        """Record a heartbeat for a job owned by ``worker_id``."""

        now = self._now()
        with self._lock:
            row = self._require_job(job_id)
            self._check_lease(row, worker_id)
            self._conn.execute(
                "UPDATE leases SET heartbeat_at = ? WHERE job_id = ?", (now, job_id)
            )
            self._conn.execute("UPDATE jobs SET updated_at = ? WHERE job_id = ?", (now, job_id))
            self._event(EVENT_HEARTBEAT, job_id, worker_id, None, now)
        return self.status(job_id)

    def complete(self, job_id: str, worker_id: str, result: Any = None) -> Job:
        """Mark a leased job DONE and persist ``result``."""

        now = self._now()
        with self._lock:
            row = self._require_job(job_id)
            self._check_lease(row, worker_id)
            self._conn.execute(
                "UPDATE jobs SET state = ?, result = ?, error = NULL, updated_at = ? "
                "WHERE job_id = ?",
                (JobState.DONE, encode_json(result), now, job_id),
            )
            self._conn.execute(
                "INSERT INTO results (job_id, worker_id, result, completed_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET worker_id = excluded.worker_id, "
                "result = excluded.result, completed_at = excluded.completed_at",
                (job_id, worker_id, encode_json(result), now),
            )
            self._conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
            self._event(EVENT_COMPLETED, job_id, worker_id, {"result": result}, now)
        return self.status(job_id)

    def fail(self, job_id: str, worker_id: str, error: str = "failed") -> Job:
        """Report a failed attempt and make the job claimable again."""

        now = self._now()
        with self._lock:
            row = self._require_job(job_id)
            self._check_lease(row, worker_id)
            self._conn.execute(
                "UPDATE jobs SET state = ?, error = ?, updated_at = ? WHERE job_id = ?",
                (JobState.RETRY, str(error) if error is not None else None, now, job_id),
            )
            self._conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
            self._event(EVENT_RETRY_SCHEDULED, job_id, worker_id, {"error": error}, now)
        return self.status(job_id)

    def status(self, job_id: str) -> Job:
        """Return the current state of a job. Raises UnknownJobError."""

        row = self._get_job_row(job_id)
        if row is None:
            raise UnknownJobError(job_id)
        return self._job_from_row(row)

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
        """Return the append-only audit trail (optionally for one job)."""

        sql = "SELECT event_id, ts, job_id, worker_id, type, detail FROM events"
        params: List[Any] = []
        if job_id is not None:
            sql += " WHERE job_id = ?"
            params.append(job_id)
        sql += " ORDER BY event_id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [
            Event(
                event_id=int(row["event_id"]),
                ts=float(row["ts"] or 0.0),
                type=str(row["type"]),
                job_id=row["job_id"],
                worker_id=row["worker_id"],
                detail=decode_json(row["detail"]),
            )
            for row in self._read(sql, params)
        ]

    def stats(self) -> Dict[str, Any]:
        """Aggregate counters (jobs, results, by_state, events, attempts, workers)."""

        by_state: Dict[str, int] = {state: 0 for state in ALL_STATES}
        total = 0
        attempts = 0
        for row in self._read(
            "SELECT state, COUNT(*) AS n, SUM(attempts) AS a FROM jobs GROUP BY state"
        ):
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
        """Sanitise corrupt persisted state.

        Placeholder: returns an empty summary and changes nothing.
        """

        return {
            "orphan_leases_removed": 0,
            "invalid_jobs_quarantined": 0,
            "terminal_leases_removed": 0,
            "attempts_clamped": 0,
            "timestamps_fixed": 0,
        }
