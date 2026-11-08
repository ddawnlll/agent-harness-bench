"""Command line interface for the job coordinator.

Usage::

    python3 -m coordinator.cli --db jobs.db init
    python3 -m coordinator.cli --db jobs.db add job-1 --payload '{"cmd": "make test"}'
    python3 -m coordinator.cli --db jobs.db claim --worker w1
    python3 -m coordinator.cli --db jobs.db heartbeat job-1 --worker w1
    python3 -m coordinator.cli --db jobs.db complete job-1 --worker w1 --result '{"ok": true}'
    python3 -m coordinator.cli --db jobs.db fail job-1 --worker w1 --error "tests failed"
    python3 -m coordinator.cli --db jobs.db status job-1
    python3 -m coordinator.cli --db jobs.db list --state PENDING
    python3 -m coordinator.cli --db jobs.db audit --job job-1
    python3 -m coordinator.cli --db jobs.db stats
    python3 -m coordinator.cli --db jobs.db repair
    python3 -m coordinator.cli --db jobs.db serve-worker --worker w1 --max-jobs 5
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from .core import Coordinator
from .errors import CoordinatorError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_EMPTY = 2


def _loads(text: Optional[str]) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coordinator", description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite database path")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--lease-seconds", type=float, default=30.0)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create/migrate the schema")

    add = sub.add_parser("add", help="add a job")
    add.add_argument("job_id")
    add.add_argument("--payload")
    add.add_argument("--max-attempts", dest="job_max_attempts", type=int, default=None)

    claim = sub.add_parser("claim", help="claim the next job")
    claim.add_argument("--worker", required=True)

    for name in ("start", "heartbeat", "complete", "fail", "status"):
        cmd = sub.add_parser(name)
        cmd.add_argument("job_id")
        if name != "status":
            cmd.add_argument("--worker", required=True)
        if name == "complete":
            cmd.add_argument("--result")
        if name == "fail":
            cmd.add_argument("--error", default="failed")

    listing = sub.add_parser("list")
    listing.add_argument("--state", default=None)
    listing.add_argument("--limit", type=int, default=None)

    audit = sub.add_parser("audit")
    audit.add_argument("--job", default=None)
    audit.add_argument("--limit", type=int, default=None)

    sub.add_parser("stats")
    sub.add_parser("repair")

    serve = sub.add_parser("serve-worker", help="claim and auto-complete jobs in a loop")
    serve.add_argument("--worker", required=True)
    serve.add_argument("--max-jobs", type=int, default=1)
    serve.add_argument("--result", default='{"ok": true}')
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with Coordinator(
            args.db,
            max_attempts=args.max_attempts,
            lease_seconds=args.lease_seconds,
        ) as coord:
            return _dispatch(coord, args)
    except CoordinatorError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _dispatch(coord: Coordinator, args: argparse.Namespace) -> int:
    cmd = args.command
    if cmd == "init":
        coord.init_schema()
        print("ok")
    elif cmd == "add":
        job = coord.add_job(args.job_id, _loads(args.payload), max_attempts=args.job_max_attempts)
        print(_dumps(job.to_dict()))
    elif cmd == "claim":
        job = coord.claim(args.worker)
        if job is None:
            print("null")
            return EXIT_EMPTY
        print(_dumps(job.to_dict()))
    elif cmd == "start":
        print(_dumps(coord.start(args.job_id, args.worker).to_dict()))
    elif cmd == "heartbeat":
        print(_dumps(coord.heartbeat(args.job_id, args.worker).to_dict()))
    elif cmd == "complete":
        print(_dumps(coord.complete(args.job_id, args.worker, _loads(args.result)).to_dict()))
    elif cmd == "fail":
        print(_dumps(coord.fail(args.job_id, args.worker, args.error).to_dict()))
    elif cmd == "status":
        print(_dumps(coord.status(args.job_id).to_dict()))
    elif cmd == "list":
        print(_dumps([job.to_dict() for job in coord.list_jobs(args.state, args.limit)]))
    elif cmd == "audit":
        print(_dumps([event.to_dict() for event in coord.audit(args.job, args.limit)]))
    elif cmd == "stats":
        print(_dumps(coord.stats()))
    elif cmd == "repair":
        print(_dumps(coord.repair()))
    elif cmd == "serve-worker":
        done = 0
        while done < args.max_jobs:
            job = coord.claim(args.worker)
            if job is None:
                break
            coord.complete(job.job_id, args.worker, _loads(args.result))
            done += 1
        print(_dumps({"completed": done, "worker": args.worker}))
    else:  # pragma: no cover - argparse enforces the choices
        raise SystemExit(f"unknown command: {cmd}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
