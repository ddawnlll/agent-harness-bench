#!/usr/bin/env python3
"""End-to-end publication verification. Writes publication-audit.json.

Fails if a result is inconsistent, a chart cannot be regenerated, a figure is
missing, the site is stale, the security gate fails, or a claim contradicts the
dataset.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "results" / "publication-data.json"
AUDIT = ROOT / "publication-audit.json"
AGENTS = ["pi-bolt", "jcode", "hax"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    checks = []

    def add(name, ok, detail):
        checks.append({"id": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    data = json.loads(DATA.read_text())
    trials = data["trials"]
    add("schema", data.get("schema") == "agent-harness-bench/public-data/1", data.get("schema", ""))
    add("trial_count", len(trials) == 9, f"{len(trials)} trials")
    add("trial_uniqueness", len({(t["task_id"], t["agent_id"]) for t in trials}) == 9,
        "one trial per task/agent")
    add("reconciliation", data.get("reconciliation", {}).get("status") == "OK",
        f"{len(data.get('reconciliation', {}).get('discrepancies', []))} discrepancies")
    add("seal", data.get("provenance", {}).get("seal", {}).get("ok") is True,
        f"{data.get('provenance', {}).get('seal', {}).get('checked')} files verified")

    # recompute aggregates from trials
    problems = []
    for agent in AGENTS:
        rows = [t for t in trials if t["agent_id"] == agent]
        agg = data["agents"][agent]
        passes = sum(1 for t in rows if t["verified_pass"])
        quality = round(sum(t["score"] for t in rows), 2)
        nominal = round(sum(t["tokens"]["list_cost_usd"] for t in rows), 9)
        tokens = sum(t["tokens"]["total"] for t in rows)
        if passes != agg["verified_passes"]:
            problems.append(f"{agent}: passes {passes} != {agg['verified_passes']}")
        if abs(quality - agg["quality_points"]) > 1e-6:
            problems.append(f"{agent}: quality {quality} != {agg['quality_points']}")
        if abs(nominal - agg["nominal_cost_usd"]) > 1e-6:
            problems.append(f"{agent}: cost {nominal} != {agg['nominal_cost_usd']}")
        if tokens != agg["tokens"]["total"]:
            problems.append(f"{agent}: tokens {tokens} != {agg['tokens']['total']}")
        cpv = nominal / passes if passes else None
        if cpv is None:
            if agg["cost_per_verified_usd"] is not None:
                problems.append(f"{agent}: cost/verified should be null")
        elif abs(cpv - agg["cost_per_verified_usd"]) > 1e-9:
            problems.append(f"{agent}: cost/verified mismatch")
        for t in rows:
            if not (0 <= float(t["score"]) <= 100):
                problems.append(f"{t['task_id']}/{agent}: score out of range")
            if t["verified_pass"] and float(t["score"]) < 80:
                problems.append(f"{t['task_id']}/{agent}: pass below threshold")
    add("aggregate_recompute", not problems, "; ".join(problems) or "all aggregates match trials")

    gates_ok = all(t["verified_pass"] == (t["status"] == "PASS") for t in trials)
    add("pass_semantics", gates_ok, "verified_pass mirrors grader status")

    ranking = sorted(AGENTS, key=lambda a: (-data["agents"][a]["verified_passes"],
                                            -data["agents"][a]["quality_points"]))
    add("verdict_rule", data["verdict"]["ranking"] == ranking,
        f"ranking {ranking}")

    # deterministic generators and structural checks
    for name, cmd in [
        ("charts_deterministic", [sys.executable, "tools/make_charts.py", "--check"]),
        ("svg_structure", [sys.executable, "tools/check_svg.py"]),
        ("contrast", [sys.executable, "tools/check_contrast.py"]),
        ("site_current", [sys.executable, "tools/build_site.py", "--check"]),
        ("publish_safety", [sys.executable, "tools/check_publish.py"]),
    ]:
        rc, out = run(cmd)
        add(name, rc == 0, out.splitlines()[-1] if out else "")

    # figures + embed integrity
    missing = [f"{n}-{t}.svg" for n in ["hero", "race", "safety", "timing", "economics", "resources"]
               for t in ["light", "dark"] if not (ROOT / "assets" / f"{n}-{t}.svg").exists()]
    add("figures_present", not missing, ", ".join(missing) or "12/12 present")
    add("report_present", (ROOT / "reports" / "AGENT_BENCHMARK_FORENSIC_REPORT.html").exists(),
        "sanitized forensic report")
    add("no_raw_runs", not list((ROOT / "results").glob("run-*")),
        "sealed run directories are not published")
    seal_lines = (ROOT / "results" / "SHA256SUMS").read_text().splitlines()
    seal_bad = []
    for line in seal_lines:
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        path = ROOT / "results" / rel
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            seal_bad.append(rel)
    add("results_seal", not seal_bad,
        f"{len(seal_lines)} files verified" if not seal_bad else ", ".join(seal_bad[:5]))

    # README must reference all six figures and the report
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing_refs = [n for n in ["hero", "race", "safety", "timing", "economics", "resources"]
                    if f"assets/{n}-light.svg" not in readme]
    add("readme_figures", not missing_refs, ", ".join(missing_refs) or "all figures referenced")
    add("readme_report", "reports/AGENT_BENCHMARK_FORENSIC_REPORT.html" in readme,
        "report linked from README")

    audit = {
        "schema": "agent-harness-bench/publication-audit/1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
        "checks": checks,
        "hashes": {
            "publication-data.json": sha(DATA),
            "README.md": sha(ROOT / "README.md"),
            "report": sha(ROOT / "reports" / "AGENT_BENCHMARK_FORENSIC_REPORT.html"),
            "report-seal": sha(ROOT / "reports" / "SEAL.json") if (ROOT / "reports" / "SEAL.json").exists() else None,
            "site/index.html": sha(ROOT / "site" / "index.html") if (ROOT / "site" / "index.html").exists() else None,
        },
    }
    AUDIT.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    width = max(len(c["id"]) for c in checks)
    for c in checks:
        print(f"{c['id']:<{width}}  {c['status']}  {c['detail'][:100]}")
    print(f"\nPUBLICATION AUDIT: {audit['status']} ({len(checks)} checks)")
    return 0 if audit["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
