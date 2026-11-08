#!/usr/bin/env python3
"""Validate the t2_replay_perf grader with its reference and negative controls.

    python3 validate.py [--repeats N] [--negatives-quick] [--no-oracle-repeat]

What it does
------------
1. exports a pristine copy of the task repository (``git archive HEAD``),
   grades it and checks that it FAILs with a low performance score;
2. applies ``oracle/apply_oracle.sh`` to a pristine copy, grades it and
   requires PASS, total >= 80 and a real large-profile speedup >= 3x;
3. grades the oracle several times and requires an identical verdict
   (determinism of the grader + candidate);
4. applies every ``negative/*/apply.sh`` control to a pristine copy, grades
   it and requires FAIL with the gate ids listed in the control's
   ``meta.json`` among the failed gates;
5. writes ``validation_report.json`` and exits non-zero if any expectation
   is unmet.

Every graded workspace is a fresh copy: the task repository is never mutated.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GRADE = os.path.join(HERE, "grade.py")
ORACLE = os.path.join(HERE, "oracle")
NEGATIVE_DIR = os.path.join(HERE, "negative")
REPORTS_DIR = os.path.join(HERE, "validation_reports")
DEFAULT_TASK_REPO = os.path.normpath(
    os.path.join(HERE, "..", "..", "tasks", "t2_replay_perf", "repo"))
PASS_THRESHOLD = 80.0


def log(message):
    print(message, flush=True)


def export_pristine(task_repo, destination):
    os.makedirs(destination, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", task_repo, "archive", "HEAD"],
        check=True, capture_output=True)
    extract = subprocess.run(
        ["tar", "-x", "-C", destination],
        input=archive.stdout, check=True, capture_output=True)
    if extract.returncode != 0:  # pragma: no cover - defensive
        raise RuntimeError("tar failed while exporting the task repository")
    if not os.path.isdir(os.path.join(destination, "replay")):
        raise RuntimeError("exported repository has no replay/ package")
    return destination


def apply_script(script, target):
    proc = subprocess.run([script, target], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("%s failed:\n%s\n%s"
                           % (script, proc.stdout[-2000:],
                              proc.stderr[-2000:]))
    return proc.stdout.strip()


def run_grader(workspace, json_out, work_dir, repeats):
    os.makedirs(os.path.dirname(json_out), exist_ok=True)
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, GRADE, "--workspace", workspace,
         "--json-out", json_out, "--work-dir", work_dir,
         "--repeats", str(repeats)],
        capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if not os.path.exists(json_out):
        raise RuntimeError("grader wrote no report (rc=%s)\n%s\n%s"
                           % (proc.returncode, proc.stdout[-3000:],
                              proc.stderr[-3000:]))
    with open(json_out, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    document["_grader_exit_code"] = proc.returncode
    document["_grader_elapsed_s"] = round(elapsed, 2)
    document["_grader_stdout_tail"] = proc.stdout[-4000:]
    return document


def failed_gate_ids(document):
    return [gate["id"] for gate in document.get("gates", [])
            if not gate["passed"]]


def summary(document):
    metrics = document.get("metrics", {})
    return {
        "status": document.get("status"),
        "total": document.get("total"),
        "speedup_large": metrics.get("speedup", {}).get("h_large"),
        "speedup_adversarial": metrics.get("speedup", {}).get(
            "h_adversarial"),
        "failed_gates": failed_gate_ids(document),
        "performance_score": document.get("categories", {}).get(
            "performance", {}).get("score"),
        "invalid_reason": document.get("invalid_reason"),
        "grader_exit_code": document.get("_grader_exit_code"),
        "grader_elapsed_s": document.get("_grader_elapsed_s"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task-repo", default=DEFAULT_TASK_REPO,
                        help="task repository (default: %(default)s)")
    parser.add_argument("--repeats", type=int, default=3,
                        help="measured timing repeats for baseline/oracle "
                             "(default 3)")
    parser.add_argument("--negative-repeats", type=int, default=1,
                        help="repeats used when grading negative controls "
                             "(default 1: they fail on gates, not timing)")
    parser.add_argument("--oracle-runs", type=int, default=3,
                        help="how often the oracle is graded to check "
                             "verdict determinism (default 3)")
    parser.add_argument("--only", default=None,
                        help="comma separated subset: baseline,oracle,neg*")
    parser.add_argument("--json-out", default=os.path.join(
        HERE, "validation_report.json"))
    args = parser.parse_args(argv)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="t2-validate-")
    results = {
        "task_id": "t2",
        "grader_version": read_grader_version(),
        "task_repo": os.path.abspath(args.task_repo),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "settings": {
            "repeats": args.repeats,
            "negative_repeats": args.negative_repeats,
            "oracle_runs": args.oracle_runs,
        },
        "baseline": None,
        "oracle": None,
        "negatives": [],
        "expectations_met": True,
        "failures": [],
    }
    only = None
    if args.only:
        only = {part.strip() for part in args.only.split(",") if part.strip()}

    def want(name):
        return only is None or name in only

    def fail(message):
        log("  ! %s" % message)
        results["expectations_met"] = False
        results["failures"].append(message)

    try:
        # ------------------------------------------------------------ baseline
        if want("baseline"):
            log("[1/4] grading the pristine baseline (expect FAIL, no speedup)")
            pristine = export_pristine(args.task_repo,
                                       os.path.join(tmp_root, "pristine"))
            report_path = os.path.join(REPORTS_DIR,
                                       "baseline_pristine.json")
            document = run_grader(pristine, report_path,
                                  os.path.join(tmp_root, "work_baseline"),
                                  args.repeats)
            info = summary(document)
            results["baseline"] = info
            log("      status=%s total=%.2f speedup_large=%s"
                % (info["status"], info["total"], info["speedup_large"]))
            if info["status"] != "FAIL":
                fail("pristine baseline must FAIL (got %s)" % info["status"])
            if info["total"] is None or info["total"] >= PASS_THRESHOLD:
                fail("pristine baseline total must be < %.0f (got %s)"
                     % (PASS_THRESHOLD, info["total"]))
            speedup = info["speedup_large"] or 0.0
            if not (0.5 <= speedup <= 1.6):
                fail("pristine baseline speedup should be ~1x (got %s)"
                     % speedup)
            if (info["performance_score"] or 0.0) > 2.0:
                fail("pristine baseline must score ~0 performance (got %s)"
                     % info["performance_score"])
            if info["failed_gates"]:
                fail("pristine baseline must not fail P0 gates (got %s)"
                     % info["failed_gates"])
            info["expectation_met"] = not results["failures"]

        # -------------------------------------------------------------- oracle
        if want("oracle"):
            runs = []
            total_runs = max(1, args.oracle_runs)
            for index in range(total_runs):
                label = "oracle" if total_runs == 1 else "oracle_%d" % (index + 1)
                log("[2/4] grading the oracle (%d/%d, repeats=%d)"
                    % (index + 1, total_runs, args.repeats))
                workspace = export_pristine(
                    args.task_repo, os.path.join(tmp_root, label))
                apply_script(os.path.join(ORACLE, "apply_oracle.sh"),
                             workspace)
                report_path = os.path.join(REPORTS_DIR, label + ".json")
                document = run_grader(workspace, report_path,
                                      os.path.join(tmp_root, "work_" + label),
                                      args.repeats)
                info = summary(document)
                runs.append(info)
                log("      status=%s total=%.2f speedup_large=%s "
                    "speedup_adv=%s" % (info["status"], info["total"],
                                       info["speedup_large"],
                                       info["speedup_adversarial"]))
                if info["status"] != "PASS":
                    fail("oracle run %d must PASS (got %s, failed gates %s)"
                         % (index + 1, info["status"],
                            info["failed_gates"]))
                if info["total"] is None or info["total"] < PASS_THRESHOLD:
                    fail("oracle run %d total must be >= %.0f (got %s)"
                         % (index + 1, PASS_THRESHOLD, info["total"]))
                if info["failed_gates"]:
                    fail("oracle run %d failed P0 gates: %s"
                         % (index + 1, info["failed_gates"]))
                if (info["speedup_large"] or 0.0) < 3.0:
                    fail("oracle run %d large speedup must be >= 3x (got %s)"
                         % (index + 1, info["speedup_large"]))
            # verdict determinism: same status and same failed gate ids
            verdict_set = {(info["status"],
                            tuple(sorted(info["failed_gates"])))
                           for info in runs}
            identical = len(verdict_set) == 1
            totals = [info["total"] for info in runs]
            results["oracle"] = {
                "runs": runs,
                "identical_verdicts": identical,
                "totals": totals,
                "speedups_large": [info["speedup_large"] for info in runs],
                "expected": {"status": "PASS", "min_total": PASS_THRESHOLD,
                             "min_speedup_large": 3.0},
                "expectation_met": True,
            }
            if not identical:
                fail("oracle verdicts differ between runs: %s"
                     % sorted(verdict_set))
            if any(info["status"] != "PASS" or
                   (info["total"] or 0) < PASS_THRESHOLD or
                   (info["speedup_large"] or 0) < 3.0 for info in runs):
                results["oracle"]["expectation_met"] = False

        # ----------------------------------------------------------- negatives
        if only is None or any(name.startswith("neg") or
                               name == "negatives" for name in only):
            controls = sorted(os.listdir(NEGATIVE_DIR))
            log("[3/4] grading %d negative controls" % len(controls))
            for control in controls:
                control_path = os.path.join(NEGATIVE_DIR, control)
                if not os.path.isdir(control_path):
                    continue
                if only is not None and not any(
                        control == o or control.startswith(o)
                        for o in only):
                    continue
                meta_path = os.path.join(control_path, "meta.json")
                with open(meta_path, "r", encoding="utf-8") as handle:
                    meta = json.load(handle)
                log("      %s: %s" % (control, meta["description"][:90]))
                workspace = export_pristine(
                    args.task_repo, os.path.join(tmp_root, control))
                apply_script(os.path.join(control_path, "apply.sh"),
                             workspace)
                report_path = os.path.join(REPORTS_DIR, control + ".json")
                document = run_grader(
                    workspace, report_path,
                    os.path.join(tmp_root, "work_" + control),
                    args.negative_repeats)
                info = summary(document)
                failed = set(info["failed_gates"])
                expected = set(meta.get("expected_failing_gates", []))
                met = (info["status"] == "FAIL" and expected.issubset(failed))
                if meta.get("expected_zero_performance"):
                    zero = info["performance_score"] in (0, 0.0)
                    met = met and zero
                else:
                    zero = None
                log("         status=%s total=%.2f failed_gates=%s "
                    "expectation_met=%s"
                    % (info["status"], info["total"], sorted(failed), met))
                entry = {
                    "id": control,
                    "description": meta["description"],
                    "status": info["status"],
                    "total": info["total"],
                    "failed_gates": sorted(failed),
                    "expected_failing_gates": sorted(expected),
                    "missing_expected_gates": sorted(expected - failed),
                    "performance_score": info["performance_score"],
                    "expected_zero_performance": meta.get(
                        "expected_zero_performance", False),
                    "speedup_large": info["speedup_large"],
                    "expectation_met": met,
                    "report_path": report_path,
                }
                results["negatives"].append(entry)
                if info["status"] != "FAIL":
                    fail("%s must FAIL (got %s)" % (control, info["status"]))
                if not expected.issubset(failed):
                    fail("%s did not fail the expected gates: missing %s "
                         "(failed: %s)" % (control,
                                           sorted(expected - failed),
                                           sorted(failed)))
                if meta.get("expected_zero_performance") and zero is False:
                    fail("%s must score 0 performance (got %s)"
                         % (control, info["performance_score"]))

        log("[4/4] writing %s" % args.json_out)
        results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime())
        results["work_dir"] = tmp_root
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, sort_keys=True)
            handle.write("\n")
    finally:
        if not os.environ.get("T2_KEEP_WORK"):
            shutil.rmtree(tmp_root, ignore_errors=True)

    log("")
    log("=" * 78)
    if results["baseline"]:
        log("baseline   status=%-6s total=%s speedup=%s"
            % (results["baseline"]["status"], results["baseline"]["total"],
               results["baseline"]["speedup_large"]))
    if results["oracle"]:
        log("oracle     %s  totals=%s speedups=%s identical_verdicts=%s"
            % ("; ".join(run["status"] for run in results["oracle"]["runs"]),
               results["oracle"]["totals"],
               results["oracle"]["speedups_large"],
               results["oracle"]["identical_verdicts"]))
    for entry in results["negatives"]:
        log("negative   %-30s status=%-6s expected=%s failed=%s met=%s"
            % (entry["id"], entry["status"],
               entry["expected_failing_gates"], entry["failed_gates"],
               entry["expectation_met"]))
    log("=" * 78)
    if results["expectations_met"]:
        log("VALIDATION OK: all expectations met")
        return 0
    log("VALIDATION FAILED:")
    for message in results["failures"]:
        log("  - %s" % message)
    return 1


def read_grader_version():
    path = os.path.join(HERE, "grade.py")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("GRADER_VERSION ="):
                    return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return None


if __name__ == "__main__":
    sys.exit(main())
