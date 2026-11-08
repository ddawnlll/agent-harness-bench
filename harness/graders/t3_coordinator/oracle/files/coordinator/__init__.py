"""Durable, crash-safe job coordinator for unattended engineering workers.

Public API (see ``TASK.md`` for the contract)::

    from coordinator import Coordinator, Worker, Job, JobState

    with Coordinator("jobs.db", max_attempts=3, lease_seconds=30.0) as coord:
        coord.add_job("job-1", {"task": "run tests"})
        job = coord.claim("worker-a")          # atomic, None when idle
        coord.heartbeat(job.job_id, "worker-a")
        coord.complete(job.job_id, "worker-a", {"ok": True})
        coord.status("job-1")
        coord.audit("job-1")
"""

from .core import Coordinator
from .errors import (
    CoordinatorError,
    CorruptStateError,
    DuplicateJobError,
    InvalidTransitionError,
    LeaseError,
    StorageError,
    UnknownJobError,
)
from .models import ALL_STATES, Event, Job, JobState, Worker

__all__ = [
    "Coordinator",
    "Worker",
    "Job",
    "Event",
    "JobState",
    "ALL_STATES",
    "CoordinatorError",
    "DuplicateJobError",
    "UnknownJobError",
    "LeaseError",
    "InvalidTransitionError",
    "CorruptStateError",
    "StorageError",
]

__version__ = "1.0.0"
