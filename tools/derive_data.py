#!/usr/bin/env python3
"""Derive the sanitized publication dataset from the sealed benchmark evidence.

Reads the original run directory (never modified), verifies its SHA256SUMS,
recomputes every published metric from primary records, redacts host-specific
identifiers, and writes deterministic public derivatives with a transformation
record.

Usage:
    python3 tools/derive_data.py --source ../agent-benchmark/results/run-... \
        --out results
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "agent-harness-bench/public-data/1"
AGENTS = ["pi-bolt", "jcode", "hax"]
TASKS = ["t1_order_lifecycle", "t2_replay_perf", "t3_coordinator"]
TASK_TITLES = {
    "t1_order_lifecycle": "Cross-module execution safety",
    "t2_replay_perf": "Replay performance under parity",
    "t3_coordinator": "Crash-safe job coordinator",
}
TASK_SHORT = {"t1_order_lifecycle": "T1", "t2_replay_perf": "T2", "t3_coordinator": "T3"}

SECRET_PATTERNS = [
    (re.compile(r"/home/[A-Za-z0-9._-]+"), "/home/<user>"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+"), "/Users/<user>"),
    (re.compile(r"12735\.example\.com"), "bench-host"),
    (re.compile(r"/sys/fs/cgroup/[^\s\"']+"), "<cgroup>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<redacted-key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<redacted-token>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "<redacted-token>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"), "Bearer <redacted>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<redacted-ip>"),
]


def sanitize(value):
    if isinstance(value, str):
        out = value
        for rx, repl in SECRET_PATTERNS:
            out = rx.sub(repl, out)
        return out
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    return value


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path):
    rows = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def verify_seal(run: Path):
    sums = run / "SHA256SUMS"
    if not sums.exists():
        return {"ok": False, "checked": 0, "mismatches": ["SHA256SUMS missing"], "seal_sha256": None}
    checked = 0
    mismatches = []
    for line in sums.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, rel = line.split("  ", 1)
        p = run / rel
        if not p.exists():
            mismatches.append(f"missing:{rel}")
            continue
        checked += 1
        if sha256_path(p) != digest:
            mismatches.append(f"hash:{rel}")
    return {"ok": not mismatches, "checked": checked, "mismatches": mismatches,
            "seal_sha256": sha256_path(sums)}


def cgroup_final_values(run: Path, task: str, agent: str):
    path = run / "runs" / task / agent / "cgroup_final.txt"
    out = {}
    if not path.exists():
        return out
    section = ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("---"):
            section = line[3:]
            continue
        if "=" in line and not section:
            k, v = line.split("=", 1)
            try:
                out[k] = int(v)
            except ValueError:
                pass
            continue
        parts = line.split()
        if len(parts) == 2 and section:
            try:
                out[f"{section}.{parts[0]}"] = int(parts[1])
            except ValueError:
                pass
        elif len(parts) == 1 and section:
            try:
                out[section] = int(parts[0])
            except ValueError:
                pass
    return out


def parse_tool_counts(agent: str, stdout_path: Path):
    """Counts only; no command text is published."""
    calls = 0
    failures = 0
    if not stdout_path.exists():
        return {"calls": 0, "failures": 0, "available": False}
    pending = {}
    try:
        with stdout_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if agent == "pi-bolt" and obj.get("type") == "tool_execution_start":
                    calls += 1
                    pending[obj.get("toolCallId")] = True
                elif agent == "pi-bolt" and obj.get("type") == "tool_execution_end":
                    text = json.dumps(obj.get("result") or {})[:2000]
                    if re.search(r"exited with code [1-9]|isError\"?\s*:\s*true", text):
                        failures += 1
                elif agent == "hax" and obj.get("kind") == "tool_call":
                    calls += 1
                    pending[obj.get("call_id")] = True
                elif agent == "hax" and obj.get("kind") == "tool_result":
                    out = str(obj.get("output") or "")
                    if re.search(r"(?i)^\s*(error|traceback)|\berror:", out[:400]):
                        failures += 1
    except OSError:
        return {"calls": 0, "failures": 0, "available": False}
    return {"calls": calls, "failures": failures, "available": calls > 0}


def downsample(points, limit=240):
    if len(points) <= limit:
        return points
    step = max(1, len(points) // limit)
    out = points[::step]
    if points[-1] not in out:
        out.append(points[-1])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    run = Path(args.source).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    seal = verify_seal(run)
    if not seal["ok"]:
        print(f"SEAL FAILURE: {seal['mismatches'][:5]}", file=sys.stderr)
        return 2

    summary = read_json(run / "summary_raw.json", {})
    env = read_json(run / "telemetry" / "environment.json", {})
    api_rows = read_jsonl(run / "telemetry" / "api_usage.jsonl")
    resource_rows = read_jsonl(run / "telemetry" / "resource_samples.jsonl")
    event_rows = read_jsonl(run / "telemetry" / "events.jsonl")

    host = summary.get("host") or env.get("host") or {}
    cfg = summary.get("config") or {}
    source_runs = {(r.get("task_id"), r.get("agent_id")): r for r in summary.get("runs", [])}

    # ------------------------------------------------------------ raw accounting
    api_by = defaultdict(list)
    for row in api_rows:
        if "chat/completions" in (row.get("path") or ""):
            api_by[(row.get("task_id"), row.get("agent_id"))].append(row)
    res_by = defaultdict(list)
    for row in resource_rows:
        res_by[(row.get("task_id"), row.get("agent_id"))].append(row)

    trials = []
    discrepancies = []
    for task in TASKS:
        for agent in AGENTS:
            src = source_runs.get((task, agent))
            if not src:
                return fail(out, f"missing summary run {task}/{agent}")
            grade = read_json(run / "runs" / task / agent / "hidden_grader.json", {}) or {}
            rows = sorted(api_by[(task, agent)], key=lambda r: r.get("utc_ns") or 0)
            tok = {
                "input": sum(int(r.get("input_tokens") or 0) for r in rows),
                "output": sum(int(r.get("output_tokens") or 0) for r in rows),
                "reasoning": sum(int(r.get("reasoning_tokens") or 0) for r in rows),
                "cached_read": sum(int(r.get("cached_read_tokens") or 0) for r in rows),
                "total": sum(int(r.get("total_tokens") or 0) for r in rows),
                "nominal_cost_usd": round(sum(float(r.get("provider_reported_cost_usd") or 0) for r in rows), 9),
                "list_cost_usd": round(sum(float(r.get("provider_list_cost_usd") or 0) for r in rows), 9),
                "allowance_usd": round(sum(float(r.get("provider_allowance_usd") or 0) for r in rows), 9),
                "requests": len(rows),
                "requests_without_usage": sum(1 for r in rows if r.get("usage_provenance") != "PROVIDER_REPORTED"),
                "api_service_seconds": round(sum(float(r.get("duration_ms") or 0) for r in rows) / 1000.0, 3),
                "models": sorted({r.get("model_id_returned") for r in rows if r.get("model_id_returned")}),
                "effort_forwarded": sorted({r.get("reasoning_effort_forwarded") for r in rows if r.get("reasoning_effort_forwarded")}),
            }
            summary_tokens = src.get("tokens") or {}
            for key, recomputed in (
                ("input", tok["input"]), ("output", tok["output"]), ("total", tok["total"]),
                ("reasoning", tok["reasoning"]), ("cached_read", tok["cached_read"]),
            ):
                claimed = summary_tokens.get(key)
                if claimed is not None and int(claimed) != recomputed:
                    discrepancies.append({"trial": f"{task}/{agent}", "field": f"tokens.{key}",
                                          "summary": claimed, "recomputed": recomputed})
            claimed_cost = summary_tokens.get("list_cost_usd")
            if claimed_cost is not None and abs(float(claimed_cost) - tok["list_cost_usd"]) > 1e-6:
                discrepancies.append({"trial": f"{task}/{agent}", "field": "tokens.list_cost_usd",
                                      "summary": claimed_cost, "recomputed": tok["list_cost_usd"]})

            final = cgroup_final_values(run, task, agent)
            cpu_us = final.get("cpu.stat.usage_usec")
            mem_peak = final.get("memory.peak")
            if cpu_us is None:
                samples = res_by[(task, agent)]
                cpu_us = max((s.get("cpu_usage_usec") or 0) for s in samples) if samples else None
                discrepancies.append({"trial": f"{task}/{agent}", "field": "cpu.source",
                                      "summary": "cgroup_final", "recomputed": "samples-fallback"})
            if mem_peak is None:
                samples = res_by[(task, agent)]
                mem_peak = max((s.get("memory_peak_bytes") or 0) for s in samples) if samples else None

            # derive elapsed from lifecycle events when possible
            elapsed = src.get("elapsed_seconds")
            st = sorted(res_by[(task, agent)], key=lambda r: r.get("monotonic_ns") or 0)
            samples = []
            if st:
                base = st[0].get("monotonic_ns") or 0
                for s in st:
                    samples.append({
                        "t": round(((s.get("monotonic_ns") or base) - base) / 1e9, 2),
                        "cpu_cores": None,
                        "mem_mb": round((s.get("memory_current_bytes") or 0) / 1048576, 2),
                        "pids": s.get("pids_current"),
                    })
                # CPU utilisation from deltas
                prev = None
                for src_s, dst_s in zip(st, samples):
                    if prev is not None:
                        dt = max(1e-6, ((src_s.get("monotonic_ns") or 0) - (prev.get("monotonic_ns") or 0)) / 1e9)
                        du = max(0, (src_s.get("cpu_usage_usec") or 0) - (prev.get("cpu_usage_usec") or 0))
                        dst_s["cpu_cores"] = round(du / 1e6 / dt, 3)
                    prev = src_s
                samples = downsample(samples)

            tool = parse_tool_counts(agent, run / "runs" / task / agent / "stdout.log")
            apiseries = []
            cum = 0
            for idx, r in enumerate(rows):
                cum += int(r.get("total_tokens") or 0)
                apiseries.append({
                    "i": idx,
                    "t_s": round((r.get("utc_ns") or 0) / 1e9, 3),
                    "duration_ms": r.get("duration_ms"),
                    "input": r.get("input_tokens"),
                    "output": r.get("output_tokens"),
                    "status": r.get("http_status"),
                    "cached": r.get("cached_read_tokens"),
                    "cumulative_tokens": cum,
                })
            grade = sanitize(grade)
            trials.append({
                "task_id": task,
                "task": TASK_SHORT[task],
                "task_title": TASK_TITLES[task],
                "agent_id": agent,
                "status": grade.get("status"),
                "verified_pass": grade.get("status") == "PASS",
                "score": grade.get("total"),
                "categories": grade.get("categories"),
                "gates": grade.get("gates"),
                "cases": grade.get("cases"),
                "determinism": grade.get("determinism"),
                "elapsed_s": round(float(elapsed), 3) if elapsed is not None else None,
                "api_service_s": tok["api_service_seconds"],
                "agent_busy_s": round(max(0.0, float(elapsed or 0) - tok["api_service_seconds"]), 3),
                "cpu_s": round((cpu_us or 0) / 1e6, 3),
                "cpu_user_s": round((final.get("cpu.stat.user_usec") or 0) / 1e6, 3),
                "cpu_system_s": round((final.get("cpu.stat.system_usec") or 0) / 1e6, 3),
                "cpu_throttled_s": round((final.get("cpu.stat.throttled_usec") or 0) / 1e6, 4),
                "peak_mem_bytes": mem_peak,
                "oom_events": final.get("memory.events.oom_kill", 0),
                "pids_peak": final.get("pids.events.max"),
                "tokens": tok,
                "requests": tok["requests"],
                "first_token_ms": src.get("first_token_ms"),
                "startup_ms": src.get("startup_ms"),
                "launch_delay_ms": src.get("launch_delay_ms"),
                "code": {
                    "files_changed": (src.get("snapshot") or {}).get("code_metrics", {}).get("files_changed"),
                    "lines_added": (src.get("snapshot") or {}).get("code_metrics", {}).get("lines_added"),
                    "lines_deleted": (src.get("snapshot") or {}).get("code_metrics", {}).get("lines_deleted"),
                    "test_files_touched": (src.get("snapshot") or {}).get("code_metrics", {}).get("test_files_touched"),
                    "diff_sha256": (src.get("snapshot") or {}).get("diff_sha256"),
                },
                "tools": tool,
                "agent_version": src.get("agent_version"),
                "agent_binary_sha256": src.get("agent_binary_sha256"),
                "cpu_affinity": src.get("cpu_affinity"),
                "exit_code": src.get("exit_code"),
                "exit_reason": src.get("exit_reason"),
                "round_id": src.get("round_id"),
                "start_sha": src.get("start_sha"),
                "speedup": (grade.get("metrics") or {}).get("speedup"),
                "baseline_median_ms": (grade.get("metrics") or {}).get("baseline_median_ms"),
                "candidate_median_ms": (grade.get("metrics") or {}).get("candidate_median_ms"),
                "peak_rss": (grade.get("metrics") or {}).get("peak_rss_bytes"),
                "resource_series": samples,
                "api_series": downsample(apiseries, 400),
            })

    # --------------------------------------------------------------- aggregates
    agents = {}
    for agent in AGENTS:
        ar = [t for t in trials if t["agent_id"] == agent]
        passes = [t for t in ar if t["verified_pass"]]
        quality = round(sum(float(t["score"] or 0) for t in ar), 2)
        nominal = round(sum(t["tokens"]["list_cost_usd"] for t in ar), 9)
        allowance = round(sum(t["tokens"]["allowance_usd"] for t in ar), 9)
        tokens_total = sum(t["tokens"]["total"] for t in ar)
        success_wall = [t["elapsed_s"] for t in passes if t["elapsed_s"]]
        mems = [t["peak_mem_bytes"] for t in ar if t["peak_mem_bytes"]]
        agents[agent] = {
            "id": agent,
            "version": ar[0]["agent_version"],
            "binary_sha256": ar[0]["agent_binary_sha256"],
            "attempts": len(ar),
            "verified_passes": len(passes),
            "pass_rate": round(len(passes) / len(ar), 4),
            "quality_points": quality,
            "total_wall_s": round(sum(t["elapsed_s"] or 0 for t in ar), 3),
            "mean_success_wall_s": round(sum(success_wall) / len(success_wall), 1) if success_wall else None,
            "fastest_trial_s": min((t["elapsed_s"] for t in ar if t["elapsed_s"]), default=None),
            "slowest_trial_s": max((t["elapsed_s"] for t in ar if t["elapsed_s"]), default=None),
            "total_cpu_s": round(sum(t["cpu_s"] for t in ar), 3),
            "mean_cpu_s": round(statistics.mean([t["cpu_s"] for t in ar]), 3),
            "mean_peak_mem_bytes": int(sum(mems) / len(mems)) if mems else None,
            "max_peak_mem_bytes": max(mems) if mems else None,
            "tokens": {
                "input": sum(t["tokens"]["input"] for t in ar),
                "output": sum(t["tokens"]["output"] for t in ar),
                "reasoning": sum(t["tokens"]["reasoning"] for t in ar),
                "cached_read": sum(t["tokens"]["cached_read"] for t in ar),
                "total": tokens_total,
            },
            "requests": sum(t["requests"] for t in ar),
            "nominal_cost_usd": nominal,
            "allowance_usd": allowance,
            "cost_per_verified_usd": round(nominal / len(passes), 9) if passes else None,
            "tokens_per_quality_point": round(tokens_total / quality, 1) if quality else None,
            "quality_per_million_tokens": round(quality / (tokens_total / 1e6), 2) if tokens_total else None,
        }

    ranking = sorted(AGENTS, key=lambda a: (-agents[a]["verified_passes"], -agents[a]["quality_points"]))
    best = agents[ranking[0]]["verified_passes"]
    top = [a for a in AGENTS if agents[a]["verified_passes"] == best]
    if best == 0:
        recommendation = "INSUFFICIENT_EVIDENCE"
    elif len(top) == 1:
        recommendation = top[0].upper().replace("-", "_")
    else:
        recommendation = "TASK_DEPENDENT"

    decision = {}
    eligible = [a for a in AGENTS if agents[a]["verified_passes"] > 0]
    if eligible:
        decision["fastest_verified"] = min(eligible, key=lambda a: agents[a]["mean_success_wall_s"] or 1e18)
        decision["best_quality"] = max(eligible, key=lambda a: agents[a]["quality_points"])
        decision["cheapest_verified"] = min(
            eligible, key=lambda a: agents[a]["cost_per_verified_usd"] if agents[a]["cost_per_verified_usd"] is not None else 1e18)
        decision["lowest_memory"] = min(eligible, key=lambda a: agents[a]["mean_peak_mem_bytes"] or 1e18)
        decision["best_balanced"] = recommendation.lower().replace("_", "-") if recommendation in (
            "PI_BOLT", "JCODE", "HAX") else "task-dependent"

    task_meta = {}
    for task in TASKS:
        t0 = next(t for t in trials if t["task_id"] == task)
        task_meta[task] = {
            "title": TASK_TITLES[task], "short": TASK_SHORT[task],
            "max_score": 100,
            "gates": [g.get("id") for g in (t0.get("gates") or [])],
            "weight_total": sum(v.get("max", 0) for v in (t0.get("categories") or {}).values()),
        }

    host_public = {
        "kernel": host.get("kernel"),
        "debian": host.get("debian_version"),
        "cpu_model": host.get("cpu_model"),
        "logical_cpus": host.get("logical_cpus"),
        "mem_total_bytes": host.get("mem_total_bytes"),
        "swap_total_bytes": host.get("swap_total_bytes"),
        "cgroup_controllers": host.get("cgroup_controllers"),
        "virtualization": "KVM guest",
        "hostname": "bench-host",
    }

    data = {
        "schema": SCHEMA,
        "experiment": {
            "id": (cfg.get("experiment_id") or summary.get("experiment_id")),
            "run_id": summary.get("run_id"),
            "started_utc": summary.get("started_utc"),
            "finished_utc": summary.get("finished_utc"),
            "measured_wall_s": round(float(summary.get("wall_seconds") or 0), 1),
            "model": cfg.get("model"),
            "provider": cfg.get("provider"),
            "reasoning_effort": cfg.get("reasoning_effort"),
            "design": "3 agents x 3 tasks x 1 trial; three concurrent agents per round",
            "agents": AGENTS,
            "tasks": TASKS,
            "host": host_public,
        },
        "verdict": {
            "recommendation": recommendation,
            "confidence": "LOW",
            "ranking": ranking,
            "verified_passes": {a: agents[a]["verified_passes"] for a in AGENTS},
            "decision_specific": decision,
            "headline": "JCode completed all three tasks; Pi-Bolt and Hax completed two. "
                        "The difference came from one critical stale-callback safety test.",
            "caveats": [
                "One trial per agent-task combination (n=1); no statistical superiority claim.",
                "Three tasks only; task-selection bias cannot be excluded.",
                "Shared VPS and provider capacity; latency is an outcome, not a controlled constant.",
                "Competitors shared one Unix user; isolation was cgroup/process based, not a security boundary.",
                "JCode's CLI emitted no tool-level trace; its tool activity is unknown.",
                "Nominal list-price cost is not incremental subscription cash spend.",
                "The benchmark was built by the Pi-Bolt author; grader/controls were independently validated but authorship is a confounder.",
            ],
        },
        "agents": agents,
        "tasks": task_meta,
        "trials": trials,
        "provenance": {
            "source_run_id": summary.get("run_id"),
            "source_dir": "<sealed-run>",
            "seal": seal,
            "generated_utc": "1970-01-01T00:00:00Z",
            "transformation": "tools/derive_data.py",
            "redactions": [
                "absolute home paths -> /home/<user>",
                "hostname -> bench-host",
                "cgroup paths -> <cgroup>",
                "IP addresses -> <redacted-ip>",
                "credential-shaped strings -> <redacted-*>",
            ],
        },
        "reconciliation": {
            "status": "OK" if not discrepancies else "DISCREPANCIES",
            "discrepancies": discrepancies,
            "checked_fields": ["tokens.input", "tokens.output", "tokens.total",
                               "tokens.reasoning", "tokens.cached_read", "tokens.list_cost_usd",
                               "cpu (cgroup final vs fallback)", "elapsed (summary)"],
        },
    }

    write_json(out / "publication-data.json", data)
    write_sanitized_telemetry(run, out, api_rows, resource_rows, event_rows)
    write_transform(out, run, seal)
    print(f"data: {len(trials)} trials, seal ok ({seal['checked']} files), "
          f"discrepancies={len(discrepancies)}, verdict={recommendation}")
    return 0


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_sanitized_telemetry(run: Path, out: Path, api_rows, resource_rows, event_rows):
    tel = out / "telemetry"
    tel.mkdir(parents=True, exist_ok=True)
    # API: drop provider request ids and session ids; keep measurements only.
    with (tel / "api_usage.jsonl").open("w", encoding="utf-8") as fh:
        for r in api_rows:
            keep = {
                "task_id": r.get("task_id"), "agent_id": r.get("agent_id"),
                "utc_ns": r.get("utc_ns"), "path": r.get("path"),
                "model_id_returned": r.get("model_id_returned"),
                "reasoning_effort_requested": r.get("reasoning_effort_requested"),
                "reasoning_effort_forwarded": r.get("reasoning_effort_forwarded"),
                "reasoning_effort_rewritten": r.get("reasoning_effort_rewritten"),
                "http_status": r.get("http_status"), "duration_ms": r.get("duration_ms"),
                "ttfb_ms": r.get("ttfb_ms"), "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"), "reasoning_tokens": r.get("reasoning_tokens"),
                "cached_read_tokens": r.get("cached_read_tokens"),
                "cached_write_tokens": r.get("cached_write_tokens"),
                "total_tokens": r.get("total_tokens"),
                "provider_reported_cost_usd": r.get("provider_reported_cost_usd"),
                "provider_list_cost_usd": r.get("provider_list_cost_usd"),
                "provider_allowance_usd": r.get("provider_allowance_usd"),
                "usage_provenance": r.get("usage_provenance"),
                "stream": r.get("stream"), "finish_reason": r.get("finish_reason"),
                "billing_source": r.get("billing_source"),
            }
            fh.write(json.dumps(sanitize(keep), sort_keys=True) + "\n")
    # Resource samples: drop io device stats and host-load corner values.
    with (tel / "resource_samples.jsonl").open("w", encoding="utf-8") as fh:
        for r in resource_rows:
            keep = {
                "task_id": r.get("task_id"), "agent_id": r.get("agent_id"),
                "monotonic_ns": r.get("monotonic_ns"), "sample_seq": r.get("sample_seq"),
                "cpu_usage_usec": r.get("cpu_usage_usec"), "cpu_user_usec": r.get("cpu_user_usec"),
                "cpu_system_usec": r.get("cpu_system_usec"),
                "nr_throttled": r.get("nr_throttled"), "throttled_usec": r.get("throttled_usec"),
                "memory_current_bytes": r.get("memory_current_bytes"),
                "memory_peak_bytes": r.get("memory_peak_bytes"),
                "memory_anon_bytes": r.get("memory_anon_bytes"),
                "pids_current": r.get("pids_current"), "pids_peak": r.get("pids_peak"),
                "oom_events": r.get("oom_events"),
                "cpu_pressure": r.get("cpu_pressure"), "memory_pressure": r.get("memory_pressure"),
                "host_load1": r.get("host_load1"), "host_cpu_steal_usec": r.get("host_cpu_steal_usec"),
            }
            fh.write(json.dumps(sanitize(keep), sort_keys=True) + "\n")
    # Lifecycle events: strip cgroup paths and unit names.
    with (tel / "events.jsonl").open("w", encoding="utf-8") as fh:
        for r in event_rows:
            detail = sanitize(r.get("detail") or {})
            for k in ("workspace", "cgroup", "unit", "pid", "relay", "grader_workspace"):
                if isinstance(detail, dict):
                    detail.pop(k, None)
            keep = {"event_type": r.get("event_type"), "task_id": r.get("task_id"),
                    "agent_id": r.get("agent_id"), "monotonic_ns": r.get("monotonic_ns"),
                    "utc_ns": r.get("utc_ns"), "detail": detail}
            fh.write(json.dumps(sanitize(keep), sort_keys=True) + "\n")


def write_transform(out: Path, run: Path, seal: dict):
    files = {}
    for rel in [
        "summary_raw.json", "telemetry/api_usage.jsonl", "telemetry/resource_samples.jsonl",
        "telemetry/events.jsonl", "telemetry/environment.json", "verdict.json",
        "AGENT_BENCHMARK_FORENSIC_REPORT.html",
    ]:
        p = run / rel
        if p.exists():
            files[rel] = {"sha256": sha256_path(p), "bytes": p.stat().st_size}
    for task in TASKS:
        for agent in AGENTS:
            p = run / "runs" / task / agent / "hidden_grader.json"
            if p.exists():
                rel = str(p.relative_to(run))
                files[rel] = {"sha256": sha256_path(p), "bytes": p.stat().st_size}
    write_json(out / "TRANSFORM.json", {
        "schema": "agent-harness-bench/transform/1",
        "source_run": run.name,
        "source_seal": seal,
        "source_files": files,
        "derivations": [
            "trials: summary_raw.json + runs/<task>/<agent>/hidden_grader.json",
            "resource totals: runs/<task>/<agent>/cgroup_final.txt (samples as fallback)",
            "tokens/cost: telemetry/api_usage.jsonl chat-completions rows",
            "tool counts: native stdout traces (Pi-Bolt/Hax only), counts only",
            "aggregates: recomputed, not copied from verdict.json",
        ],
        "redactions": "see tools/derive_data.py SECRET_PATTERNS",
        "published_derivatives": {
            "results/publication-data.json": sha256_path(out / "publication-data.json"),
            "results/telemetry/api_usage.jsonl": sha256_path(out / "telemetry" / "api_usage.jsonl"),
            "results/telemetry/resource_samples.jsonl": sha256_path(out / "telemetry" / "resource_samples.jsonl"),
            "results/telemetry/events.jsonl": sha256_path(out / "telemetry" / "events.jsonl"),
        },
    })


def fail(out: Path, message: str):
    write_json(out / "DERIVE_ERROR.json", {"status": "FAIL", "message": message})
    print("DERIVE FAILURE:", message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
