#!/usr/bin/env python3
"""Validate the t3_coordinator grader end-to-end.

What it does
------------
1. Fixture integrity: the task repository is a clean git checkout at the commit
   recorded in ``manifest.json`` and ``baseline_repo/`` is an exact export of it.
2. Pristine baseline must FAIL (the hard semantics are absent).
3. Oracle must PASS with total >= 80 and every P0 gate green.
4. Every negative control must FAIL and must trip its documented gate(s).
5. Determinism: the oracle is graded 3 times in independent workspaces and the
   verdict signatures must be identical. Selected negatives are re-run to prove
   their failure is not a flake.
6. ``validation_report.json`` is written next to this script; exit code is
   nonzero if any expectation fails.

Usage::

    python3 validate.py [--quick] [--report validation_report.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GRADE = os.path.join(HERE, "grade.py")
BASELINE_REPO = os.path.join(HERE, "baseline_repo")
ORACLE = os.path.join(HERE, "oracle", "apply_oracle.sh")
NEGATIVE_ROOT = os.path.join(HERE, "negative")
TASK_REPO = os.path.normpath(
    os.path.join(HERE, "..", "..", "tasks", "t3_coordinator", "repo")
)
MANIFEST = os.path.join(HERE, "manifest.json")

NEGATIVES = [
    "neg01_inmemory",
    "neg02_nonatomic_claim",
    "neg03_no_lease_expiry",
    "neg04_nonidempotent_complete",
    "neg05_unbounded_retries",
    "neg06_corrupt_state_crash",
    "neg07_test_deletion",
]
# Negatives whose detection must be proven non-flaky (race / timing driven).
EXTRA_REPEATS = {"neg02_nonatomic_claim": 2, "neg06_corrupt_state_crash": 2}
ORACLE_REPEATS = 3


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_files(root: str) -> dict[str, str]:
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git"))
        for filename in sorted(filenames):
            if filename.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, filename)
            files[os.path.relpath(path, root)] = sha256(path)
    return files


def run(cmd, cwd=None, timeout=600):
    started = time.monotonic()
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return {
        "cmd": " ".join(cmd),
        "rc": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
        "elapsed_s": round(time.monotonic() - started, 2),
    }


def check_fixture_integrity() -> dict:
    result = {"passed": False, "details": {}, "errors": []}
    if not os.path.isdir(TASK_REPO):
        result["errors"].append(f"task repo missing: {TASK_REPO}")
        return result
    try:
        with open(MANIFEST, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        result["errors"].append(f"manifest unreadable: {exc}")
        return result

    head = run(["git", "-C", TASK_REPO, "rev-parse", "HEAD"])
    status = run(["git", "-C", TASK_REPO, "status", "--porcelain"])
    result["details"]["git_head"] = head["stdout_tail"].strip()
    result["details"]["git_status"] = status["stdout_tail"].strip()
    result["details"]["manifest_start_commit"] = manifest.get("start_commit", "")
    if head["rc"] != 0:
        result["errors"].append(f"git rev-parse failed: {head['stderr_tail']}")
    if status["stdout_tail"].strip():
        result["errors"].append("task repository working tree is not clean")
    if head["stdout_tail"].strip() != manifest.get("start_commit", ""):
        result["errors"].append(
            "manifest start_commit does not match the task repo HEAD"
        )

    repo_files = tree_files(TASK_REPO)
    baseline_files = tree_files(BASELINE_REPO)
    if repo_files != baseline_files:
        only_repo = sorted(set(repo_files) - set(baseline_files))
        only_baseline = sorted(set(baseline_files) - set(repo_files))
        changed = sorted(
            name for name in set(repo_files) & set(baseline_files)
            if repo_files[name] != baseline_files[name]
        )
        result["errors"].append(
            f"baseline_repo differs from task repo: only_repo={only_repo} "
            f"only_baseline={only_baseline} changed={changed}"
        )
    result["details"]["tracked_files"] = len(repo_files)
    result["passed"] = not result["errors"]
    return result


def prepare_workspace(destination: str) -> None:
    os.makedirs(destination, exist_ok=True)
    for entry in os.listdir(BASELINE_REPO):
        source = os.path.join(BASELINE_REPO, entry)
        target = os.path.join(destination, entry)
        if os.path.isdir(source):
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def grade_workspace(workspace: str, json_out: str) -> dict:
    run_result = run(
        [sys.executable, GRADE, "--workspace", workspace, "--json-out", json_out],
        timeout=400,
    )
    report = None
    try:
        with open(json_out, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        report = None
        run_result["report_error"] = str(exc)
    run_result["report"] = report
    return run_result


def verdict_signature(report: dict) -> tuple:
    cases = tuple(sorted((c["id"], bool(c["passed"])) for c in report.get("cases", [])))
    gates = tuple(sorted((g["id"], bool(g["passed"])) for g in report.get("gates", [])))
    return (report.get("status"), report.get("total"), cases, gates)


def run_target(kind: str, apply_script: str | None, label: str, temp_root: str,
               attempt: int) -> dict:
    workspace = os.path.join(temp_root, f"{label}-{attempt}")
    prepare_workspace(workspace)
    applied = None
    if apply_script:
        applied = run(["bash", apply_script, workspace], timeout=120)
        if applied["rc"] != 0:
            return {
                "label": label,
                "kind": kind,
                "attempt": attempt,
                "apply": applied,
                "error": "apply script failed",
            }
    report_path = os.path.join(temp_root, f"{label}-{attempt}.json")
    graded = grade_workspace(workspace, report_path)
    report = graded.get("report") or {}
    failed_gates = [g["id"] for g in report.get("gates", []) if not g["passed"]]
    return {
        "label": label,
        "kind": kind,
        "attempt": attempt,
        "apply": applied,
        "grade": {
            "rc": graded["rc"],
            "elapsed_s": graded["elapsed_s"],
            "stderr_tail": graded["stderr_tail"],
        },
        "status": report.get("status"),
        "total": report.get("total"),
        "failed_gates": failed_gates,
        "failed_cases": [c["id"] for c in report.get("cases", []) if not c["passed"]],
        "determinism": report.get("determinism"),
        "signature": verdict_signature(report) if report else None,
        "report_path": report_path,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the t3_coordinator grader")
    parser.add_argument("--quick", action="store_true",
                        help="oracle once, negatives once, no extra repeats")
    parser.add_argument("--report", default=os.path.join(HERE, "validation_report.json"))
    parser.add_argument("--keep", action="store_true", help="keep temporary workspaces")
    args = parser.parse_args(argv)

    started = time.monotonic()
    temp_root = tempfile.mkdtemp(prefix="t3-validate-")
    outcomes = []
    failures = []

    fixture = check_fixture_integrity()
    outcomes.append({"target": "fixture_integrity", "passed": fixture["passed"],
                     "detail": fixture})
    if not fixture["passed"]:
        failures.append("fixture integrity failed")

    # 1. pristine baseline must FAIL
    base = run_target("baseline", None, "baseline", temp_root, 0)
    base_ok = base.get("status") == "FAIL"
    outcomes.append({"target": "baseline", "passed": base_ok,
                     "detail": {k: base.get(k) for k in
                                ("status", "total", "failed_gates", "failed_cases")}})
    if not base_ok:
        failures.append(f"baseline expected FAIL, got {base.get('status')}")

    # 2. oracle must PASS, repeated for determinism
    oracle_repeats = 1 if args.quick else ORACLE_REPEATS
    oracle_runs = []
    for attempt in range(oracle_repeats):
        result = run_target("oracle", ORACLE, "oracle", temp_root, attempt)
        oracle_runs.append(result)
        ok = (
            result.get("status") == "PASS"
            and float(result.get("total") or 0) >= 80.0
            and not result.get("failed_gates")
        )
        outcomes.append({
            "target": f"oracle[run {attempt}]",
            "passed": ok,
            "detail": {k: result.get(k) for k in
                       ("status", "total", "failed_gates", "failed_cases")},
        })
        if not ok:
            failures.append(
                f"oracle run {attempt} expected PASS >=80 with all gates, got "
                f"{result.get('status')} {result.get('total')} "
                f"failed_gates={result.get('failed_gates')}"
            )
    oracle_signatures = {json.dumps(run["signature"]) for run in oracle_runs if run.get("signature")}
    oracle_agreement = len(oracle_signatures) == 1
    if not oracle_agreement:
        failures.append("oracle verdict signatures differ across runs")

    # 3. negatives must FAIL and trip their documented gate(s)
    negative_runs = []
    for index, name in enumerate(NEGATIVES):
        directory = os.path.join(NEGATIVE_ROOT, name)
        apply_script = os.path.join(directory, "apply.sh")
        if not os.path.exists(apply_script):
            failures.append(f"negative missing: {name}")
            continue
        with open(os.path.join(directory, "expected_gates.json"), "r",
                  encoding="utf-8") as handle:
            expectation = json.load(handle)
        expected = set(expectation["expected_failed_gates"])
        repeats = 1 if args.quick else max(1, EXTRA_REPEATS.get(name, 1))
        runs = []
        for attempt in range(repeats):
            result = run_target("negative", apply_script, name, temp_root, attempt)
            runs.append(result)
            failed = set(result.get("failed_gates") or [])
            missed = sorted(expected - failed)
            ok = result.get("status") == "FAIL" and not missed
            outcomes.append({
                "target": f"{name}[run {attempt}]",
                "passed": ok,
                "detail": {
                    "reason": expectation["reason"],
                    "status": result.get("status"),
                    "total": result.get("total"),
                    "failed_gates": sorted(failed),
                    "expected_gates": sorted(expected),
                    "missed_gates": missed,
                },
            })
            if not ok:
                failures.append(
                    f"{name} run {attempt}: expected FAIL with gates {sorted(expected)}, "
                    f"got status={result.get('status')} total={result.get('total')} "
                    f"failed_gates={sorted(failed)}"
                )
        negative_runs.append({"name": name, "expectation": expectation, "runs": runs})

    # 4. determinism summary
    determinism = {
        "oracle_repeats": oracle_repeats,
        "oracle_agreement": oracle_agreement,
        "oracle_signature": json.loads(sorted(oracle_signatures)[0]) if oracle_signatures else None,
        "negative_repeats": {
            name: (1 if args.quick else max(1, EXTRA_REPEATS.get(name, 1)))
            for name in NEGATIVES
        },
    }
    negative_stability = {}
    for entry in negative_runs:
        verdicts = {run.get("status") for run in entry["runs"]}
        gates = {tuple(sorted(run.get("failed_gates") or [])) for run in entry["runs"]}
        stable = len(verdicts) == 1 and len(gates) == 1
        negative_stability[entry["name"]] = {
            "stable": stable,
            "statuses": sorted(v for v in verdicts if v),
            "failed_gate_sets": [list(g) for g in gates],
        }
        if not stable:
            failures.append(f"{entry['name']} verdict was not stable across repeats")
    determinism["negative_stability"] = negative_stability

    report = {
        "task_id": "t3",
        "grader_version": json.load(open(MANIFEST, encoding="utf-8")).get("grader_version")
        if os.path.exists(MANIFEST) else "unknown",
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(time.monotonic() - started, 1),
        "passed": not failures,
        "failures": failures,
        "fixture_integrity": fixture,
        "baseline": {k: base.get(k) for k in
                     ("status", "total", "failed_gates", "failed_cases", "grade")},
        "oracle": {
            "runs": [{k: run.get(k) for k in
                      ("status", "total", "failed_gates", "failed_cases", "grade")}
                     for run in oracle_runs],
            "all_passed": all(o["passed"] for o in outcomes if o["target"].startswith("oracle")),
        },
        "negatives": [
            {
                "name": entry["name"],
                "reason": entry["expectation"]["reason"],
                "expected_failed_gates": entry["expectation"]["expected_failed_gates"],
                "runs": [{k: run.get(k) for k in
                          ("status", "total", "failed_gates", "failed_cases", "grade")}
                         for run in entry["runs"]],
            }
            for entry in negative_runs
        ],
        "determinism": determinism,
        "outcomes": outcomes,
    }
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)

    print("=" * 78)
    print(f"t3_coordinator validation -- {'PASS' if report['passed'] else 'FAIL'} "
          f"in {report['elapsed_s']}s")
    print("-" * 78)
    for outcome in outcomes:
        mark = "PASS" if outcome["passed"] else "FAIL"
        detail = outcome["detail"]
        summary = ""
        if isinstance(detail, dict):
            if "status" in detail:
                summary = (f"status={detail.get('status')} total={detail.get('total')} "
                           f"failed_gates={detail.get('failed_gates')}")
            elif "errors" in detail:
                summary = f"errors={detail['errors']}"
        print(f"  [{mark}] {outcome['target']:34s} {summary[:120]}")
    print("-" * 78)
    print(f"oracle determinism: agreement={determinism['oracle_agreement']} "
          f"repeats={determinism['oracle_repeats']}")
    for name, info in negative_stability.items():
        print(f"  {name:32s} stable={info['stable']} statuses={info['statuses']}")
    if failures:
        print("-" * 78)
        for failure in failures:
            print(f"  FAILURE: {failure}")
    print(f"report: {os.path.abspath(args.report)}")
    print("=" * 78)

    if not args.keep:
        shutil.rmtree(temp_root, ignore_errors=True)
    else:
        print(f"workspaces kept in {temp_root}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
