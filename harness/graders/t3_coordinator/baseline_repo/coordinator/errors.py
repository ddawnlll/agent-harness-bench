"""Exception hierarchy for the job coordinator.

All errors raised for *expected* misuse derive from :class:`CoordinatorError`
so callers can handle them without catching programming errors.
"""

from __future__ import annotations


class CoordinatorError(Exception):
    """Base class for all coordinator errors."""


class DuplicateJobError(CoordinatorError):
    """Raised when ``add_job`` is called twice with the same job id."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job already exists: {job_id}")
        self.job_id = job_id


class UnknownJobError(CoordinatorError):
    """Raised when a job id does not exist in the store."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"unknown job: {job_id}")
        self.job_id = job_id


class LeaseError(CoordinatorError):
    """Raised when a worker does not hold a valid lease for a job.

    Covers: foreign worker, missing lease, expired lease.
    """


class InvalidTransitionError(CoordinatorError):
    """Raised when a state transition is not allowed from the current state."""


class CorruptStateError(CoordinatorError):
    """Raised when persisted state cannot be repaired safely."""


class StorageError(CoordinatorError):
    """Raised when the underlying store cannot be opened or used."""
