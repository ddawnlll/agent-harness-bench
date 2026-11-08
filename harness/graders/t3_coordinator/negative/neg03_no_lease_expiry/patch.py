"""NEGATIVE 3: expired leases are never reclaimed (no crash recovery).

The expiry predicate is kept bound but can never match.  Heartbeats still
extend leases, so only the "owner died" path is broken.
Expected failure: crash_recovery gate.
"""
from _patchlib import main_wrapper, replace_once


def patch(path, workspace):
    replace_once(
        path,
        'AND (j.state IN (?, ?) OR l.job_id IS NULL OR l.expires_at <= ?) ',
        'AND (j.state IN (?, ?) OR l.job_id IS NULL OR (l.expires_at <= ? AND 0)) ',
        "neg03-no-expiry",
    )


if __name__ == "__main__":
    main_wrapper("neg03_no_lease_expiry", patch)
