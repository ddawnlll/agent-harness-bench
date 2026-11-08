#!/usr/bin/env python3
"""Independent grader for Task 1: cross-module execution safety.

Deterministic tests are authoritative; nothing in this file trusts the
candidate's self-report. The candidate workspace is copied to a scratch
directory and never mutated.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HIDDEN = os.path.join(HERE, "hidden", "test_hidden.py")
BASELINE = os.path.join(HERE, "baseline_repo")

SAFETY = {"h01", "h02", "h03", "h07", "h08", "h09", "h10", "h11", "h13", "h14"}
CASES = [
    ("h01", "test_h01_protection_is_pending_before_venue_ack", 4, "pending_ack_state"),
    ("h02", "test_h02_accepted_stop_ack_activates_protection", 3, "venue_ack_required"),
    ("h03", "test_h03_rejected_stop_ack_fails_closed", 3, "fail_closed_on_rejection"),
    ("h04", "test_h04_partial_fills_accumulate", 4, "partial_fill_accumulation"),
    ("h05", "test_h05_duplicate_fill_applied_once", 4, "duplicate_fill_idempotency"),
    ("h06", "test_h06_protection_resized_after_additional_fill", 4, "coverage_resize"),
    ("h07", "test_h07_stale_callback_cannot_downgrade_active_protection", 3, "stale_callback_guard"),
    ("h08", "test_h08_protection_loss_callback_halts", 3, "fail_closed_on_loss"),
    ("h09", "test_h09_restart_does_not_promote_pending_protection", 4, "restart_state_fidelity"),
    ("h10", "test_h10_accepted_cancel_keeps_protection_for_open_position", 3, "cancel_race_protection"),
    ("h11", "test_h11_rejected_cancel_keeps_protection", 2, "cancel_reject_protection"),
    ("h12", "test_h12_fill_before_ack_is_applied", 3, "out_of_order_fill"),
    ("h13", "test_h13_native_identity_mismatch_fails_closed", 3, "native_identity"),
    ("h14", "test_h14_entry_blocked_while_protection_unresolved", 3, "entry_gate"),
    ("h15", "test_h15_risk_reducing_exit_allowed_while_unresolved", 2, "risk_reducing_exit"),
    ("h16", "test_h16_audit_deterministic_and_consistent", 2, "audit_consistency"),
]

GATES = [
    ("G1_PREMATURE_PROTECTION", ["h01", "h02"]),
    ("G2_FAIL_CLOSED", ["h03", "h14"]),
    ("G3_FILL_ATTRIBUTION", ["h04", "h05", "h06"]),
    ("G4_PROTECTION_LOSS", ["h07", "h08"]),
    ("G5_RESTART_FIDELITY", ["h09"]),
    ("G6_CANCEL_RACE", ["h10", "h11"]),
    ("G7_NATIVE_IDENTITY", ["h13"]),
    ("G8_TEST_INTEGRITY", []),   # computed from public tests + hashes
    ("G9_NO_SHORTCUT", []),      # computed from static checks
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def run_public(ws):
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ws, capture_output=True, text=True, timeout=180,
    )
    out = proc.stdout + proc.stderr
    ran = re.search(r"Ran (\d+) test", out)
    failed = re.search(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?\)", out)
    n_fail = 0
    if failed:
        n_fail = int(failed.group(1) or 0) + int(failed.group(2) or 0)
    elif proc.returncode != 0:
        n_fail = 1
    return {
        "returncode": proc.returncode,
        "tests_run": int(ran.group(1)) if ran else 0,
        "failures": n_fail,
        "passed": proc.returncode == 0,
        "output_tail": out[-3000:],
    }


def run_hidden(ws):
    tmp = tempfile.mkdtemp(prefix="t1hidden-")
    try:
        tf = os.path.join(tmp, "test_hidden.py")
        shutil.copy2(HIDDEN, tf)
        env = dict(os.environ)
        env["PYTHONPATH"] = ws
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "_hidden_runner.py"), tf],
            cwd=tmp, capture_output=True, text=True, timeout=300, env=env,
        )
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout)[-2000:], "cases": {}}
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception as exc:
            return {"ok": False, "error": f"runner output unparseable: {exc}", "cases": {}}
        data["ok"] = True
        return data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def python_files(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".venv"}]
        for fn in filenames:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                out[os.path.relpath(p, root)] = p
    return out


def diff_analysis(ws):
    base = python_files(BASELINE)
    cand = python_files(ws)
    changed, added, removed = [], [], []
    added_lines = deleted_lines = 0
    for rel, path in cand.items():
        if rel not in base:
            added.append(rel)
            added_lines += sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
            continue
        b = open(base[rel], encoding="utf-8", errors="replace").read().splitlines()
        c = open(path, encoding="utf-8", errors="replace").read().splitlines()
        if b != c:
            changed.append(rel)
            dl = list(difflib.unified_diff(b, c, lineterm=""))
            added_lines += sum(1 for l in dl if l.startswith("+") and not l.startswith("+++"))
            deleted_lines += sum(1 for l in dl if l.startswith("-") and not l.startswith("---"))
    for rel in base:
        if rel not in cand:
            removed.append(rel)
    return {"changed": sorted(changed), "added": sorted(added), "removed": sorted(removed),
            "added_lines": added_lines, "deleted_lines": deleted_lines}


def test_integrity(ws):
    base_tests = {rel: p for rel, p in python_files(BASELINE).items() if rel.startswith("tests/")}
    cand = python_files(ws)
    modified, removed = [], []
    for rel, p in base_tests.items():
        if rel not in cand:
            removed.append(rel)
        elif sha256(p) != sha256(cand[rel]):
            modified.append(rel)
    suspicious = []
    for rel in base_tests:
        if rel in cand:
            txt = open(cand[rel], encoding="utf-8", errors="replace").read()
            if "unittest.skip" in txt or "pytest.mark.skip" in txt:
                suspicious.append(f"{rel}:skip_decorator")
    new_test_files = [r for r in cand if r.startswith("tests/") and r not in base_tests]
    return {"modified": modified, "removed": removed, "suspicious": suspicious,
            "new_test_files": new_test_files}


def static_checks(ws, diff):
    findings = []
    net = re.compile(r"^\s*(?:import|from)\s+(socket|urllib|http\.client|requests|aiohttp)\b", re.M)
    debug = re.compile(r"\bprint\s*\(")
    hardcode = re.compile(r"(GHOST|STOP-E1-|test_h\d\d|F9\b)")
    for rel in diff["changed"] + diff["added"]:
        path = os.path.join(ws, rel)
        try:
            txt = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if net.search(txt):
            findings.append(f"{rel}:network_import")
        if rel.startswith("v9/") and hardcode.search(txt):
            findings.append(f"{rel}:fixture_hardcoding_pattern")
        if rel.startswith("v9/") and debug.search(txt):
            findings.append(f"{rel}:debug_print")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()

    ws = os.path.abspath(args.workspace)
    result = {
        "task_id": "t1",
        "grader_version": "1.0.0",
        "workspace": ws,
        "status": "INVALID",
        "total": 0.0,
        "categories": {},
        "gates": [],
        "cases": [],
        "metrics": {},
        "determinism": {},
        "evidence_refs": [],
        "notes": [],
    }
    if not os.path.isdir(ws):
        result["notes"].append("workspace missing")
        json.dump(result, open(args.json_out, "w"), indent=2)
        return 1

    tmp = tempfile.mkdtemp(prefix="t1cand-")
    try:
        cand = os.path.join(tmp, "ws")
        shutil.copytree(ws, cand, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        public = run_public(cand)
        hidden1 = run_hidden(cand)
        hidden2 = run_hidden(cand)
        diff = diff_analysis(cand)
        integrity = test_integrity(cand)
        static_findings = static_checks(cand, diff)

        if not hidden1.get("ok"):
            result["status"] = "INVALID"
            result["notes"].append("hidden suite failed to execute: " + str(hidden1.get("error"))[:800])
            json.dump(result, open(args.json_out, "w"), indent=2)
            return 1

        failed1 = hidden1.get("cases", {})
        failed2 = hidden2.get("cases", {})
        case_rows = []
        for cid, method, weight, req in CASES:
            key = f"test_hidden.Task1Acceptance.{method}"
            passed = key not in failed1
            passed2 = key not in failed2
            case_rows.append({
                "id": cid, "requirement": req, "severity": "safety" if cid in SAFETY else "correctness",
                "weight": weight, "passed": passed,
                "detail": (failed1.get(key) or "")[:400],
            })
        total_weight = sum(c[2] for c in CASES)
        cw = sum(c[2] for c in CASES if c[0] not in SAFETY)
        sw = sum(c[2] for c in CASES if c[0] in SAFETY)
        # correctness excludes the safety-gated cases to keep categories meaningful
        c_total = sum(c[2] for c in CASES if c[0] not in SAFETY)
        c_earned = sum(c[2] for c in CASES if c[0] not in SAFETY and
                       not any(r["id"] == c[0] and not r["passed"] for r in case_rows))
        s_earned = sum(c[2] for c in CASES if c[0] in SAFETY and
                       not any(r["id"] == c[0] and not r["passed"] for r in case_rows))

        correctness_pts = 50.0 * (c_earned / c_total) if c_total else 0.0
        safety_pts = 25.0 * (s_earned / sw) if sw else 0.0
        regression_pts = 10.0 * (1.0 if public["passed"] else 0.0)
        regression_pts += 5.0 if (integrity["new_test_files"] or diff["added"]) else 0.0
        maint = 0.0
        if not integrity["modified"] and not integrity["removed"]:
            maint += 2.0
        narrow = len(diff["changed"]) + len(diff["added"]) + len(diff["removed"]) <= 8
        if narrow and diff["added_lines"] <= 400:
            maint += 2.0
        if not any("debug_print" in f for f in static_findings):
            maint += 2.0
        if not any("fixture_hardcoding" in f or "network_import" in f for f in static_findings):
            maint += 2.0
        if any(r["id"] == "h16" and r["passed"] for r in case_rows):
            maint += 2.0
        # audit observability check through a direct smoke run
        observe_ok = False
        try:
            code = (
                "import json,sys;"
                "from v9 import Engine,RecordingBroker;"
                "e=Engine(RecordingBroker());"
                "e.run([{'type':'SUBMIT_ENTRY','seq':1,'order_id':'E1','symbol':'BTCUSD','side':'BUY','qty':1,'price':10.0},"
                "{'type':'ENTRY_ACK','seq':2,'order_id':'E1','native_id':'N1','accepted':True},"
                "{'type':'FILL','seq':3,'order_id':'E1','fill_id':'F1','qty':1,'price':10.0}]);"
                "print(int(any(r['type']=='PROTECTION_SUBMITTED' for r in e.audit.records)))"
            )
            env = dict(os.environ, PYTHONPATH=cand, PYTHONDONTWRITEBYTECODE="1")
            p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env)
            observe_ok = p.returncode == 0 and p.stdout.strip().endswith("1")
        except Exception:
            observe_ok = False
        if observe_ok:
            maint += 0.0
        else:
            result["notes"].append("audit observability smoke check failed")

        total = correctness_pts + safety_pts + regression_pts + maint * 1.0
        total = round(min(100.0, total), 2)

        gate_rows = []
        by_id = {r["id"]: r["passed"] for r in case_rows}
        for gid, deps in GATES:
            if gid == "G8_TEST_INTEGRITY":
                ok = public["passed"] and not integrity["modified"] and not integrity["removed"] and not integrity["suspicious"]
                detail = f"public_passed={public['passed']} modified={integrity['modified']} removed={integrity['removed']} suspicious={integrity['suspicious']}"
            elif gid == "G9_NO_SHORTCUT":
                ok = not static_findings
                detail = ";".join(static_findings) or "no static findings"
            else:
                ok = all(by_id.get(d, False) for d in deps)
                detail = "deps=" + ",".join(f"{d}:{'pass' if by_id.get(d) else 'fail'}" for d in deps)
            gate_rows.append({"id": gid, "severity": "P0", "passed": ok, "detail": detail,
                              "evidence": "hidden:" + ",".join(deps) if deps else "static"})

        determinism = {
            "repeats": 2,
            "agreement": failed1.keys() == failed2.keys(),
            "run1_failures": sorted(failed1.keys()),
            "run2_failures": sorted(failed2.keys()),
        }
        if not determinism["agreement"]:
            result["notes"].append("hidden suite is nondeterministic across repeats")

        all_gates = all(g["passed"] for g in gate_rows)
        status = "PASS" if (all_gates and total >= 80.0 and determinism["agreement"]) else "FAIL"
        if not determinism["agreement"]:
            status = "INVALID"

        result.update({
            "status": status,
            "total": total,
            "categories": {
                "correctness_adversarial": {"score": round(correctness_pts, 2), "max": 50},
                "safety_invariants": {"score": round(safety_pts, 2), "max": 25},
                "regression": {"score": round(regression_pts, 2), "max": 15},
                "maintainability": {"score": round(maint, 2), "max": 10},
            },
            "gates": gate_rows,
            "cases": case_rows,
            "metrics": {
                "public": public,
                "diff": diff,
                "integrity": integrity,
                "static_findings": static_findings,
                "hidden_tests_run": hidden1.get("tests_run"),
            },
            "determinism": determinism,
            "evidence_refs": ["hidden/test_hidden.py", "grade.py", "baseline_repo/"],
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    with open(args.json_out, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    print(f"[t1] status={result['status']} total={result['total']} "
          f"gates={'PASS' if all(g['passed'] for g in result['gates']) else 'FAIL'}")
    for g in result["gates"]:
        print(f"  gate {g['id']}: {'PASS' if g['passed'] else 'FAIL'} ({g['detail']})")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
