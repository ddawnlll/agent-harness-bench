#!/usr/bin/env python3
"""Generate the narrative deliverables from a completed run directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_report import build_data, write_csvs  # noqa: E402


def pct(v):
    return "—" if v is None else f"{v*100:.1f}%"


def fmt(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out-root", default=".")
    args = ap.parse_args()
    run_dir = os.path.abspath(args.run_dir)
    data = build_data(run_dir)
    write_csvs(run_dir, data)
    meta, verdict, econ = data["meta"], data["verdict"], data["economics"]

    os.makedirs(args.out_root, exist_ok=True)

    # ---------------------------------------------------------- verdict.json
    out_verdict = {
        "schema_version": 1,
        "experiment_id": meta["experiment_id"],
        "run_id": meta["run_id"],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "model": meta["model"], "provider": meta["provider"],
        "reasoning_effort": meta["reasoning_effort"],
        "recommendation": verdict["recommendation"],
        "confidence": verdict["confidence"],
        "n_trials": verdict["n_trials"], "n_tasks": verdict["n_tasks"],
        "ranking_by_verified_completions": verdict["ranking"],
        "per_agent": econ["per_agent"],
        "trials": [{
            "task_id": r["task_id"], "agent_id": r["agent_id"],
            "status": (r.get("grade") or {}).get("status") or r["status"],
            "verified_pass": r["verified_pass"],
            "score": (r.get("grade") or {}).get("total"),
            "gates": (r.get("grade") or {}).get("gates"),
            "elapsed_seconds": r.get("elapsed_seconds"),
            "cpu_seconds": r.get("cpu_seconds"),
            "peak_memory_bytes": r.get("peak_memory_bytes"),
            "tokens": r.get("tokens"),
            "nominal_cost_usd": r.get("nominal_cost_usd"),
            "diff_sha256": r.get("diff_sha256"),
            "evidence_dir": f"runs/{r['task_id']}/{r['agent_id']}",
        } for r in data["runs"]],
        "caveats": verdict["caveats"],
    }
    json.dump(out_verdict, open(os.path.join(run_dir, "verdict.json"), "w"), indent=2, sort_keys=True)

    # ----------------------------------------------------------- manifest.json
    task_shas = {}
    for rnd in (meta.get("tasks") and [] or []):
        pass
    manifest = {
        "schema_version": 1,
        "experiment_id": meta["experiment_id"], "run_id": meta["run_id"],
        "host": meta["host"], "model": meta["model"], "provider": meta["provider"],
        "reasoning_effort": meta["reasoning_effort"],
        "credential_source": meta["credential_source"],
        "started_utc": meta["started_utc"], "finished_utc": meta["finished_utc"],
        "report_generated_utc": meta["generated_utc"],
        "agents": meta["agents"], "tasks": meta["tasks"],
        "task_start_shas": {r["task_id"]: r.get("start_sha") for r in data["runs"]},
        "agent_versions": {r["agent_id"]: {"version": r.get("agent_version"), "binary_sha256": r.get("agent_binary_sha256")}
                            for r in data["runs"]},
        "pricing": {
            "basis": "provider-reported usage with OpenCode Go list_cost; subscription allowance reported separately",
            "note": "nominal list price is not the incremental cash charged to the subscription",
        },
        "hashes": {
            "report_html": "",  # filled by the caller
            "sha256sums": hashlib.sha256(open(os.path.join(run_dir, "SHA256SUMS"), "rb").read()).hexdigest()
            if os.path.exists(os.path.join(run_dir, "SHA256SUMS")) else None,
        },
        "integrity": data["integrity"],
        "verdict": verdict["recommendation"],
        "confidence": verdict["confidence"],
        "synthetic": meta.get("synthetic", False),
    }
    json.dump(manifest, open(os.path.join(run_dir, "manifest.json"), "w"), indent=2, sort_keys=True)

    # --------------------------------------------------------------- report.md
    lines = [f"# Agent Benchmark — Forensic Report", "",
             f"**Run:** `{meta['run_id']}` · **Experiment:** `{meta['experiment_id']}`  ",
             f"**Model:** `{meta['model']}` via `{meta['provider']}`, reasoning_effort `{meta['reasoning_effort']}`  ",
             f"**Design:** {len(meta['agents'])} agents × {len(meta['tasks'])} tasks, one trial per cell (n=1)  ",
             f"**Generated:** {meta['generated_utc']}", ""]
    if meta.get("synthetic"):
        lines += ["> **SYNTHETIC DEMO DATA — not a benchmark result.**", ""]
    if meta.get("empty_state"):
        lines += ["> **NO TRIALS EXECUTED — no recommendation is possible.**", ""]
    lines += [f"## Provisional decision: **{verdict['recommendation']}**", "",
              f"Confidence: **{verdict['confidence']}**. Best verified completions: "
              f"{verdict['best_verified_completions']}/{verdict['n_tasks']}.", "",
              "### Limitations (mandatory reading)", ""]
    lines += [f"- {c}" for c in verdict["caveats"]]
    lines += ["", "## Outcomes", "",
              "| Task | Agent | Status | Score | Verified | Elapsed s | CPU s | Peak MiB | Tokens | Nominal USD |",
              "|---|---|---|---:|---|---:|---:|---:|---:|---:|"]
    for r in data["runs"]:
        g = r.get("grade") or {}
        t = r.get("tokens") or {}
        lines.append(f"| {r['task_id']} | {r['agent_id']} | {g.get('status') or r['status']} | "
                     f"{fmt(g.get('total'))} | {'yes' if r['verified_pass'] else 'no'} | "
                     f"{fmt(r.get('elapsed_seconds'),1)} | {fmt(r.get('cpu_seconds'),3)} | "
                     f"{fmt((r.get('peak_memory_bytes') or 0)/1048576,1)} | {t.get('total')} | "
                     f"{fmt(r.get('nominal_cost_usd'),6)} |")
    lines += ["", "## Per-agent economics", "",
              "| Agent | Verified passes | Pass rate | Quality points | Nominal cost | Cost / verified | Cost / quality point | Mean success wall s |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for a in meta["agents"]:
        e = econ["per_agent"][a]
        lines.append(f"| {a} | {e['verified_passes']} | {pct(e['pass_rate'])} | {fmt(e['quality_points'])} | "
                     f"{fmt(e['total_cost_usd'],6)} | "
                     f"{'undefined' if e['cost_per_verified'] is None else fmt(e['cost_per_verified'],6)} | "
                     f"{'undefined' if e['cost_per_quality_point'] is None else fmt(e['cost_per_quality_point'],6)} | "
                     f"{fmt(e['mean_success_wall_seconds'],1)} |")
    lines += ["", "Costs are nominal provider list-price accounting, not incremental subscription cash spend. "
              "No value is reported as zero when it is undefined.", "",
              "## Integrity", ""]
    for c in data["integrity"]:
        lines.append(f"- **{c['id']}**: {c['status']} — {c['detail']}")
    lines += ["", "## Evidence", "",
              f"Raw telemetry, patches, grader receipts and per-trial logs are preserved under `{run_dir}` "
              "and sealed by `SHA256SUMS`. The interactive report embeds the normalized data and links every "
              "figure back to the trial directory.", "",
              "## What would change the recommendation", "",
              "- Additional tasks and repeated trials per agent-task (n>1).",
              "- Task families weighted toward the operator's actual workload.",
              "- Provider contention measurements at higher concurrency.",
              "- Independent blinded review of maintainability and residual risk.",
              "- Real incremental billing (rather than nominal list price) for subscription accounting."]
    open(os.path.join(args.out_root, "report.md"), "w").write("\n".join(lines) + "\n")

    # ------------------------------------------------- executive_summary.md
    ex = [f"# Executive Summary — {verdict['recommendation']}", "",
          f"Provisional decision after {verdict['n_trials']} trials on {verdict['n_tasks']} tasks. "
          f"Confidence: **{verdict['confidence']}**.", "",
          "| Agent | Verified passes | Quality points | Total wall s | Nominal USD | Cost / verified |",
          "|---|---:|---:|---:|---:|---:|"]
    for a in meta["agents"]:
        e = econ["per_agent"][a]
        total_wall = sum(r["elapsed_seconds"] or 0 for r in data["runs"] if r["agent_id"] == a)
        ex.append(f"| {a} | {e['verified_passes']}/{e['attempts']} | {fmt(e['quality_points'])} | "
                  f"{fmt(total_wall,0)} | {fmt(e['total_cost_usd'],6)} | "
                  f"{'undefined' if e['cost_per_verified'] is None else fmt(e['cost_per_verified'],6)} |")
    ex += ["", "## Decision", f"**{verdict['recommendation']}**", ""]
    ex += [f"- {c}" for c in verdict["caveats"]]
    ex += ["", "This is a screening exercise, not a statistically conclusive benchmark. "
           "A verified pass requires every P0 gate and a score of at least 80/100 from an independent grader." ]
    open(os.path.join(args.out_root, "executive_summary.md"), "w").write("\n".join(ex) + "\n")

    print(json.dumps({"recommendation": verdict["recommendation"],
                      "confidence": verdict["confidence"],
                      "agents": {a: econ["per_agent"][a]["verified_passes"] for a in meta["agents"]}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
