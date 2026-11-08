"""Run a unittest directory and emit machine-readable per-test results.

Usage::

    python3 run_hidden.py --start-dir tests --pattern 'test_*.py' --out public.json
    python3 run_hidden.py --start-dir . --pattern 'test_*.py' --out hidden.json

The case id of a hidden test is the token before the first colon of the first
docstring line (``F4: ...`` -> ``F4``).  Public tests have no case id; their
test id is used instead.

Exit code: 0 when every test passed, 1 otherwise, 2 when discovery itself
failed (e.g. an import error in the candidate's code).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CWD = os.getcwd()
# Hidden helpers must win over same-named candidate modules (cheat vector).
for entry in (CWD, HERE):
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)

_CASE_RE = re.compile(r"^([A-Z]\d+)$")


def _case_id(test):
    doc = getattr(test, "_testMethodDoc", None) or ""
    lines = doc.strip().splitlines()
    if not lines:
        return None
    first = lines[0]
    if ":" not in first:
        return None
    token = first.split(":", 1)[0].strip()
    return token if _CASE_RE.match(token) else None


def _format_error(err):
    exc_type, exc_value, tb = err
    text = "".join(traceback.format_exception(exc_type, exc_value, tb))
    lines = text.strip().splitlines()
    return "\n".join(lines[-12:])[-4000:]


class RecordingResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.records = []
        self._started = {}

    def startTest(self, test):  # noqa: N802 - unittest API
        super().startTest(test)
        self._started[test.id()] = time.monotonic()

    def _record(self, test, passed, detail):
        started = self._started.pop(test.id(), time.monotonic())
        self.records.append(
            {
                "id": test.id(),
                "case": _case_id(test),
                "passed": bool(passed),
                "detail": (detail or "")[:4000],
                "duration_ms": round((time.monotonic() - started) * 1000.0, 2),
            }
        )

    def addSuccess(self, test):  # noqa: N802
        super().addSuccess(test)
        self._record(test, True, "")

    def addFailure(self, test, err):  # noqa: N802
        super().addFailure(test, err)
        self._record(test, False, _format_error(err))

    def addError(self, test, err):  # noqa: N802
        super().addError(test, err)
        self._record(test, False, _format_error(err))

    def addSkip(self, test, reason):  # noqa: N802
        super().addSkip(test, reason)
        self._record(test, False, f"skipped: {reason}")

    def addExpectedFailure(self, test, err):  # noqa: N802
        super().addExpectedFailure(test, err)
        self._record(test, True, "expected failure")

    def addUnexpectedSuccess(self, test):  # noqa: N802
        super().addUnexpectedSuccess(test)
        self._record(test, False, "unexpected success")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-dir", default=".")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    start_dir = args.start_dir
    if not os.path.abspath(start_dir).startswith(os.path.abspath(CWD)):
        raise SystemExit(f"start dir outside workspace: {start_dir}")

    payload = {
        "start_dir": start_dir,
        "records": [],
        "counts": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
        "elapsed_s": 0.0,
        "discovery_error": None,
    }
    started = time.monotonic()
    try:
        suite = unittest.TestLoader().discover(
            start_dir=os.path.abspath(start_dir), pattern=args.pattern, top_level_dir=None
        )
        result = RecordingResult()
        suite.run(result)
    except Exception as exc:  # noqa: BLE001 - import failures land here
        payload["discovery_error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-3000:]}"
        payload["elapsed_s"] = round(time.monotonic() - started, 3)
        _write(args.out, payload)
        print(f"DISCOVERY ERROR: {payload['discovery_error']}")
        return 2

    payload["records"] = result.records
    payload["counts"] = {
        "tests": len(result.records),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
    }
    payload["elapsed_s"] = round(time.monotonic() - started, 3)
    _write(args.out, payload)

    if not args.quiet:
        for record in result.records:
            status = "ok" if record["passed"] else "FAIL"
            print(f"{status:4s} {record['case'] or '-':6s} {record['id']} "
                  f"({record['duration_ms']}ms)")
            if not record["passed"]:
                for line in record["detail"].splitlines()[-6:]:
                    print(f"       {line}")
        print(f"-- {payload['counts']} in {payload['elapsed_s']}s")
    failed = payload["counts"]["failures"] + payload["counts"]["errors"]
    return 0 if failed == 0 and payload["counts"]["tests"] > 0 else 1


def _write(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)


if __name__ == "__main__":
    raise SystemExit(main())
