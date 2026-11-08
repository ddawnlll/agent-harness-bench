"""Value objects shared by the coordinator implementation and its callers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class JobState:
    """String constants for the persisted job lifecycle.

    The lifecycle is::

        PENDING --claim--> CLAIMED --start--> RUNNING --complete--> DONE
                             |                    |
                             +---fail/expiry-----> RETRY --claim--> ...
                             |
                             +---budget exhausted--> FAILED (terminal)

    ``RETRY`` is claimable exactly like ``PENDING``; the distinction exists so
    that audit trails can tell "never attempted" from "attempt failed".
    ``DONE`` and ``FAILED`` are terminal.
    """

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    DONE = "DONE"
    FAILED = "FAILED"


ALL_STATES = (
    JobState.PENDING,
    JobState.CLAIMED,
    JobState.RUNNING,
    JobState.RETRY,
    JobState.DONE,
    JobState.FAILED,
)

ACTIVE_STATES = (JobState.CLAIMED, JobState.RUNNING)
CLAIMABLE_STATES = (JobState.PENDING, JobState.RETRY)
TERMINAL_STATES = (JobState.DONE, JobState.FAILED)


def decode_json(raw: Optional[str], default: Any = None) -> Any:
    """Decode a JSON column, tolerating corrupt/truncated persisted values.

    Corrupt payloads must never crash the service: the raw text is returned
    instead so that operators can still see (and recover) the data.
    """

    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def encode_json(value: Any) -> Optional[str]:
    """Encode a value for a JSON column. ``None`` stays ``None``."""

    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class Job:
    """A persisted job plus the lease information visible to callers."""

    job_id: str
    state: str
    payload: Any = None
    attempts: int = 0
    max_attempts: int = 3
    worker_id: Optional[str] = None
    lease_expires_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "payload": self.payload,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "worker_id": self.worker_id,
            "lease_expires_at": self.lease_expires_at,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Event:
    """One append-only audit receipt."""

    event_id: int
    ts: float
    type: str
    job_id: Optional[str] = None
    worker_id: Optional[str] = None
    detail: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "type": self.type,
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Worker:
    """Convenience wrapper a worker process can use to talk to a coordinator."""

    worker_id: str
    tags: Dict[str, str] = field(default_factory=dict)

    def claim(self, coordinator: Any) -> Optional[Job]:
        return coordinator.claim(self.worker_id)

    def heartbeat(self, coordinator: Any, job_id: str) -> Job:
        return coordinator.heartbeat(job_id, self.worker_id)

    def complete(self, coordinator: Any, job_id: str, result: Any = None) -> Job:
        return coordinator.complete(job_id, self.worker_id, result)

    def fail(self, coordinator: Any, job_id: str, error: str = "failed") -> Job:
        return coordinator.fail(job_id, self.worker_id, error)
