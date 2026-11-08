"""Shared helpers for the hidden test suite.

This module is copied into a throw-away copy of the candidate workspace; it is
never part of the candidate's repository.  Everything here is standard library
only.

Design notes
------------
* Every test gets its own temporary directory and SQLite file.
* Concurrency is exercised with real OS processes (never threads pretending to
  be processes) and a file-free start barrier: all children are spawned back to
  back and then synchronised on a start timestamp passed on the command line.
* ``run_child`` / ``spawn_child`` always enforce a timeout, so a hung candidate
  fails the case instead of hanging the grader.
* ``retry_transient`` absorbs SQLite "database is locked" blips: those are a
  quality issue, not the failure this benchmark is trying to detect.  Anything
  that indicates duplicate ownership or data loss is *not* retried.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CHILD = os.path.join(HERE, "child_worker.py")
WORKSPACE = os.getcwd()

# Transient SQLite contention only; semantic errors are never retried.
_TRANSIENT = ("database is locked", "database table is locked", "disk I/O error")


def new_db(prefix: str = "t3"):
    """Create an isolated temp dir + database path."""
    directory = tempfile.mkdtemp(prefix=f"{prefix}-")
    return directory, os.path.join(directory, "jobs.db")


def unique_id(prefix: str) -> str:
    """Random job id: hardcoded-id cheats cannot pre-answer these."""
    return f"{prefix}-{uuid.uuid4().hex}"


def make_coord(db, **kwargs):
    from coordinator import Coordinator

    return Coordinator(db, **kwargs)


def child_env():
    env = dict(os.environ)
    root = WORKSPACE
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    # Keep the child single-threaded for deterministic SQLite behaviour.
    env.pop("PYTHONHASHSEED", None)
    return env


def _child_cmd(args):
    return [sys.executable, CHILD] + [str(a) for a in args]


def spawn_child(args, **kwargs):
    return subprocess.Popen(
        _child_cmd(args),
        cwd=WORKSPACE,
        env=child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        **kwargs,
    )


def reap(proc, timeout=30.0):
    """Wait for a child with a hard timeout; kill its process group on hang."""
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate(timeout=5)
        raise AssertionError(
            f"child worker timed out after {timeout}s (stdout={out[-800:]!r} "
            f"stderr={err[-800:]!r})"
        )
    return out, err


def run_child(args, timeout=30.0, check=True):
    proc = spawn_child(args)
    out, err = reap(proc, timeout=timeout)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"child failed rc={proc.returncode} args={args} "
            f"stdout={out[-1500:]!r} stderr={err[-2000:]!r}"
        )
    return proc, out, err


def read_json(path, timeout=10.0, required=True):
    """Read a file a child wrote, tolerating short race windows."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            if text.strip():
                return json.loads(text)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            pass
        if time.monotonic() >= deadline:
            if required:
                raise AssertionError(f"child output missing/unreadable: {path}")
            return None
        time.sleep(0.02)


def read_jsonl(path, required=True):
    """Read a JSONL log, ignoring a possibly truncated final line.

    A worker killed with SIGKILL can die mid-write; a torn last record is
    expected and must not confuse the analysis.
    """
    records = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        if required:
            raise AssertionError(f"child log missing: {path}")
        return []
    return records


def wait_until(predicate, timeout=10.0, interval=0.02, message="condition"):
    """Poll ``predicate`` until true; raise AssertionError on timeout.

    Returns the elapsed seconds when the condition became true.
    """
    start = time.monotonic()
    deadline = start + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return time.monotonic() - start
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported below
            last_error = exc
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {message}"
                         + (f" (last error: {last_error!r})" if last_error else ""))


def retry_transient(func, attempts=40, delay=0.02, label="operation"):
    """Retry only transient SQLite lock errors; re-raise anything else."""
    last = None
    for _ in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if not any(marker in text for marker in _TRANSIENT):
                raise
            last = exc
            time.sleep(delay)
    raise AssertionError(f"{label} kept failing with transient storage errors: {last!r}")


def event_types(coord, job_id=None):
    return [event.type for event in coord.audit(job_id)]


def completed_event_count(coord, job_id):
    return sum(1 for event in coord.audit(job_id) if event.type == "COMPLETED")


def assert_job_invariants(testcase, coord, job_id, max_attempts=None):
    """Common invariants every persisted job must satisfy."""
    job = coord.status(job_id)
    limit = job.max_attempts if max_attempts is None else max_attempts
    testcase.assertGreaterEqual(job.attempts, 0, f"{job_id}: negative attempts")
    testcase.assertLessEqual(
        job.attempts, limit, f"{job_id}: attempts {job.attempts} exceeds budget {limit}"
    )
    if job.state == "DONE":
        testcase.assertEqual(
            completed_event_count(coord, job_id), 1, f"{job_id}: duplicate COMPLETED receipts"
        )
    return job


def error_class(name):
    """Resolve a documented exception class, with a clear failure message."""
    import importlib

    import coordinator  # noqa: F401 - imported for its side effect

    modules = [coordinator]
    try:
        modules.insert(0, importlib.import_module("coordinator.errors"))
    except ImportError:
        pass
    for module in modules:
        candidate = getattr(module, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            return candidate
    raise AssertionError(f"documented exception coordinator.errors.{name} is missing")


def write_evidence(name, payload):
    """Persist grader-side evidence (best effort; never fails a test)."""
    try:
        directory = os.path.join(WORKSPACE, "_hidden_evidence")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f"{name}.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
    except OSError:
        pass


def cleanup(directory):
    import shutil

    shutil.rmtree(directory, ignore_errors=True)


def make_coordinator_factory(db, lease_seconds=30.0, max_attempts=3, **kwargs):
    """Return a zero-argument factory so a test can open several instances."""

    def factory():
        return make_coord(db, lease_seconds=lease_seconds, max_attempts=max_attempts, **kwargs)

    return factory
