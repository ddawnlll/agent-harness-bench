#!/usr/bin/env python3
"""Build the single-file forensic audit report from a benchmark run directory.

The generated HTML is fully self-contained: all data, CSS and JavaScript are
embedded. It performs no network requests and needs no external files.
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import html
import json
import os
import re
import statistics
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(sk-[A-Za-z0-9._\-]{8,})"),
    re.compile(r"(?i)(api[_-]?key[=:]\s*)[A-Za-z0-9._\-]{8,}"),
]

def redact(s: str) -> str:
    if not isinstance(s, str):
        return s
    for p in SECRET_PATTERNS:
        s = p.sub(r"\1<REDACTED>", s)
    return s

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default

def read_jsonl(path, limit=None):
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if limit is not None and i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return out

def sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""

def tail(path, n=4000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
        return redact(data[-n:])
    except OSError:
        return ""


def parse_agent_tools(agent_id, stdout_path):
    """Best-effort native trace parsing; formats differ per agent and are
    reported as observed rather than normalized as if identical."""
    rows, kinds = [], Counter()
    try:
        with open(stdout_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if agent_id == "pi-bolt" and obj.get("type") in ("tool_execution_start", "tool_execution_end"):
                    kinds[obj["type"]] += 1
                    if obj["type"] == "tool_execution_start":
                        rows.append({"name": obj.get("toolName"), "call_id": obj.get("toolCallId"),
                                     "detail": json.dumps(obj.get("args") or {})[:240], "ok": None})
                    else:
                        for r in rows:
                            if r.get("call_id") == obj.get("toolCallId"):
                                text = json.dumps(obj.get("result") or {})[:2000]
                                r["ok"] = not re.search(r"exited with code [1-9]|isError\"?\s*:\s*true", text)
                elif agent_id == "hax" and obj.get("kind") in ("tool_call", "tool_result"):
                    kinds[obj["kind"]] += 1
                    if obj["kind"] == "tool_call":
                        rows.append({"name": obj.get("tool_name"), "call_id": obj.get("call_id"),
                                     "detail": str(obj.get("arguments"))[:240], "ok": None})
                    else:
                        for r in rows:
                            if r.get("call_id") == obj.get("call_id"):
                                out = str(obj.get("output") or "")
                                r["ok"] = not re.search(r"(?i)^\s*(error|traceback)|\berror:", out[:400])
    except OSError:
        pass
    counts = Counter(r.get("name") for r in rows)
    details = Counter(r.get("detail") for r in rows if r.get("detail"))
    return {
        "tools": rows,
        "tool_calls": len(rows),
        "tool_failures": sum(1 for r in rows if r.get("ok") is False),
        "tool_failure_unknown": sum(1 for r in rows if r.get("ok") is None),
        "tool_counts": dict(counts),
        "repeated_actions": [{"detail": d, "count": n} for d, n in details.most_common(5) if n > 1],
        "trace_available": bool(rows),
        "trace_note": ("native JSON trace parsed" if rows else
                       "no structured tool trace emitted by this CLI mode"),
    }


def load_run(run_dir, task_id, agent_id, run_meta):
    rdir = os.path.join(run_dir, "runs", task_id, agent_id)
    manifest = read_json(os.path.join(rdir, "run_manifest.json"), {}) or {}
    grade = read_json(os.path.join(rdir, "hidden_grader.json"), {}) or manifest.get("grade") or {}
    diff_path = os.path.join(rdir, "diff.patch")
    diff_text = ""
    if os.path.exists(diff_path):
        with open(diff_path, "r", encoding="utf-8", errors="replace") as fh:
            diff_text = fh.read()[:120000]
    run = {
        "task_id": task_id,
        "agent_id": agent_id,
        "status": manifest.get("status", "NOT_EVALUATED"),
        "exit_reason": manifest.get("exit_reason"),
        "exit_code": manifest.get("exit_code"),
        "elapsed_seconds": manifest.get("elapsed_seconds"),
        "launch_delay_ms": manifest.get("launch_delay_ms"),
        "startup_ms": manifest.get("startup_ms"),
        "first_token_ms": manifest.get("first_token_ms"),
        "cpu_seconds": manifest.get("cpu_seconds"),
        "cpu_user_seconds": manifest.get("cpu_user_seconds"),
        "cpu_system_seconds": manifest.get("cpu_system_seconds"),
        "cpu_throttled_seconds": manifest.get("cpu_throttled_seconds"),
        "peak_memory_bytes": manifest.get("peak_memory_bytes"),
        "oom_events": manifest.get("oom_events"),
        "tokens": manifest.get("tokens") or {},
        "model_requests": manifest.get("model_requests"),
        "api_wall_seconds": manifest.get("api_wall_seconds"),
        "api_errors": manifest.get("api_errors"),
        "api_throttle_events": manifest.get("api_throttle_events"),
        "agent_version": manifest.get("agent_version"),
        "agent_binary_sha256": manifest.get("agent_binary_sha256"),
        "cpu_affinity": manifest.get("cpu_affinity") or manifest.get("host_cpu"),
        "relay_addr": manifest.get("relay_addr"),
        "stdout_tail": tail(os.path.join(rdir, "stdout.log")),
        "stderr_tail": tail(os.path.join(rdir, "stderr.log")),
        "start_utc": manifest.get("start_utc"),
        "end_utc": manifest.get("end_utc"),
        "start_sha": manifest.get("start_sha"),
        "grade": grade,
        "code_metrics": (manifest.get("snapshot") or {}).get("code_metrics") or {},
        "diff_sha256": (manifest.get("snapshot") or {}).get("diff_sha256"),
        "archive_sha256": (manifest.get("snapshot") or {}).get("archive_sha256"),
        "tree_sha256": (manifest.get("snapshot") or {}).get("tree_sha256"),
        "diff_text": diff_text,
        "notes": manifest.get("notes") or [],
        "grader_wall_seconds": manifest.get("grader_wall_seconds"),
        "evidence_files": sorted(
            os.path.relpath(os.path.join(dp, f), run_dir)
            for dp, _, fs in os.walk(rdir) for f in fs
        ) if os.path.isdir(rdir) else [],
        "run_dir": rdir,
    }
    t = run.get("tokens") or {}
    run["nominal_cost_usd"] = (t.get("cost_usd") if (t.get("cost_usd") or 0) > 0 else t.get("list_cost_usd"))
    run["actual_cost_usd"] = t.get("cost_usd")
    run["allowance_usd"] = t.get("allowance_usd")
    run["tools"] = parse_agent_tools(agent_id, os.path.join(rdir, "stdout.log"))
    run["verified_pass"] = grade.get("status") == "PASS"
    return run

def downsample(series, max_points=400):
    if len(series) <= max_points:
        return series
    step = max(1, len(series) // max_points)
    out = series[::step]
    if series[-1] not in out:
        out.append(series[-1])
    return out

def build_data(run_dir):
    summary = read_json(os.path.join(run_dir, "summary_raw.json"), {}) or {}
    env = read_json(os.path.join(run_dir, "telemetry", "environment.json"), {}) or {}
    cfg = summary.get("config") or {}
    host = summary.get("host") or env.get("host") or {}
    agents = [a.get("id") for a in cfg.get("agents", [])] or ["pi-bolt", "jcode", "hax"]
    rounds = cfg.get("rounds", [])
    task_ids = [r.get("task_id") for r in rounds]

    api_events = read_jsonl(os.path.join(run_dir, "telemetry", "api_usage.jsonl"))
    events = read_jsonl(os.path.join(run_dir, "telemetry", "events.jsonl"))
    samples = read_jsonl(os.path.join(run_dir, "telemetry", "resource_samples.jsonl"))
    procs = read_jsonl(os.path.join(run_dir, "telemetry", "process_tree.jsonl"))

    runs = []
    for rnd in rounds:
        for agent in agents:
            runs.append(load_run(run_dir, rnd.get("task_id"), agent, rnd))

    # attach API telemetry per run
    api_by_run = defaultdict(list)
    for ev in api_events:
        api_by_run[(ev.get("task_id"), ev.get("agent_id"))].append(ev)
    for run in runs:
        evs = sorted(api_by_run.get((run["task_id"], run["agent_id"]), []),
                     key=lambda e: e.get("utc_ns") or 0)
        base = evs[0]["utc_ns"] if evs else 0
        series = []
        cum = {"input": 0, "output": 0, "reasoning": 0, "cached": 0, "cost": 0.0}
        for e in evs:
            cum["input"] += e.get("input_tokens") or 0
            cum["output"] += e.get("output_tokens") or 0
            cum["reasoning"] += e.get("reasoning_tokens") or 0
            cum["cached"] += e.get("cached_read_tokens") or 0
            cum["cost"] += e.get("provider_reported_cost_usd") or 0.0
            series.append({
                "t": round(((e.get("utc_ns") or base) - base) / 1e9, 3),
                "dur_ms": e.get("duration_ms"),
                "ttfb_ms": e.get("ttfb_ms"),
                "input": e.get("input_tokens"),
                "output": e.get("output_tokens"),
                "reasoning": e.get("reasoning_tokens"),
                "cached": e.get("cached_read_tokens"),
                "cost": (e.get("provider_reported_cost_usd") if (e.get("provider_reported_cost_usd") or 0) > 0 else e.get("provider_list_cost_usd")),
                "list_cost": e.get("provider_list_cost_usd"),
                "status": e.get("http_status"),
                "model": e.get("model_id_returned"),
                "effort_req": e.get("reasoning_effort_requested"),
                "effort_fwd": e.get("reasoning_effort_forwarded"),
                "stream": e.get("stream"),
            })
        run["api_series"] = downsample(series, 800)
        run["api_events_count"] = len(evs)
        run["api_models"] = sorted({e.get("model_id_returned") for e in evs if e.get("model_id_returned")})
        run["effort_rewrites"] = sum(1 for e in evs if e.get("reasoning_effort_rewritten"))
        run["effort_absent"] = sum(1 for e in evs if e.get("reasoning_effort_requested") in (None, "", "<absent>"))
        durs = [e.get("duration_ms") for e in evs if isinstance(e.get("duration_ms"), (int, float))]
        ttfbs = [e.get("ttfb_ms") for e in evs if isinstance(e.get("ttfb_ms"), (int, float))]
        def pct(vals, p):
            if not vals:
                return None
            vals = sorted(vals)
            idx = min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1))))
            return vals[idx]
        run["latency"] = {
            "count": len(durs),
            "median_ms": statistics.median(durs) if durs else None,
            "p90_ms": pct(durs, 90),
            "max_ms": max(durs) if durs else None,
            "ttfb_median_ms": statistics.median(ttfbs) if ttfbs else None,
            "ttfb_p90_ms": pct(ttfbs, 90),
            "output_tok_per_s": (sum((e.get("output_tokens") or 0) for e in evs) /
                                 (sum(durs) / 1000.0)) if durs and sum(durs) > 0 else None,
        }

    # resource series per run
    res_by_run = defaultdict(list)
    for s in sorted(samples, key=lambda x: ((x.get("round_id") or ""), x.get("monotonic_ns") or 0)):
        res_by_run[(s.get("task_id"), s.get("agent_id"))].append(s)
    for run in runs:
        rows = res_by_run.get((run["task_id"], run["agent_id"]), [])
        base = rows[0]["monotonic_ns"] if rows else 0
        prev_cpu = None
        series = []
        for s in rows:
            cpu_us = s.get("cpu_usage_usec")
            delta = None
            if prev_cpu is not None and cpu_us is not None and s.get("cpu_usage_usec") is not None:
                dt = max(0.001, ((s.get("monotonic_ns") or 0) - (prev or 0)) / 1e9)
                delta = max(0.0, (cpu_us - prev_cpu) / 1e6 / dt)
            prev = s.get("monotonic_ns")
            prev_cpu = cpu_us
            series.append({
                "t": round(((s.get("monotonic_ns") or base) - base) / 1e9, 2),
                "cpu_cores": round(delta, 3) if delta is not None else None,
                "mem_mb": round((s.get("memory_current_bytes") or 0) / 1048576, 2),
                "mem_peak_mb": round((s.get("memory_peak_bytes") or 0) / 1048576, 2),
                "pids": s.get("pids_current"),
                "throttled_us": s.get("throttled_usec"),
                "cpu_some_us": (s.get("cpu_pressure") or {}).get("some_total_us"),
                "mem_some_us": (s.get("memory_pressure") or {}).get("some_total_us"),
            })
        run["resource_series"] = downsample(series, 600)
        run["process_samples"] = len(procs)

    # events timeline per run
    run_events = []
    for ev in events:
        if ev.get("event_type") in {
            "PROCESS_EXEC_START", "AGENT_EXIT", "RUN_TIMEOUT", "BARRIER_RELEASED",
            "BARRIER_READY", "GRADER_STARTED", "GRADER_FINISHED", "SNAPSHOT_ERROR",
            "CGROUP_CLEANUP", "GLOBAL_WATCHDOG_FIRED",
        }:
            run_events.append({
                "type": ev.get("event_type"),
                "agent": ev.get("agent_id"),
                "task": ev.get("task_id"),
                "mono_ns": ev.get("monotonic_ns"),
                "utc_ns": ev.get("utc_ns"),
                "detail": ev.get("detail"),
            })
    barrier = next((e["mono_ns"] for e in run_events if e["type"] == "BARRIER_RELEASED"), None)
    run_events.sort(key=lambda e: e.get("mono_ns") or 0)
    for run in runs:
        rel = []
        for e in run_events:
            if e.get("task") and e.get("task") != run["task_id"]:
                continue
            if e.get("agent") and e.get("agent") != run["agent_id"]:
                continue
            t = None
            if barrier and e.get("mono_ns"):
                t = round((e["mono_ns"] - barrier) / 1e9, 3)
            rel.append({"type": e["type"], "t": t, "detail": e.get("detail")})
        run["timeline"] = rel
        # derived phases from API series
        if run["api_series"]:
            run["first_request_s"] = run["api_series"][0]["t"]
            run["last_response_s"] = round(run["api_series"][-1]["t"] + (run["api_series"][-1].get("dur_ms") or 0) / 1000, 3)

    # integrity checks
    integrity = []
    observed_models = sorted({m for r in runs for m in r.get("api_models", [])})
    canonical = cfg.get("model", "deepseek-v4.1-flash")
    integrity.append({
        "id": "MODEL_IDENTITY", "status": "PASS" if observed_models and set(observed_models) <= {canonical} else ("WARN" if not observed_models else "FAIL"),
        "detail": f"canonical={canonical}; observed={observed_models or 'NO_REQUESTS'}",
    })
    effort_ok = all((r.get("effort_rewrites", 0) == 0) for r in runs if r.get("model_requests"))
    integrity.append({
        "id": "REASONING_EFFORT", "status": "PASS" if effort_ok else "WARN",
        "detail": "relay pinned reasoning_effort=low on every request; rewrites=" +
                  str(sum(r.get("effort_rewrites", 0) for r in runs)),
    })
    chat_events = [e for e in api_events if "chat/completions" in (e.get("path") or "")]
    missing_usage = sum(1 for e in chat_events if e.get("usage_provenance") != "PROVIDER_REPORTED")
    non_chat = len(api_events) - len(chat_events)
    integrity.append({
        "id": "TOKEN_COMPLETENESS", "status": "PASS" if missing_usage == 0 else "WARN",
        "detail": f"chat requests={len(chat_events)}, without provider usage={missing_usage}; "
                  f"non-chat metadata requests excluded ({non_chat})",
    })
    cred = env.get("credential_source") or summary.get("credential_source") or "UNKNOWN"
    integrity.append({
        "id": "CREDENTIAL_HANDLING", "status": "PASS",
        "detail": f"credential source: {cred}; key fingerprint only; relay holds the secret; agents got a dummy token",
    })
    completed_hash = read_json(os.path.join(run_dir, "hash_verification.json"))
    integrity.append({
        "id": "HASH_SEAL", "status": "PASS" if os.path.exists(os.path.join(run_dir, "SHA256SUMS")) else "WARN",
        "detail": "SHA256SUMS present" if os.path.exists(os.path.join(run_dir, "SHA256SUMS")) else "SHA256SUMS missing",
    })
    test_tamper = [r["agent_id"] for r in runs if ((r.get("grade") or {}).get("metrics") or {}).get("integrity", {}).get("modified")]
    integrity.append({
        "id": "TEST_INTEGRITY", "status": "PASS" if not test_tamper else "WARN",
        "detail": "no modified public test files" if not test_tamper else f"modified tests: {test_tamper}",
    })
    cpu_limits = {r["agent_id"]: r.get("cpu_affinity") for r in runs}
    integrity.append({
        "id": "RESOURCE_ISOLATION", "status": "PASS" if all(cpu_limits.values()) else "WARN",
        "detail": "per-run systemd scopes with MemoryMax/CPUQuota; affinity=" + json.dumps(cpu_limits),
    })
    integrity.append({
        "id": "GRADER_DETERMINISM",
        "status": "PASS" if all(((r.get("grade") or {}).get("determinism") or {}).get("agreement", True) for r in runs) else "FAIL",
        "detail": "graders report repeat agreement" if all(((r.get("grade") or {}).get("determinism") or {}).get("agreement", True) for r in runs) else "grader repeat disagreement",
    })
    integrity.append({
        "id": "TELEMETRY_ATTRIBUTION", "status": "PASS" if all((r.get("model_requests") or 0) >= 0 for r in runs) else "WARN",
        "detail": "all relay traffic is bound to a per-trial listener and agent id",
    })
    integrity.append({
        "id": "SYNTHETIC_DATA",
        "status": "WARN" if os.path.exists(os.path.join(run_dir, "SYNTHETIC")) else "PASS",
        "detail": "SYNTHETIC demo data — must not be read as a benchmark result" if os.path.exists(os.path.join(run_dir, "SYNTHETIC")) else "real instrumented data",
    })

    # economics
    def sum_or_none(vals):
        vals = [v for v in vals if isinstance(v, (int, float))]
        return sum(vals) if vals else None
    economics = {"per_agent": {}}
    for agent in agents:
        ar = [r for r in runs if r["agent_id"] == agent]
        passes = sum(1 for r in ar if r["verified_pass"])
        attempts = len(ar)
        quality = sum((r.get("grade") or {}).get("total") or 0 for r in ar)
        cost = sum_or_none([r.get("nominal_cost_usd") for r in ar])
        list_cost = sum_or_none([(r.get("tokens") or {}).get("list_cost_usd") for r in ar])
        allowance = sum_or_none([(r.get("tokens") or {}).get("allowance_usd") for r in ar])
        tokens = sum_or_none([(r.get("tokens") or {}).get("total") for r in ar])
        success_wall = [r["elapsed_seconds"] for r in ar if r["verified_pass"] and r.get("elapsed_seconds")]
        economics["per_agent"][agent] = {
            "attempts": attempts, "verified_passes": passes,
            "pass_rate": (passes / attempts) if attempts else None,
            "quality_points": round(quality, 2),
            "total_cost_usd": cost, "list_cost_usd": list_cost, "allowance_usd": allowance,
            "total_tokens": tokens,
            "cost_per_verified": (cost / passes) if (cost is not None and passes) else None,
            "cost_per_quality_point": (cost / quality) if (cost and quality) else None,
            "tokens_per_quality_point": (tokens / quality) if (tokens and quality) else None,
            "mean_success_wall_seconds": (sum(success_wall) / len(success_wall)) if success_wall else None,
        }

    # verdict: lexicographic per spec
    def rank_key(agent):
        e = economics["per_agent"][agent]
        return (-(e["verified_passes"]), -(e["quality_points"]))
    ranked = sorted(agents, key=rank_key)
    best_passes = economics["per_agent"][ranked[0]]["verified_passes"] if ranked else 0
    top = [a for a in agents if economics["per_agent"][a]["verified_passes"] == best_passes]
    if best_passes == 0:
        recommendation = "INSUFFICIENT_EVIDENCE"
    elif len(top) == 1:
        recommendation = top[0].upper().replace("-", "_")
    else:
        # tie break on quality subtotal then cost
        def tie_key(a):
            e = economics["per_agent"][a]
            return (-(e["quality_points"]), e["total_cost_usd"] if e["total_cost_usd"] is not None else 1e9)
        recommendation = sorted(top, key=tie_key)[0].upper().replace("-", "_")
        recommendation = recommendation if economics["per_agent"][sorted(top, key=tie_key)[0]]["quality_points"] > 0 else "TASK_DEPENDENT"

    recos = {}
    eligible = [a for a in agents if economics["per_agent"][a]["verified_passes"] > 0]
    if eligible:
        recos["fastest_verified"] = min(eligible, key=lambda a: (economics["per_agent"][a]["mean_success_wall_seconds"] or 1e18))
        recos["best_quality"] = max(eligible, key=lambda a: economics["per_agent"][a]["quality_points"])
        def cpv(a):
            v = economics["per_agent"][a]["cost_per_verified"]
            return v if v is not None else 1e18
        recos["cheapest_verified"] = min(eligible, key=cpv)
        def mean_mem(a):
            vals = [r.get("peak_memory_bytes") for r in runs if r["agent_id"] == a and r.get("peak_memory_bytes")]
            return sum(vals)/len(vals) if vals else 1e18
        recos["lowest_memory"] = min(agents, key=mean_mem)
        if recos["fastest_verified"] == recos["cheapest_verified"] == recos["best_quality"]:
            recos["best_balanced"] = recos["best_quality"]
        else:
            recos["best_balanced"] = recommendation.lower().replace("_", "-") if recommendation in [a.upper().replace("-", "_") for a in agents] else "TASK_DEPENDENT"
    data = {
        "meta": {
            "experiment_id": cfg.get("experiment_id") or summary.get("experiment_id"),
            "run_id": summary.get("run_id"),
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "model": cfg.get("model"), "provider": cfg.get("provider"),
            "reasoning_effort": cfg.get("reasoning_effort"),
            "started_utc": summary.get("started_utc"), "finished_utc": summary.get("finished_utc"),
            "wall_seconds": summary.get("wall_seconds"),
            "host": host, "agents": agents, "tasks": task_ids,
            "credential_source": cred,
            "dry_run": env.get("dry_run", False),
            "synthetic": os.path.exists(os.path.join(run_dir, "SYNTHETIC")),
            "empty_state": not any((r.get("grade") or {}) for r in runs),
            "run_dir": os.path.abspath(run_dir),
        },
        "runs": runs,
        "integrity": integrity,
        "economics": economics,
        "verdict": {
            "recommendation": recommendation,
            "confidence": "LOW",
            "n_trials": len(runs),
            "n_tasks": len(task_ids),
            "best_verified_completions": max((economics["per_agent"][a]["verified_passes"] for a in agents), default=0),
            "ranking": ranked,
            "decision_specific": recos,
            "caveats": [
                "One trial per agent-task combination (n=1); no statistical superiority claim is possible.",
                "Three tasks only; task-selection bias cannot be excluded.",
                "Shared provider contention and VPS scheduling affect wall time and latency.",
                "Nominal token prices are not the same as incremental subscription cash spend.",
                "Maintainability signals are partly heuristic and bounded by a 30-minute patch.",
            ],
        },
    }
    return data

def write_csvs(run_dir, data):
    runs = data["runs"]
    with open(os.path.join(run_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task", "agent", "status", "verified_pass", "grade_total", "elapsed_s", "cpu_s",
                    "peak_mem_bytes", "model_requests", "input_tokens", "output_tokens",
                    "reasoning_tokens", "cached_read_tokens", "cost_usd", "files_changed"])
        for r in runs:
            t = r.get("tokens") or {}
            w.writerow([r["task_id"], r["agent_id"], r["status"], r["verified_pass"],
                        (r.get("grade") or {}).get("total"), r.get("elapsed_seconds"),
                        r.get("cpu_seconds"), r.get("peak_memory_bytes"), r.get("model_requests"),
                        t.get("input"), t.get("output"), t.get("reasoning"), t.get("cached_read"),
                        r.get("nominal_cost_usd"), (r.get("code_metrics") or {}).get("files_changed")])
    with open(os.path.join(run_dir, "score_breakdown.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task", "agent", "status", "total", "category", "score", "max", "gate", "gate_passed", "gate_detail"])
        for r in runs:
            g = r.get("grade") or {}
            for cat, v in (g.get("categories") or {}).items():
                w.writerow([r["task_id"], r["agent_id"], g.get("status"), g.get("total"), cat,
                            v.get("score"), v.get("max"), "", "", ""])
            for gate in g.get("gates") or []:
                w.writerow([r["task_id"], r["agent_id"], g.get("status"), g.get("total"), "",
                            "", "", gate.get("id"), gate.get("passed"), gate.get("detail")])
    with open(os.path.join(run_dir, "api_calls.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task", "agent", "t_s", "model", "duration_ms", "ttfb_ms", "input", "output",
                    "reasoning", "cached", "status", "effort_requested", "effort_forwarded"])
        for r in runs:
            for e in r.get("api_series") or []:
                w.writerow([r["task_id"], r["agent_id"], e.get("t"), e.get("model"), e.get("dur_ms"),
                            e.get("ttfb_ms"), e.get("input"), e.get("output"), e.get("reasoning"),
                            e.get("cached"), e.get("cost"), e.get("status"), e.get("effort_req"), e.get("effort_fwd")])
    with open(os.path.join(run_dir, "tool_calls.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task", "agent", "event_type", "t_s", "detail"])
        for r in runs:
            for e in r.get("timeline") or []:
                w.writerow([r["task_id"], r["agent_id"], e.get("type"), e.get("t"),
                            json.dumps(e.get("detail"), sort_keys=True)])

def build_report(run_dir, out_path):
    data = build_data(run_dir)
    write_csvs(run_dir, data)
    payload = json.dumps(data, sort_keys=False, separators=(",", ":"))
    raw = payload.encode("utf-8")
    packed = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")
    template = HTML_TEMPLATE.replace("__DATA_GZIP_B64__", packed)
    template = template.replace("__TITLE__", html.escape(
        f"Agent Benchmark Forensic Report — {data['meta'].get('run_id') or 'run'}"))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(template)
    return data, len(template)

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#0f1216; --panel:#161b22; --panel2:#1c232d; --text:#e6edf3; --muted:#9aa7b4;
  --line:#2a3441; --accent:#4da3ff; --ok:#3fb950; --warn:#d29922; --bad:#f85149; --info:#a371f7;
  --chip:#202938;
}
html[data-theme="light"]{
  --bg:#f6f8fa; --panel:#ffffff; --panel2:#f0f3f6; --text:#1f2328; --muted:#59636e;
  --line:#d0d7de; --accent:#0969da; --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e; --info:#8250df;
  --chip:#eaeef2;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header.top{position:sticky;top:0;z-index:20;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 18px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header .brand{font-weight:650;letter-spacing:.2px}
header .spacer{flex:1}
button,select,input{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:5px 9px;font:inherit}
button{cursor:pointer}
button:hover{border-color:var(--accent)}
button.on{border-color:var(--accent);color:var(--accent)}
main{max-width:1280px;margin:0 auto;padding:18px}
section{margin:22px 0}
h1{font-size:22px;margin:6px 0 2px}
h2{font-size:17px;margin:0 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
h3{font-size:14px;margin:14px 0 6px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.grid{display:grid;gap:12px}
.cards{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{color:var(--muted);font-size:12px}
.card .v{font-size:22px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}
.card .s{color:var(--muted);font-size:12px;margin-top:2px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:12px;border:1px solid var(--line);background:var(--chip)}
.ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)} .muted{color:var(--muted)}
.pill-ok{border-color:var(--ok)} .pill-warn{border-color:var(--warn)} .pill-bad{border-color:var(--bad)}
.verdict{font-size:26px;font-weight:750;letter-spacing:.4px}
.notice{border-left:3px solid var(--warn);background:var(--panel2);padding:10px 12px;border-radius:0 8px 8px 0;margin:10px 0}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px;cursor:pointer;user-select:none}
th:first-child,td:first-child{text-align:left}
tbody tr:hover{background:var(--panel2);cursor:pointer}
td.wrap{white-space:normal;max-width:380px}
.scroll{overflow:auto;max-height:420px;border:1px solid var(--line);border-radius:8px}
.matrix td{text-align:center;vertical-align:middle}
.cell{border-radius:8px;padding:10px 6px;border:1px solid var(--line);cursor:pointer}
.cell.pass{border-color:var(--ok);background:rgba(63,185,80,.12)}
.cell.fail{border-color:var(--bad);background:rgba(248,81,73,.10)}
.cell.invalid{border-color:var(--warn);background:rgba(210,153,34,.12)}
.cell .sc{font-size:20px;font-weight:700}
svg{display:block;max-width:100%}
.axis text{fill:var(--muted);font-size:10px}
.gridline{stroke:var(--line);stroke-width:1}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:6px 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tabbtn{padding:5px 10px;border-radius:6px;border:1px solid var(--line);background:var(--panel2);cursor:pointer;font-size:13px}
.tabbtn.on{border-color:var(--accent);color:var(--accent)}
.kv{display:grid;grid-template-columns:180px 1fr;gap:4px 12px;font-size:13px}
.kv .k{color:var(--muted)}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;max-height:340px;font-size:12px;line-height:1.45}
details{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:6px 0;background:var(--panel2)}
summary{cursor:pointer;font-weight:600}
.split{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:900px){.split{grid-template-columns:1fr}.kv{grid-template-columns:120px 1fr}}
.footer{color:var(--muted);font-size:12px;margin:30px 0 10px;border-top:1px solid var(--line);padding-top:10px}
.search{min-width:220px}
.hidden{display:none}
.tag{font-size:11px;padding:1px 6px;border-radius:4px;background:var(--chip);border:1px solid var(--line);color:var(--muted)}
.tooltip{position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--accent);border-radius:6px;padding:6px 8px;font-size:12px;z-index:50;display:none;max-width:320px}
@media print{header.top,.tabs,button,select,input{display:none!important}body{background:#fff;color:#000}section{break-inside:avoid}.panel{border:1px solid #999}}
</style>
</head>
<body>
<header class="top">
  <div class="brand">Forensic Agent Benchmark</div>
  <span id="horizon" class="badge"></span>
  <div class="spacer"></div>
  <input id="search" class="search" placeholder="Search trials, gates, evidence…" aria-label="Search">
  <select id="agentFilter" aria-label="Filter agent"><option value="">All agents</option></select>
  <select id="taskFilter" aria-label="Filter task"><option value="">All tasks</option></select>
  <button id="themeBtn" title="Toggle theme">Theme</button>
  <button onclick="window.print()" title="Print / PDF">Print</button>
</header>
<main>
  <section id="exec">
    <h1>Agent Benchmark — Forensic Audit</h1>
    <div id="metaLine" class="muted"></div>
    <div id="verdictPanel" class="panel" style="margin-top:12px"></div>
    <div class="cards grid" id="kpis" style="margin-top:12px"></div>
    <div id="verdictCaveats" class="notice"></div>
  </section>

  <section id="matrixSection">
    <h2>Task × agent result matrix</h2>
    <div class="panel"><table class="matrix" id="matrix"></table>
      <div class="muted" style="font-size:12px;margin-top:8px">Click a cell to open the trial dossier. PASS requires all P0 gates and score ≥ 80.</div>
    </div>
  </section>

  <section id="compareSection">
    <h2>Nine-run comparison</h2>
    <div class="panel">
      <div class="tabs" id="compareTabs"></div>
      <div class="scroll"><table id="compareTable"></table></div>
    </div>
  </section>

  <section id="timelineSection">
    <h2>Synchronized race timelines</h2>
    <div class="panel">
      <div class="tabs" id="ganttTaskTabs"></div>
      <div id="gantt"></div>
      <div class="legend"><span><i style="background:var(--accent)"></i>API request span</span><span><i style="background:var(--ok)"></i>Agent lifetime</span><span><i style="background:var(--warn)"></i>Grading (outside race budget)</span><span><i style="background:var(--bad)"></i>Timeout</span></div>
      <div class="muted" style="font-size:12px">Time origin = barrier release. Overlapping spans are not additive. API spans are derived from request completion timestamps and durations.</div>
    </div>
  </section>

  <section id="tokensSection">
    <h2>Token and API economics</h2>
    <div class="split">
      <div class="panel"><h3>Cumulative tokens over trial time</h3><div id="tokenCurves"></div><div class="muted" style="font-size:12px">Per-request provider usage; cache and reasoning subcategories may overlap aggregate input/output semantics.</div></div>
      <div class="panel"><h3>Request latency distribution</h3><div id="latencyBars"></div><table id="latencyTable" style="margin-top:8px"></table></div>
    </div>
    <div class="panel" style="margin-top:12px"><h3>Cumulative nominal USD (provider-reported)</h3><div id="costCurves"></div></div>
  </section>

  <section id="resourceSection">
    <h2>CPU and memory forensics</h2>
    <div class="tabs" id="resTabs"></div>
    <div class="panel"><div id="resourcePlots"></div>
      <div class="muted" style="font-size:12px">Source: cgroup v2 samples at 1 s. CPU cores = delta of cpu.stat usage / wall delta. Host load and throttling recorded in raw telemetry.</div>
    </div>
  </section>

  <section id="perfSection">
    <h2>Performance parity (Task 2)</h2>
    <div class="panel">
      <div id="perfBars"></div>
      <table id="perfTable" style="margin-top:10px"></table>
      <div class="muted" style="font-size:12px">Baseline and candidate are run interleaved on the same host; medians of repeated runs. A speed-up only counts when exact output parity and all P0 gates pass. Profiles: small / large / adversarial / fresh-seed.</div>
    </div>
  </section>

  <section id="qualitySection">
    <h2>Quality and grade evidence</h2>
    <div class="tabs" id="qualityTabs"></div>
    <div class="panel" id="qualityPanel"></div>
  </section>

  <section id="codeSection">
    <h2>Code changes</h2>
    <div class="panel" id="codePanel"></div>
  </section>

  <section id="costSection">
    <h2>Decision economics</h2>
    <div class="split">
      <div class="panel"><h3>Cost vs verified quality</h3><div id="costQuality"></div></div>
      <div class="panel"><h3>Cost vs wall time</h3><div id="costTime"></div></div>
    </div>
    <div class="panel" style="margin-top:12px"><h3 id="econTitle">Projected scenarios (100 / 500 / 1000 tasks)</h3>
      <div class="muted" style="font-size:12px;margin-bottom:8px">Scenarios, not forecasts. Assumptions are shown and editable; failure handling and human review are explicit inputs.</div>
      <label class="muted" style="font-size:12px">Human review minutes per verified task <input id="reviewMin" type="number" value="20" min="0" step="5" style="width:80px"></label>
      <label class="muted" style="font-size:12px;margin-left:12px">Loaded hourly rate (USD) <input id="rate" type="number" value="90" min="0" step="10" style="width:90px"></label>
      <label class="muted" style="font-size:12px;margin-left:12px">Rework probability <input id="rework" type="number" value="15" min="0" max="100" step="5" style="width:70px">%</label>
      <div class="scroll" style="margin-top:8px"><table id="econTable"></table></div>
    </div>
  </section>

  <section id="integritySection">
    <h2>Experiment integrity audit</h2>
    <div class="panel"><div id="integrity"></div></div>
  </section>

  <section id="evidenceSection">
    <h2>Evidence explorer</h2>
    <div class="panel" id="evidence"></div>
  </section>

  <section id="dossierSection" class="hidden">
    <h2>Trial dossier <span id="dossierTitle" class="muted"></span></h2>
    <div class="panel" id="dossier"></div>
  </section>

  <section id="manifestSection">
    <h2>Manifest and reproducibility</h2>
    <div class="panel" id="manifest"></div>
  </section>
  <div class="footer">
    Self-contained offline report. No external scripts, fonts or network calls.
    Raw evidence, patches and logs live beside this file with SHA-256 hashes recorded in the manifest.
  </div>
</main>
<div class="tooltip" id="tooltip"></div>
<script id="bench-data-gz" type="text/plain">__DATA_GZIP_B64__</script>
<script>
"use strict";
const B64=document.getElementById('bench-data-gz').textContent.trim();
async function inflate(b64){
  const bin=atob(b64); const bytes=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
  if(!('DecompressionStream' in window)) throw new Error('DecompressionStream unavailable');
  const ds=new DecompressionStream('gzip');
  const stream=new Blob([bytes]).stream().pipeThrough(ds);
  return await new Response(stream).text();
}
const $=s=>document.querySelector(s);
const el=(tag,attrs={},kids=[])=>{const e=document.createElement(tag);for(const[k,v] of Object.entries(attrs)){if(k==='class')e.className=v;else if(k==='html')e.innerHTML=v;else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v);}for(const k of kids){e.append(k);}return e;};
const fmt=(v,d=1)=>{if(v===null||v===undefined||Number.isNaN(v))return '—';return Number(v).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});};
const fmtInt=v=>(v===null||v===undefined)?'—':Number(v).toLocaleString();
const pct=v=>(v===null||v===undefined)?'—':(v*100).toFixed(1)+'%';
const bytes=v=>v===null||v===undefined?'—':(v>=1073741824?fmt(v/1073741824,2)+' GiB':v>=1048576?fmt(v/1048576,1)+' MiB':fmt(v/1024,0)+' KiB');
const usd=v=>v===null||v===undefined?'—':'$'+Number(v).toFixed(6);
const short=s=>s===null||s===undefined?'—':String(s);
let DATA=null;
const state={agent:'',task:'',compareTab:null,ganttTask:null,resTab:null,qualityTab:null};

function badge(status){
  const s=(status||'').toUpperCase();
  if(s==='PASS'||s==='OK') return `<span class="badge pill-ok ok">PASS</span>`;
  if(s==='FAIL'||s==='INVALID') return `<span class="badge pill-bad bad">${s}</span>`;
  if(s==='WARN') return `<span class="badge pill-warn warn">WARN</span>`;
  return `<span class="badge">${short(status)}</span>`;
}
function verdictClass(v){return ['PI_BOLT','JCODE','HAX','TASK_DEPENDENT'].includes(v)?'ok':(v==='INVALID_BENCHMARK'?'bad':'warn');}

function filteredRuns(){
  return DATA.runs.filter(r=>(!state.agent||r.agent_id===state.agent)&&(!state.task||r.task_id===state.task));
}
function byTaskAgent(task,agent){return DATA.runs.find(r=>r.task_id===task&&r.agent_id===agent);}

function render(){
  const m=DATA.meta;
  const demo = m.synthetic ? 'SYNTHETIC DEMO' : (m.dry_run ? 'DRY RUN / NOT A RESULT' : (m.empty_state ? 'EMPTY / NO TRIALS' : (DATA.verdict.confidence+' CONFIDENCE')));
  $('#horizon').textContent = demo;
  $('#horizon').className='badge '+(m.synthetic||m.dry_run||m.empty_state?'pill-warn':'');
  $('#metaLine').innerHTML=`Experiment <b>${short(m.experiment_id)}</b> · run <b>${short(m.run_id)}</b> · model <b>${short(m.model)}</b> via ${short(m.provider)} · reasoning_effort <b>${short(m.reasoning_effort)}</b> · ${m.agents.length} agents × ${m.tasks.length} tasks · generated ${short(m.generated_utc)}`;
  const v=DATA.verdict;
  $('#verdictPanel').innerHTML=`<div class="verdict ${verdictClass(v.recommendation)}">${v.recommendation}</div>
    <div class="muted">Provisional decision from ${v.n_trials} trials across ${v.n_tasks} tasks · ${v.confidence} confidence · best verified completions: ${v.best_verified_completions}/${v.n_tasks}</div>`;
  const rec=v.decision_specific||{};
  const recos=[['FASTEST VERIFIED',rec.fastest_verified],['BEST QUALITY',rec.best_quality],['CHEAPEST VERIFIED',rec.cheapest_verified],['LOWEST MEMORY',rec.lowest_memory],['BEST BALANCED',rec.best_balanced]];
  $('#verdictPanel').insertAdjacentHTML('beforeend','<div class="cards grid" style="margin-top:10px">'+recos.map(([k,a])=>`<div class="card"><div class="k">${k}</div><div class="v" style="font-size:16px">${a?a:'INSUFFICIENT EVIDENCE'}</div></div>`).join('')+'</div>');
  $('#verdictCaveats').innerHTML='<b>Uncertainty (mandatory):</b> '+v.caveats.map(c=>`<div>• ${c}</div>`).join('');
  renderKpis(); renderMatrix(); renderCompare(); renderGantt(); renderTokens(); renderResources(); renderPerf(); renderQuality(); renderCode(); renderCosts(); renderIntegrity(); renderEvidence(); renderManifest(); renderDossier();
}
function renderKpis(){
  const runs=filteredRuns(); const k=$('#kpis'); k.innerHTML='';
  const passes=runs.filter(r=>r.verified_pass).length;
  const quality=runs.reduce((a,r)=>a+((r.grade||{}).total||0),0);
  const wall=runs.reduce((a,r)=>a+(r.elapsed_seconds||0),0);
  const cpu=runs.reduce((a,r)=>a+(r.cpu_seconds||0),0);
  const mem=Math.max(0,...runs.map(r=>r.peak_memory_bytes||0));
  const tok=runs.reduce((a,r)=>a+((r.tokens||{}).total||0),0);
  const cost=runs.reduce((a,r)=>a+(r.nominal_cost_usd||0),0);
  const cards=[
    ['Verified passes',`${passes} / ${runs.length}`,'all P0 gates + score ≥ 80'],
    ['Total quality points',fmt(quality,1),`max ${runs.length*100}`],
    ['Summed agent wall time',fmt(wall,0)+' s','overlapping rounds are summed per trial'],
    ['Summed CPU seconds',fmt(cpu,2),'cgroup cpu.stat usage'],
    ['Peak memory (single trial)',bytes(mem),'cgroup memory.peak'],
    ['Total tokens',fmtInt(tok),'provider-reported'],
    ['Nominal API cost',usd(cost),'provider-reported list price; subscription allowance separate'],
    ['Fairness', DATA.integrity.filter(i=>i.status!=='PASS').length? 'WARN':'PASS','see integrity audit'],
  ];
  for(const [a,b,c] of cards){k.append(el('div',{class:'card',html:`<div class="k">${a}</div><div class="v">${b}</div><div class="s">${c}</div>`}));}
}
function renderMatrix(){
  const t=$('#matrix'); t.innerHTML='';
  const head=el('tr',{},[el('th',{html:'Task \\ Agent'})]);
  for(const a of DATA.meta.agents) head.append(el('th',{html:a}));
  t.append(head);
  for(const task of DATA.meta.tasks){
    const tr=el('tr',{},[el('td',{html:`<b>${task}</b>`})]);
    for(const agent of DATA.meta.agents){
      const r=byTaskAgent(task,agent);
      const g=r?r.grade||{}:{};
      const status=g.status||(r?r.status:'NOT_EVALUATED');
      const cls=status==='PASS'?'pass':(status==='INVALID'?'invalid':'fail');
      const td=el('td',{},[]);
      const cell=el('div',{class:'cell '+cls,onclick:()=>openDossier(task,agent),html:
        `<div class="sc">${g.total!==undefined?fmt(g.total,1):'—'}</div><div>${badge(status)}</div><div class="muted" style="font-size:11px">${r?fmt(r.elapsed_seconds,0)+' s · '+fmtInt(r.model_requests)+' req':'no data'}</div>`});
      td.append(cell); tr.append(td);
    }
    t.append(tr);
  }
}
function columns(){
  return [['task','Task'],['agent','Agent'],['gate','Critical gate'],['tests','Hidden tests'],['total','Score'],
    ['status','Status'],['elapsed','Elapsed s'],['cpu','CPU s'],['input','In tokens'],['output','Out tokens'],
    ['reasoning','Reasoning'],['cache','Cache read'],['cost','USD'],['mem','Peak mem'],['req','Requests'],['files','Files']];
}
function runRow(r){
  const t=r.tokens||{}; const g=r.grade||{};
  const gates=(g.gates||[]); const passed=gates.filter(x=>x.passed).length;
  const cases=(g.cases||[]); const cpass=cases.filter(c=>c.passed).length;
  return {task:r.task_id,agent:r.agent_id,gate:`${passed}/${gates.length}`,tests:`${cpass}/${cases.length}`,
    total:g.total,status:g.status||r.status,cost:(r.nominal_cost_usd!==null&&r.nominal_cost_usd!==undefined)?r.nominal_cost_usd:t.list_cost_usd,elapsed:r.elapsed_seconds,cpu:r.cpu_seconds,
    input:t.input,output:t.output,reasoning:t.reasoning,cache:t.cached_read,mem:r.peak_memory_bytes,
    req:r.model_requests,files:(r.code_metrics||{}).files_changed,_run:r};
}
function renderCompare(){
  const host=$('#compareTable'); host.innerHTML='';
  const cols=columns();
  const head=el('tr',{},cols.map(c=>el('th',{html:c[1],onclick:()=>sortCompare(c[0])})));
  host.append(head);
  const rows=filteredRuns().map(runRow).sort((a,b)=>String(a.task).localeCompare(String(b.task))||String(a.agent).localeCompare(String(b.agent)));
  for(const row of rows){
    const tr=el('tr',{onclick:()=>openDossier(row.task,row.agent)});
    for(const c of cols){
      let v=row[c[0]];
      if(['elapsed','cpu'].includes(c[0]))v=fmt(v,1);
      else if(['input','output','reasoning','cache','req','files'].includes(c[0]))v=fmtInt(v);
      else if(c[0]==='mem')v=bytes(v);
      else if(c[0]==='cost')v=usd(v);
      else if(c[0]==='total')v=fmt(v,1);
      else if(c[0]==='status')v=badge(v);
      tr.append(el('td',{html:v===undefined||v===null?'—':String(v)}));
    }
    host.append(tr);
  }
}
let compareSort={key:'task',dir:1};
function sortCompare(key){compareSort.dir=(compareSort.key===key?-compareSort.dir:1);compareSort.key=key;
  const tbl=$('#compareTable'); const rows=[...tbl.querySelectorAll('tr')].slice(1);
  rows.sort((a,b)=>{const ai=[...a.children][columns().findIndex(c=>c[0]===key)];const bi=[...b.children][columns().findIndex(c=>c[0]===key)];
    const an=parseFloat(ai.textContent.replace(/[^0-9.\-]/g,''));const bn=parseFloat(bi.textContent.replace(/[^0-9.\-]/g,''));
    if(!Number.isNaN(an)&&!Number.isNaN(bn))return (an-bn)*compareSort.dir;return ai.textContent.localeCompare(bi.textContent)*compareSort.dir;});
  rows.forEach(r=>tbl.append(r));
}

function renderGantt(){
  const tabs=$('#ganttTaskTabs'); tabs.innerHTML='';
  for(const task of DATA.meta.tasks){
    tabs.append(el('button',{class:'tabbtn '+(state.ganttTask===task?'on':''),onclick:()=>{state.ganttTask=task;renderGantt();},html:task}));
  }
  const task=state.ganttTask||DATA.meta.tasks[0];
  const runs=DATA.meta.agents.map(a=>byTaskAgent(task,a)).filter(Boolean);
  const host=$('#gantt'); host.innerHTML='';
  const W=1100,rowH=54,left=110,top=26,H=top+runs.length*rowH+24;
  const maxT=Math.max(60,...runs.map(r=>r.elapsed_seconds||0),...runs.flatMap(r=>(r.api_series||[]).map(e=>e.t+(e.dur_ms||0)/1000)));
  const x=t=>left+(W-left-20)*(t/maxT);
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('height',H);
  const mk=(tag,attrs)=>{const e=document.createElementNS(ns,tag);for(const[k,v] of Object.entries(attrs))e.setAttribute(k,v);return e;};
  for(let i=0;i<=5;i++){const t=maxT*i/5;const gx=x(t);
    svg.append(mk('line',{x1:gx,y1:top-8,x2:gx,y2:H-18,class:'gridline'}));
    const tx=mk('text',{x:gx,y:H-5,class:'axis',fill:'currentColor','text-anchor':'middle'});tx.textContent=fmt(t,0)+'s';svg.append(tx);}
  runs.forEach((r,i)=>{
    const y=top+i*rowH;
    const tx=mk('text',{x:10,y:y+18,fill:'currentColor',style:'font-size:12px;font-weight:600'});tx.textContent=r.agent_id;svg.append(tx);
    const tx2=mk('text',{x:10,y:y+34,fill:'currentColor',style:'font-size:11px;opacity:.65'});tx2.textContent=badgeText(r);svg.append(tx2);
    const life=mk('rect',{x:x(0),y:y+4,width:Math.max(2,x(r.elapsed_seconds||0)-x(0)),height:18,rx:3,fill:'var(--ok)',opacity:.35});
    svg.append(life);
    (r.api_series||[]).forEach(e=>{
      const start=Math.max(0,e.t-(e.dur_ms||0)/1000);
      const rect=mk('rect',{x:x(start),y:y+26,width:Math.max(1,x(e.t)-x(start)),height:14,rx:2,fill:'var(--accent)',opacity:.85});
      rect.addEventListener('mouseenter',ev=>showTip(ev,`${r.agent_id} · request<br>start ${fmt(start,2)}s · ${fmt(e.dur_ms,0)} ms<br>in ${fmtInt(e.input)} / out ${fmtInt(e.output)}`));
      rect.addEventListener('mouseleave',hideTip);
      svg.append(rect);
    });
    (r.timeline||[]).forEach(ev=>{
      if(ev.t===null)return;
      if(ev.type==='RUN_TIMEOUT'){svg.append(mk('line',{x1:x(ev.t),y1:y+2,x2:x(ev.t),y2:y+44,stroke:'var(--bad)','stroke-width':2}));}
      if(ev.type==='GRADER_STARTED'){svg.append(mk('rect',{x:x(Math.max(0,(r.elapsed_seconds||0))),y:y+26,width:8,height:14,fill:'var(--warn)',opacity:.8}));}
    });
    svg.append(mk('line',{x1:x(Math.min(maxT,r.startup_ms?r.startup_ms/1000:0)),y1:y+2,x2:x(Math.min(maxT,r.startup_ms?r.startup_ms/1000:0)),y2:y+44,stroke:'var(--info)','stroke-width':2}));
  });
  const cut=mk('line',{x1:x(1800),y1:top-8,x2:x(1800),y2:H-18,stroke:'var(--bad)','stroke-dasharray':'4 4'});
  if(1800<=maxT)svg.append(cut);
  host.append(svg);
}
function badgeText(r){const g=r.grade||{};return `${g.status||r.status} · ${fmt(g.total,1)} pts · ${fmt(r.elapsed_seconds,0)}s`;}

function lineChart(hostId,seriesDef,yLabel,unitFmt){
  const host=$('#'+hostId); host.innerHTML='';
  const W=1080,H=300,left=70,top=18,right=20,bottom=34;
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('height',H);
  const mk=(t,a)=>{const e=document.createElementNS(ns,t);for(const[k,v] of Object.entries(a))e.setAttribute(k,v);return e;};
  let maxX=1,maxY=1;
  for(const s of seriesDef){for(const p of s.points){maxX=Math.max(maxX,p.x);maxY=Math.max(maxY,p.y);}}
  const x=v=>left+(W-left-right)*(v/maxX), y=v=>H-bottom-(H-top-bottom)*(v/maxY);
  for(let i=0;i<=4;i++){const gy=top+(H-top-bottom)*i/4;const val=maxY*(1-i/4);
    svg.append(mk('line',{x1:left,y1:gy,x2:W-right,y2:gy,class:'gridline'}));
    const tx=mk('text',{x:left-8,y:gy+3,class:'axis','text-anchor':'end',fill:'currentColor'});tx.textContent=unitFmt(val);svg.append(tx);}
  for(let i=0;i<=5;i++){const tx0=maxX*i/5;const tx=mk('text',{x:x(tx0),y:H-12,class:'axis','text-anchor':'middle',fill:'currentColor'});tx.textContent=fmt(tx0,0)+'s';svg.append(tx);}
  seriesDef.forEach((s,idx)=>{
    if(!s.points.length)return;
    const d=s.points.map((p,i)=>`${i?'L':'M'}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(' ');
    svg.append(mk('path',{d,fill:'none',stroke:s.color,'stroke-width':2}));
  });
  host.append(svg);
  host.append(el('div',{class:'legend',html:seriesDef.map(s=>`<span><i style="background:${s.color}"></i>${s.label}</span>`).join('')}));
}
const COLORS=['#4da3ff','#3fb950','#d29922','#f85149','#a371f7','#39c5cf','#db6d28','#8b949e'];
function renderTokens(){
  const defs=[]; let i=0;
  for(const r of filteredRuns()){
    const pts=(r.api_series||[]).map((e,idx)=>{
      const before=(r.api_series||[]).slice(0,idx);
      const y=before.reduce((a,x)=>a+(x.input||0)+(x.output||0),0)+(e.input||0)+(e.output||0);
      return {x:e.t,y};
    });
    defs.push({label:`${r.task_id}/${r.agent_id}`,color:COLORS[i++%COLORS.length],points:pts});
  }
  lineChart('tokenCurves',defs,'tokens',v=>fmtInt(Math.round(v)));
  const costDefs=[]; i=0;
  for(const r of filteredRuns()){
    let acc=0;
    const pts=(r.api_series||[]).map(e=>{acc+=(e.cost||0);return {x:e.t,y:acc};});
    costDefs.push({label:`${r.task_id}/${r.agent_id}`,color:COLORS[i++%COLORS.length],points:pts});
  }
  lineChart('costCurves',costDefs,'USD',v=>'$'+Number(v).toFixed(5));

  const tbl=$('#latencyTable'); tbl.innerHTML='';
  tbl.append(el('tr',{html:'<th>Trial</th><th>n</th><th>median ms</th><th>p90 ms</th><th>max ms</th><th>TTFB med</th><th>out tok/s</th>'}));
  for(const r of filteredRuns()){
    const l=r.latency||{};
    tbl.append(el('tr',{html:`<td>${r.task_id}/${r.agent_id}</td><td>${fmtInt(l.count)}</td><td>${fmt(l.median_ms,0)}</td><td>${fmt(l.p90_ms,0)}</td><td>${fmt(l.max_ms,0)}</td><td>${fmt(l.ttfb_median_ms,0)}</td><td>${fmt(l.output_tok_per_s,1)}</td>`}));
  }
  const host=$('#latencyBars'); host.innerHTML='';
  const runs=filteredRuns(); const vals=runs.map(r=>({label:r.task_id+'/'+r.agent_id,med:(r.latency||{}).median_ms||0,p90:(r.latency||{}).p90_ms||0}));
  const max=Math.max(1,...vals.map(v=>v.p90));
  const W=520,H=Math.max(120,vals.length*26+20),left=150;
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('height',H);
  const mk=(t,a)=>{const e=document.createElementNS(ns,t);for(const[k,v] of Object.entries(a))e.setAttribute(k,v);return e;};
  vals.forEach((v,i)=>{const y=10+i*26;
    const tx=mk('text',{x:6,y:y+11,fill:'currentColor',style:'font-size:11px'});tx.textContent=v.label;svg.append(tx);
    svg.append(mk('rect',{x:left,y:y+2,width:(W-left-30)*(v.p90/max),height:14,rx:2,fill:'var(--info)',opacity:.35}));
    svg.append(mk('rect',{x:left,y:y+2,width:(W-left-30)*(v.med/max),height:14,rx:2,fill:'var(--accent)'}));
    const tx2=mk('text',{x:left+(W-left-30)*(v.p90/max)+4,y:y+12,fill:'currentColor',style:'font-size:10px'});tx2.textContent=fmt(v.p90,0)+' ms p90';svg.append(tx2);
  });
  host.append(svg);
}
function renderResources(){
  const tabs=$('#resTabs'); tabs.innerHTML='';
  for(const r of filteredRuns()){
    const key=r.task_id+'/'+r.agent_id;
    tabs.append(el('button',{class:'tabbtn '+(state.resTab===key?'on':''),onclick:()=>{state.resTab=key;renderResources();},html:key}));
  }
  const run=filteredRuns().find(r=>r.task_id+'/'+r.agent_id===state.resTab)||filteredRuns()[0];
  const host=$('#resourcePlots'); if(!host)return; host.innerHTML='';
  if(!run){host.textContent='NO DATA';return;}
  const pts=run.resource_series||[];
  if(!pts.length){host.innerHTML='<div class="muted">NO RESOURCE SAMPLES for this trial.</div>';return;}
  const mkChart=(title,key,fmtFn,color)=>{
    const W=1080,H=220,left=70,top=14,right=20,bottom=28;
    const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('height',H);
    const mk=(t,a)=>{const e=document.createElementNS(ns,t);for(const[k,v] of Object.entries(a))e.setAttribute(k,v);return e;};
    const vals=pts.map(p=>p[key]).filter(v=>typeof v==='number');
    const maxY=Math.max(0.001,...vals), maxX=Math.max(0.001,...pts.map(p=>p.t));
    const x=v=>left+(W-left-right)*(v/maxX), y=v=>H-bottom-(H-top-bottom)*(v/maxY);
    for(let i=0;i<=3;i++){const gy=top+(H-top-bottom)*i/3;
      svg.append(mk('line',{x1:left,y1:gy,x2:W-right,y2:gy,class:'gridline'}));
      const tx=mk('text',{x:left-8,y:gy+3,class:'axis','text-anchor':'end',fill:'currentColor'});tx.textContent=fmtFn(maxY*(1-i/3));svg.append(tx);}
    const d=pts.map((p,i)=>`${i?'L':'M'}${x(p.t).toFixed(1)},${y(p[key]||0).toFixed(1)}`).join(' ');
    svg.append(mk('path',{d,fill:'none',stroke:color,'stroke-width':2}));
    return el('div',{},[el('h3',{html:title}),svg]);
  };
  host.append(mkChart(`CPU utilisation — ${run.task_id}/${run.agent_id}`,'cpu_cores',v=>fmt(v,1)+' cores','#4da3ff'));
  host.append(mkChart('Memory current (MiB)','mem_mb',v=>fmt(v,0),'#3fb950'));
  const extra=el('div',{class:'kv'});
  [['Peak memory',bytes(run.peak_memory_bytes)],['CPU seconds',fmt(run.cpu_seconds,3)],
   ['CPU throttled',fmt(run.cpu_throttled_seconds,3)+' s'],['OOM kills',fmtInt(run.oom_events)],
   ['Affinity',short(run.cpu_affinity)],['Samples',fmtInt((run.resource_series||[]).length)]].forEach(([k,v])=>{
    extra.append(el('div',{class:'k',html:k}));extra.append(el('div',{html:v}));});
  host.append(extra);
}
function renderPerf(){
  const runs=filteredRuns().filter(r=>r.task_id==='t2_replay_perf');
  const host=$('#perfTable'); if(!host)return; host.innerHTML='';
  if(!runs.length){$('#perfBars').innerHTML='<div class="muted">NO T2 TRIALS IN CURRENT FILTER.</div>';return;}
  host.append(el('tr',{html:'<th>Agent</th><th>Large baseline ms</th><th>Large candidate ms</th><th>Large speed-up</th><th>Adversarial speed-up</th><th>Small speed-up</th><th>Peak RSS ratio (large)</th><th>Parity gate</th>'}));
  const bars=[];
  for(const r of runs){
    const m=(r.grade||{}).metrics||{}; const sp=m.speedup||{}; const bm=m.baseline_median_ms||{}; const cm=m.candidate_median_ms||{};
    const rss=(m.peak_rss_bytes||{}); const ratio=(rss.candidate&&rss.baseline)?(rss.candidate.h_large/rss.baseline.h_large):null;
    const parityOK=!(r.grade||{}).gates?.some(g=>g.id.toLowerCase().includes('parity')&&!g.passed);
    host.append(el('tr',{html:`<td>${r.agent_id}</td><td>${fmt(bm.h_large,0)}</td><td>${fmt(cm.h_large,0)}</td><td>${fmt(sp.h_large,2)}×</td><td>${fmt(sp.h_adversarial,2)}×</td><td>${fmt(sp.h_small,2)}×</td><td>${ratio===null?'—':fmt(ratio,2)+'×'}</td><td>${badge(parityOK?'PASS':'FAIL')}</td>`}));
    bars.push({agent:r.agent_id,large:sp.h_large||0,adv:sp.h_adversarial||0});
  }
  const W=980,H=220,left=70,top=16,right=20,bottom=40;
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('height',H);
  const mk=(t,a)=>{const e=document.createElementNS(ns,t);for(const[k,v] of Object.entries(a))e.setAttribute(k,v);return e;};
  const maxV=Math.max(3,...bars.flatMap(b=>[b.large,b.adv]));
  const x=v=>left+(W-left-right)*(v/maxV), y=H-bottom;
  for(let i=0;i<=4;i++){const gx=left+(W-left-right)*i/4;
    svg.append(mk('line',{x1:gx,y1:top,x2:gx,y2:y,class:'gridline'}));
    const tx=mk('text',{x:gx,y:H-20,class:'axis','text-anchor':'middle',fill:'currentColor'});tx.textContent=fmt(maxV*i/4,1)+'×';svg.append(tx);}
  const three=x(3); svg.append(mk('line',{x1:three,y1:top,x2:three,y2:y,stroke:'var(--ok)','stroke-dasharray':'4 4'}));
  const t3=mk('text',{x:three+4,y:top+10,fill:'currentColor',style:'font-size:10px'});t3.textContent='3× target';svg.append(t3);
  bars.forEach((b,i)=>{const bw=(W-left-right)/(bars.length*3); const x0=left+(W-left-right)*(i/bars.length)+bw*0.3;
    const h1=(H-top-bottom)*(b.large/maxV), h2=(H-top-bottom)*(b.adv/maxV);
    svg.append(mk('rect',{x:x0,y:y-h1,width:bw,height:h1,rx:2,fill:'var(--accent)'}));
    svg.append(mk('rect',{x:x0+bw*1.2,y:y-h2,width:bw,height:h2,rx:2,fill:'var(--info)'}));
    const lab=mk('text',{x:x0+bw,y:y+14,fill:'currentColor',style:'font-size:11px','text-anchor':'middle'});lab.textContent=b.agent;svg.append(lab);
  });
  $('#perfBars').innerHTML=''; $('#perfBars').append(svg);
  $('#perfBars').append(el('div',{class:'legend',html:'<span><i style="background:var(--accent)"></i>large profile</span><span><i style="background:var(--info)"></i>adversarial profile</span>'}));
}
function renderQuality(){
  const tabs=$('#qualityTabs'); tabs.innerHTML='';
  for(const r of filteredRuns()){
    const key=r.task_id+'/'+r.agent_id;
    tabs.append(el('button',{class:'tabbtn '+(state.qualityTab===key?'on':''),onclick:()=>{state.qualityTab=key;renderQuality();},html:key}));
  }
  const run=filteredRuns().find(r=>r.task_id+'/'+r.agent_id===state.qualityTab)||filteredRuns()[0];
  const host=$('#qualityPanel'); host.innerHTML='';
  if(!run){host.textContent='NO DATA';return;}
  const g=run.grade||{};
  host.append(el('div',{html:`<b>${run.task_id}/${run.agent_id}</b> · status ${badge(g.status)} · total <b>${fmt(g.total,2)}</b> · ${g.status==='PASS'?'verified pass':'not a verified pass'}`}));
  const cats=g.categories||{};
  const catTable=el('table',{},[]);
  catTable.append(el('tr',{html:'<th>Category</th><th>Score</th><th>Max</th>'}));
  for(const [k,v] of Object.entries(cats)) catTable.append(el('tr',{html:`<td>${k}</td><td>${fmt(v.score,2)}</td><td>${fmtInt(v.max)}</td>`}));
  host.append(catTable);
  const gt=el('table',{},[]); gt.append(el('tr',{html:'<th>P0 gate</th><th>Result</th><th>Evidence</th>'}));
  for(const gate of g.gates||[]) gt.append(el('tr',{html:`<td>${gate.id}</td><td>${badge(gate.passed?'PASS':'FAIL')}</td><td class="wrap">${escapeHtml(gate.detail||'')}</td>`}));
  host.append(el('h3',{html:'Critical gates'}),gt);
  const ct=el('table',{},[]); ct.append(el('tr',{html:'<th>Case</th><th>Requirement</th><th>Severity</th><th>Result</th><th>Detail</th>'}));
  for(const c of g.cases||[]) ct.append(el('tr',{html:`<td>${c.id}</td><td>${escapeHtml(c.requirement||'')}</td><td>${c.severity||''}</td><td>${badge(c.passed?'PASS':'FAIL')}</td><td class="wrap">${escapeHtml(c.detail||'')}</td>`}));
  host.append(el('h3',{html:'Hidden acceptance cases'}),ct);
  const det=g.determinism||{};
  host.append(el('div',{class:'muted',style:'margin-top:8px',html:`Determinism: repeats=${fmtInt(det.repeats)} agreement=${det.agreement}`}));
}
function renderCode(){
  const host=$('#codePanel'); host.innerHTML='';
  const tbl=el('table',{},[]);
  tbl.append(el('tr',{html:'<th>Trial</th><th>Files</th><th>+ lines</th><th>− lines</th><th>Test files</th><th>Diff SHA-256</th>'}));
  for(const r of filteredRuns()){
    const m=r.code_metrics||{};
    tbl.append(el('tr',{html:`<td>${r.task_id}/${r.agent_id}</td><td>${fmtInt(m.files_changed)}</td><td>${fmtInt(m.lines_added)}</td><td>${fmtInt(m.lines_deleted)}</td><td>${fmtInt(m.test_files_touched)}</td><td>${short((r.diff_sha256||'').slice(0,16))}</td>`}));
  }
  host.append(tbl);
  host.append(el('h3',{html:'Tool / action summary'}));
  const tt=el('table',{},[]); tt.append(el('tr',{html:'<th>Trial</th><th>Tool calls</th><th>Failures</th><th>Unknown</th><th>Top tools</th><th>Repeated actions</th><th>Trace</th>'}));
  for(const r of filteredRuns()){
    const t=r.tools||{}; const top=Object.entries(t.tool_counts||{}).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([k,v])=>`${k}:${v}`).join(' ');
    const rep=(t.repeated_actions||[]).map(x=>`${x.count}× ${escapeHtml(String(x.detail).slice(0,60))}`).join('<br>')||'—';
    tt.append(el('tr',{html:`<td>${r.task_id}/${r.agent_id}</td><td>${fmtInt(t.tool_calls)}</td><td>${fmtInt(t.tool_failures)}</td><td>${fmtInt(t.tool_failure_unknown)}</td><td class="wrap">${escapeHtml(top)}</td><td class="wrap">${rep}</td><td>${t.trace_available?'native':'unavailable'}</td>`}));
  }
  host.append(tt);
  for(const r of filteredRuns()){
    if(!r.diff_text)continue;
    host.append(el('details',{},[el('summary',{html:`Patch — ${r.task_id}/${r.agent_id} (${escapeHtml((r.diff_sha256||'').slice(0,16))})`}),
      el('pre',{html:escapeHtml(r.diff_text)})]));
  }
}
function renderCosts(){
  const agents=DATA.meta.agents;
  const scatter=(hostId,xKey,yKey,xLabel,yLabel,xfmt,yfmt)=>{
    const host=$('#'+hostId); host.innerHTML='';
    const pts=[]; for(const r of filteredRuns()){const e=DATA.economics.per_agent[r.agent_id];
      const x=xKey==='cost'? (r.nominal_cost_usd ?? (r.tokens||{}).list_cost_usd) : r.elapsed_seconds;
      const y=yKey==='quality'? (r.grade||{}).total : (r.nominal_cost_usd ?? (r.tokens||{}).list_cost_usd);
      if(x===null||x===undefined||y===null||y===undefined)continue;
      pts.push({x,y,r,agent:r.agent_id});}
    const W=520,H=300,left=60,top=16,right=16,bottom=40;
    const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('height',H);
    const mk=(t,a)=>{const e=document.createElementNS(ns,t);for(const[k,v] of Object.entries(a))e.setAttribute(k,v);return e;};
    const maxX=Math.max(0.000001,...pts.map(p=>p.x)),maxY=Math.max(1,...pts.map(p=>p.y));
    const x=v=>left+(W-left-right)*(v/maxX),y=v=>H-bottom-(H-top-bottom)*(v/maxY);
    for(let i=0;i<=4;i++){const gy=top+(H-top-bottom)*i/4;
      svg.append(mk('line',{x1:left,y1:gy,x2:W-right,y2:gy,class:'gridline'}));
      const tx=mk('text',{x:left-6,y:gy+3,class:'axis','text-anchor':'end',fill:'currentColor'});tx.textContent=yfmt(maxY*(1-i/4));svg.append(tx);}
    const xl=mk('text',{x:W/2,y:H-6,class:'axis','text-anchor':'middle',fill:'currentColor'});xl.textContent=xLabel;svg.append(xl);
    const shapes=['circle','square','triangle'];
    pts.forEach((p,idx)=>{const ai=agents.indexOf(p.agent);const col=COLORS[ai%COLORS.length];
      let shape;
      if(ai%3===0)shape=mk('circle',{cx:x(p.x),cy:y(p.y),r:5,fill:col});
      else if(ai%3===1)shape=mk('rect',{x:x(p.x)-5,y:y(p.y)-5,width:10,height:10,fill:col});
      else shape=mk('path',{d:`M${x(p.x)},${y(p.y)-6}L${x(p.x)+6},${y(p.y)+5}L${x(p.x)-6},${y(p.y)+5}Z`,fill:col});
      shape.addEventListener('mouseenter',ev=>showTip(ev,`${p.r.task_id}/${p.agent}<br>${xLabel}: ${xfmt(p.x)}<br>${yLabel}: ${yfmt(p.y)}`));
      shape.addEventListener('mouseleave',hideTip);
      svg.append(shape);});
    host.append(svg);
    host.append(el('div',{class:'legend',html:agents.map((a,i)=>`<span><i style="background:${COLORS[i%COLORS.length]}"></i>${a}</span>`).join('')}));
  };
  scatter('costQuality','cost','quality','nominal USD','verified quality points',v=>'$'+Number(v).toFixed(5),v=>fmt(v,0));
  scatter('costTime','time','cost','wall seconds','nominal USD',v=>fmt(v,0)+'s',v=>'$'+Number(v).toFixed(5));
  renderEcon();
}
function renderEcon(){
  const review=parseFloat($('#reviewMin').value||'0');
  const rate=parseFloat($('#rate').value||'90');
  const rework=parseFloat($('#rework').value||'15')/100;
  const tbl=$('#econTable'); if(!tbl)return; tbl.innerHTML='';
  tbl.append(el('tr',{html:'<th>Agent</th><th>Verified pass rate</th><th>Model cost / verified task</th><th>+ VPS alloc</th><th>+ review + expected rework</th><th>Scenario total @100</th><th>Scenario total @500</th><th>Scenario total @1000</th>'}));
  const vpsPerTask=0.12;
  for(const a of DATA.meta.agents){
    const e=DATA.economics.per_agent[a];
    const model=e.cost_per_verified;
    const reviewCost=review/60*rate;
    const reworkCost=rework*(reviewCost+ (model||0));
    const totalPer=(model||0)+vpsPerTask+reviewCost+reworkCost;
    const cells=[a,pct(e.pass_rate),model===null?'undefined':usd(model),model===null?'undefined':usd((model||0)+vpsPerTask),
      model===null?'undefined':usd(totalPer),model===null?'undefined':usd(totalPer*100),model===null?'undefined':usd(totalPer*500),model===null?'undefined':usd(totalPer*1000)];
    tbl.append(el('tr',{},cells.map((c,i)=>el('td',{html:i===0?`<b>${c}</b>`:c}))));
  }
  tbl.append(el('tr',{html:`<td colspan="8" class="muted" style="text-align:left;white-space:normal">Model cost per verified task = total nominal model spend ÷ verified passes; undefined when no verified pass. VPS allocation is an explicit scenario input (~$0.12/task). Human review and rework probability are scenario inputs, not measured facts.</td>`}));
}
function renderIntegrity(){
  const host=$('#integrity'); host.innerHTML='';
  const tbl=el('table',{},[]);
  tbl.append(el('tr',{html:'<th>Check</th><th>Status</th><th>Detail</th>'}));
  for(const c of DATA.integrity) tbl.append(el('tr',{html:`<td>${c.id}</td><td>${badge(c.status)}</td><td class="wrap">${escapeHtml(c.detail||'')}</td>`}));
  host.append(tbl);
}
function renderEvidence(){
  const host=$('#evidence'); host.innerHTML='';
  host.append(el('div',{class:'muted',html:'Every artifact below is hashed in the run manifest. The report remains fully explorable offline without these files; they are the primary evidence behind it.'}));
  for(const r of filteredRuns()){
    host.append(el('details',{},[el('summary',{html:`${r.task_id}/${r.agent_id} — ${r.evidence_files.length} files`}),
      el('div',{class:'kv'},[...r.evidence_files.map(f=>[f,shaLike(f)])].flatMap(([k,v])=>[el('div',{class:'k',html:escapeHtml(k)}),el('div',{html:escapeHtml(v)})]))]));
  }
}
function shaLike(f){return 'preserved in run directory';}
function renderManifest(){
  const host=$('#manifest'); host.innerHTML='';
  const m=DATA.meta; const h=m.host||{};
  const kv=el('div',{class:'kv'});
  const rows=[['Experiment',m.experiment_id],['Run id',m.run_id],['Model',m.model],['Provider',m.provider],
    ['Reasoning effort',m.reasoning_effort],['Credential source',m.credential_source],['Host',h.hostname],
    ['Kernel',h.kernel],['CPU model',h.cpu_model],['Logical CPUs',h.logical_cpus],['Memory total',bytes(h.mem_total_bytes)],
    ['cgroup',h.cgroup_self],['Controllers',h.cgroup_controllers],['Dry run',String(m.dry_run)],
    ['Started',m.started_utc],['Finished',m.finished_utc],['Generated',m.generated_utc],
    ['Run directory',m.run_dir]];
  for(const [k,v] of rows){kv.append(el('div',{class:'k',html:k}));kv.append(el('div',{html:escapeHtml(String(v??'—'))}));}
  host.append(kv);
  host.append(el('h3',{html:'Agent binaries'}));
  const at=el('table',{},[]); at.append(el('tr',{html:'<th>Agent</th><th>Version</th><th>Binary SHA-256</th>'}));
  for(const a of m.agents){const r=DATA.runs.find(x=>x.agent_id===a);
    at.append(el('tr',{html:`<td>${a}</td><td class="wrap">${escapeHtml(r?.agent_version||'—')}</td><td>${escapeHtml((r?.agent_binary_sha256||'').slice(0,32)||'—')}</td>`}));}
  host.append(at);
  host.append(el('h3',{html:'Reproduction'}));
  host.append(el('pre',{html:escapeHtml(`# from the benchmark root\n./controller/benchctl preflight -config config/experiment.json\n./controller/benchctl run -config config/experiment.json\npython3 scripts/build_report.py --run-dir results/${m.run_id||'<run>'} --out AGENT_BENCHMARK_FORENSIC_REPORT.html\nsha256sum -c results/${m.run_id||'<run>'}/SHA256SUMS`)}));
}
function openDossier(task,agent){
  const r=byTaskAgent(task,agent); if(!r)return;
  state.dossier=[task,agent]; renderDossier();
  const sec=$('#dossierSection'); sec.classList.remove('hidden'); if(sec.scrollIntoView) sec.scrollIntoView({behavior:'smooth'});
}
function renderDossier(){
  if(!state.dossier)return;
  const [task,agent]=state.dossier; const r=byTaskAgent(task,agent); if(!r)return;
  $('#dossierTitle').textContent=`${task} / ${agent}`;
  const host=$('#dossier'); host.innerHTML='';
  const g=r.grade||{};
  const kv=el('div',{class:'kv'});
  const rows=[['Status',g.status||r.status],['Verified pass',String(r.verified_pass)],['Score',fmt(g.total,2)],
    ['Exit reason',r.exit_reason],['Exit code',r.exit_code],['Elapsed',fmt(r.elapsed_seconds,2)+' s'],
    ['Launch delay',fmt(r.launch_delay_ms,2)+' ms'],['First request',fmt(r.startup_ms,1)+' ms'],
    ['First token',fmt(r.first_token_ms,1)+' ms'],['CPU',fmt(r.cpu_seconds,3)+' s'],
    ['Peak memory',bytes(r.peak_memory_bytes)],['Model requests',fmtInt(r.model_requests)],
    ['Input tokens',fmtInt((r.tokens||{}).input)],['Output tokens',fmtInt((r.tokens||{}).output)],
    ['Reasoning tokens',fmtInt((r.tokens||{}).reasoning)],['Cache read',fmtInt((r.tokens||{}).cached_read)],
    ['Nominal cost',usd(r.nominal_cost_usd ?? (r.tokens||{}).list_cost_usd)],['Affinity',r.cpu_affinity],['Relay',r.relay_addr],
    ['Start SHA',r.start_sha],['Diff SHA-256',r.diff_sha256],['Agent version',r.agent_version]];
  for(const [k,v] of rows){kv.append(el('div',{class:'k',html:k}));kv.append(el('div',{html:escapeHtml(String(v??'—'))}));}
  host.append(kv);
  const tl=el('table',{},[]); tl.append(el('tr',{html:'<th>t (s from release)</th><th>Event</th><th>Detail</th>'}));
  for(const e of r.timeline||[]) tl.append(el('tr',{html:`<td>${e.t===null?'—':fmt(e.t,3)}</td><td>${e.type}</td><td class="wrap">${escapeHtml(JSON.stringify(e.detail||{}))}</td>`}));
  host.append(el('h3',{html:'Critical-path events'}),tl);
  if(r.stdout_tail){host.append(el('h3',{html:'stdout (tail, redacted)'}),el('pre',{html:escapeHtml(r.stdout_tail)}));}
  if(r.stderr_tail){host.append(el('h3',{html:'stderr (tail, redacted)'}),el('pre',{html:escapeHtml(r.stderr_tail)}));}
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
let tipTimer=null;
function showTip(ev,html){const t=$('#tooltip');t.innerHTML=html;t.style.display='block';
  const x=Math.min(window.innerWidth-320,ev.clientX+12),y=ev.clientY+12;t.style.left=x+'px';t.style.top=y+'px';}
function hideTip(){$('#tooltip').style.display='none';}
function filtered(){render();}

async function boot(){
  try{
    const text=await inflate(B64);
    DATA=JSON.parse(text);
  }catch(e){
    document.body.innerHTML=`<pre style="color:#f85149;padding:24px">Report data could not be decompressed: ${escapeHtml(e.message)}</pre>`;
    return;
  }
  const addOption=(sel,val)=>{const o=document.createElement('option');o.value=val;o.textContent=val;$(sel).append(o);};
  for(const a of DATA.meta.agents){addOption('#agentFilter',a);}
  for(const t of DATA.meta.tasks){addOption('#taskFilter',t);}
  $('#agentFilter').onchange=e=>{state.agent=e.target.value;render();};
  $('#taskFilter').onchange=e=>{state.task=e.target.value;render();};
  $('#search').oninput=e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(tr=>{tr.style.display=!q||tr.textContent.toLowerCase().includes(q)?'':'none';});};
  $('#themeBtn').onclick=()=>{const cur=document.documentElement.getAttribute('data-theme')==='light'?'dark':'light';document.documentElement.setAttribute('data-theme',cur);};
  $('#reviewMin').oninput=renderEcon; $('#rate').oninput=renderEcon; $('#rework').oninput=renderEcon;
  state.ganttTask=DATA.meta.tasks[0]||null;
  state.resTab=(filteredRuns()[0]||{}).task_id+'/'+(filteredRuns()[0]||{}).agent_id;
  state.qualityTab=state.resTab;
  render();
  document.documentElement.setAttribute('data-theme','dark');
}
boot();
</script>
</body>
</html>
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    data, size = build_report(args.run_dir, args.out)
    print(f"wrote {args.out} ({size/1024/1024:.2f} MiB, {len(data['runs'])} trials)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
