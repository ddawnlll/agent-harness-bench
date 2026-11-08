"""Subprocess helper used by the hidden suite.

Never part of the candidate repository.  Each mode is a small real worker:

    add                     add jobs, exit normally
    add-claim-close         add jobs, claim one, exit normally (graceful restart)
    add-claim-die           add jobs, claim one, SIGKILL itself (abrupt crash)
    claim-die               claim one job, SIGKILL itself (crash mid-ownership)
    claim-hold              claim one job, hold it for N seconds, exit
    stress                  claim + complete until idle, JSONL log
    complete                attempt one completion, record the outcome
    heartbeat               attempt one heartbeat, record the outcome
    read                    read the database from a fresh process, dump state

All modes write a JSON (or JSONL) report; the parent test only looks at files,
never at a shared in-process object.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time


def _add_workspace_to_path():
    here = os.path.dirname(os.path.abspath(__file__))
    workspace = os.path.dirname(here) if os.path.basename(here).startswith("_hidden") else os.getcwd()
    for path in (os.getcwd(), workspace):
        if path and path not in sys.path:
            sys.path.insert(0, path)


_add_workspace_to_path()


def _open(args):
    from coordinator import Coordinator

    kwargs = {"lease_seconds": args.lease}
    if getattr(args, "max_attempts", None) is not None:
        kwargs["max_attempts"] = args.max_attempts
    return Coordinator(args.db, **kwargs)


def _write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=str)
        handle.flush()
        os.fsync(handle.fileno())


def _job_dict(job):
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "state": job.state,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "worker_id": job.worker_id,
        "lease_expires_at": job.lease_expires_at,
        "payload": job.payload,
        "result": job.result,
        "error": job.error,
    }


def _retry(func, attempts=40, delay=0.02):
    last = None
    for _ in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - classify below
            text = str(exc).lower()
            if not any(
                marker in text
                for marker in ("database is locked", "database table is locked")
            ):
                raise
            last = exc
            time.sleep(delay)
    raise last  # type: ignore[misc]


def _die():
    os.kill(os.getpid(), signal.SIGKILL)


def cmd_add(args):
    coord = _open(args)
    ids = []
    try:
        for index in range(args.count):
            job_id = f"{args.prefix}-{index:04d}"
            coord.add_job(job_id, {"job": job_id, "n": index})
            ids.append(job_id)
    finally:
        coord.close()
    _write_json(args.out, {"jobs": ids})


def cmd_add_claim(args):
    coord = _open(args)
    jobs = []
    try:
        for index in range(args.count):
            job_id = f"{args.prefix}-{index:04d}"
            coord.add_job(job_id, {"job": job_id, "n": index})
            jobs.append(job_id)
        job = _retry(lambda: coord.claim(args.worker))
        if job is not None:
            _retry(lambda: coord.heartbeat(job.job_id, args.worker), attempts=3)
        report = {"jobs": jobs, "job": _job_dict(job), "pid": os.getpid()}
    finally:
        if args.mode == "add-claim-close":
            coord.close()
    _write_json(args.out, report)
    if args.mode == "add-claim-die":
        _die()


def cmd_claim_die(args):
    coord = _open(args)
    job = _retry(lambda: coord.claim(args.worker))
    report = {
        "job": _job_dict(job),
        "pid": os.getpid(),
        "claimed_at": time.time(),
        "worker": args.worker,
    }
    _write_json(args.out, report)
    # No close(): simulate SIGKILL of a worker that just took ownership.
    _die()


def cmd_claim_hold(args):
    coord = _open(args)
    job = _retry(lambda: coord.claim(args.worker))
    report = {"job": _job_dict(job), "pid": os.getpid(), "claimed_at": time.time()}
    _write_json(args.out, report)
    time.sleep(args.seconds)
    coord.close()


def cmd_stress(args):
    coord = _open(args)
    log_path = args.out
    started = False
    while not started:
        if time.time() >= args.start_at:
            started = True
        else:
            time.sleep(0.002)
    claimed = 0
    with open(log_path, "a", encoding="utf-8", buffering=1) as log:

        def emit(record):
            log.write(json.dumps(record, default=str) + "\n")

        try:
            while claimed < args.max_jobs:
                job = _retry(lambda: coord.claim(args.worker))
                if job is None:
                    break
                claimed += 1
                payload = job.payload
                ok_payload = (
                    isinstance(payload, dict) and payload.get("job") == job.job_id
                )
                emit(
                    {
                        "op": "claim",
                        "job": job.job_id,
                        "attempts": job.attempts,
                        "worker": args.worker,
                        "payload_ok": ok_payload,
                    }
                )
                if not ok_payload:
                    emit({"op": "error", "kind": "payload", "job": job.job_id})
                    break
                if args.work > 0:
                    time.sleep(args.work)
                try:
                    done = _retry(
                        lambda: coord.complete(job.job_id, args.worker, {"by": args.worker})
                    )
                    emit({"op": "complete", "job": job.job_id, "state": done.state})
                except Exception as exc:  # noqa: BLE001 - reported to the parent
                    emit(
                        {
                            "op": "complete_error",
                            "job": job.job_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    break
        except Exception as exc:  # noqa: BLE001
            emit({"op": "fatal", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            emit({"op": "exit", "claimed": claimed})
            coord.close()


def cmd_complete(args):
    from coordinator.errors import CoordinatorError

    coord = _open(args)
    report = {"ok": False, "error": None}
    try:
        job = coord.complete(args.job, args.worker, json.loads(args.result))
        report = {
            "ok": True,
            "state": job.state,
            "result": job.result,
            "attempts": job.attempts,
        }
    except CoordinatorError as exc:
        report = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    finally:
        coord.close()
    _write_json(args.out, report)


def cmd_heartbeat(args):
    from coordinator.errors import CoordinatorError

    coord = _open(args)
    report = {"ok": False, "error": None}
    try:
        job = coord.heartbeat(args.job, args.worker)
        report = {"ok": True, "lease_expires_at": job.lease_expires_at}
    except CoordinatorError as exc:
        report = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    finally:
        coord.close()
    _write_json(args.out, report)


def cmd_read(args):
    coord = _open(args)
    try:
        jobs = [
            {
                "job_id": job.job_id,
                "state": job.state,
                "attempts": job.attempts,
                "worker_id": job.worker_id,
                "result": job.result,
            }
            for job in coord.list_jobs()
        ]
        report = {
            "stats": coord.stats(),
            "jobs": jobs,
            "events": [event.type for event in coord.audit()],
        }
    finally:
        coord.close()
    _write_json(args.out, report)


def build_parser():
    parser = argparse.ArgumentParser(prog="hidden-child-worker")
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--worker", default="child")
    parser.add_argument("--lease", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=None)
    sub = parser.add_subparsers(dest="mode", required=True)

    add = sub.add_parser("add")
    add.add_argument("--count", type=int, default=1)
    add.add_argument("--prefix", default="child-job")

    add_claim = sub.add_parser("add-claim-close")
    add_claim.add_argument("--count", type=int, default=1)
    add_claim.add_argument("--prefix", default="child-job")

    add_claim_die = sub.add_parser("add-claim-die")
    add_claim_die.add_argument("--count", type=int, default=1)
    add_claim_die.add_argument("--prefix", default="child-job")

    sub.add_parser("claim-die")
    hold = sub.add_parser("claim-hold")
    hold.add_argument("--seconds", type=float, default=2.0)

    stress = sub.add_parser("stress")
    stress.add_argument("--start-at", type=float, default=0.0)
    stress.add_argument("--work", type=float, default=0.0)
    stress.add_argument("--max-jobs", type=int, default=100000)

    complete = sub.add_parser("complete")
    complete.add_argument("--job", required=True)
    complete.add_argument("--result", default="null")

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--job", required=True)

    sub.add_parser("read")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "add": cmd_add,
        "add-claim-close": cmd_add_claim,
        "add-claim-die": cmd_add_claim,
        "claim-die": cmd_claim_die,
        "claim-hold": cmd_claim_hold,
        "stress": cmd_stress,
        "complete": cmd_complete,
        "heartbeat": cmd_heartbeat,
        "read": cmd_read,
    }
    if args.mode == "add-claim-close":
        args.mode = "add-claim-close"
    handlers[args.mode](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
