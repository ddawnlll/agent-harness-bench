#!/usr/bin/env python3
"""Create a SYNTHETIC demo run directory (nine mock trials) for pipeline tests."""
import json, os, random, sys, time
random.seed(7)
OUT = sys.argv[1] if len(sys.argv) > 1 else "results/SYNTHETIC_DEMO"
AGENTS = ["pi-bolt", "jcode", "hax"]
TASKS = ["t1", "t2", "t3"]
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "SYNTHETIC"), "w").write("SYNTHETIC DATA - not a benchmark result\n")
now = time.time()
os.makedirs(os.path.join(OUT, "telemetry"), exist_ok=True)
cfg = {"experiment_id": "SYNTHETIC-demo", "model": "deepseek-v4.1-flash",
       "provider": "opencode-go", "reasoning_effort": "low",
       "agents": [{"id": a} for a in AGENTS],
       "rounds": [{"round_id": f"r{i+1}", "task_id": t, "duration_seconds": 1800}
                  for i, t in enumerate(TASKS)]}
runs = []
api = []
for ti, task in enumerate(TASKS):
    for ai, agent in enumerate(AGENTS):
        score = {"pi-bolt": [96, 91, 88], "jcode": [89, 84, 92], "hax": [78, 71, 69]}[agent][ti]
        passed = score >= 80
        elapsed = 700 + ti * 90 + ai * 50 + random.randint(-60, 60)
        cpu = 40 + ai * 9 + ti * 4 + random.random()
        mem = (300 + ai * 40 + ti * 20) * 1024 * 1024
        tin, tout = 90000 + ai * 15000, 6000 + ti * 900
        cost = (tin * 0.14 + tout * 0.28) / 1_000_000
        run = {"task_id": task, "agent_id": agent, "run_id": "SYNTHETIC_DEMO",
               "round_id": f"r{ti+1}", "status": "completed", "exit_code": 0,
               "exit_reason": "AGENT_EXITED_OK", "elapsed_seconds": elapsed,
               "launch_delay_ms": 3.1, "startup_ms": 1200 + ai * 300,
               "first_token_ms": 1800 + ai * 400, "cpu_seconds": cpu,
               "cpu_user_seconds": cpu * 0.9, "cpu_system_seconds": cpu * 0.1,
               "cpu_throttled_seconds": 0.4, "peak_memory_bytes": mem, "oom_events": 0,
               "tokens": {"input": tin, "output": tout, "total": tin + tout,
                          "reasoning": tout // 3, "cached_read": tin // 2,
                          "cached_write": 0, "cost_usd": round(cost, 6),
                          "list_cost_usd": round(cost, 6), "allowance_usd": round(cost, 6),
                          "provenance": "SYNTHETIC"},
               "model_requests": 10 + ai * 3, "api_wall_seconds": elapsed * 0.4,
               "api_errors": 0, "api_throttle_events": 0,
               "agent_version": f"{agent} SYNTHETIC", "agent_binary_sha256": "0" * 64,
               "cpu_affinity": ["0,1", "2,3", "4,5"][ai], "relay_addr": "0.0.0.0:0",
               "stdout_tail": "SYNTHETIC stdout", "stderr_tail": "",
               "start_utc": "2026-01-01T00:00:00Z", "end_utc": "2026-01-01T00:12:00Z",
               "start_sha": "1" * 40, "notes": ["SYNTHETIC"],
               "code_metrics": {"files_changed": 2 + ai, "lines_added": 80 + 10 * ti,
                                "lines_deleted": 12, "test_files_touched": 1},
               "diff_sha256": "2" * 64, "archive_sha256": "3" * 64, "tree_sha256": "4" * 64,
               "diff_text": "--- SYNTHETIC demo patch (not real)\n+++ b/example.py\n+print('synthetic')\n",
               "grade": {"task_id": task, "status": "PASS" if passed else "FAIL",
                         "total": score,
                         "categories": {"correctness_adversarial": {"score": score * 0.5, "max": 50},
                                        "safety_invariants": {"score": score * 0.25, "max": 25},
                                        "regression": {"score": 13, "max": 15},
                                        "maintainability": {"score": 8, "max": 10}},
                         "gates": [{"id": f"G{i}", "severity": "P0",
                                    "passed": passed or i > 1, "detail": "SYNTHETIC",
                                    "evidence": "hidden"} for i in range(1, 5)],
                         "cases": [{"id": f"h{i:02d}", "requirement": f"req{i}",
                                    "severity": "safety" if i < 4 else "correctness",
                                    "passed": passed or i % 3, "detail": "SYNTHETIC"} for i in range(1, 7)],
                         "determinism": {"repeats": 2, "agreement": True},
                         "metrics": {"integrity": {"modified": []}}},
               "timeline": [{"type": "PROCESS_EXEC_START", "t": 0.003, "detail": {}},
                            {"type": "AGENT_EXIT", "t": elapsed, "detail": {"exit_code": 0}},
                            {"type": "GRADER_STARTED", "t": elapsed, "detail": {}}],
               "latency": {"count": 10 + ai * 3, "median_ms": 900 + ai * 100,
                           "p90_ms": 1800 + ai * 200, "max_ms": 4000,
                           "ttfb_median_ms": 700, "ttfb_p90_ms": 1500,
                           "output_tok_per_s": 40.0},
               "resource_series": [{"t": s, "cpu_cores": 1.4 + random.random(),
                                    "mem_mb": 200 + s * 0.2, "mem_peak_mb": 400, "pids": 4,
                                    "throttled_us": 0, "cpu_some_us": 0, "mem_some_us": 0}
                                   for s in range(0, int(elapsed), 30)],
               "api_series": [], "api_events_count": 10 + ai * 3,
               "api_models": ["deepseek-v4.1-flash"], "effort_rewrites": 0,
               "effort_absent": 0, "process_samples": 20,
               "verified_pass": passed, "evidence_files": ["run_manifest.json"],
               "grader_wall_seconds": 20.0}
        # synthetic API series
        base = now - 3600
        cum = 0
        for k in range(10 + ai * 3):
            cum += tin // (10 + ai * 3)
            run["api_series"].append({"t": k * 60.0, "dur_ms": 900 + ai * 100,
                                      "ttfb_ms": 700, "input": tin // (10 + ai * 3),
                                      "output": tout // (10 + ai * 3), "reasoning": 20,
                                      "cached": 0, "cost": cost / (10 + ai * 3),
                                      "status": 200, "model": "deepseek-v4.1-flash",
                                      "effort_req": "low", "effort_fwd": "low", "stream": True})
            api.append({"task_id": task, "agent_id": agent, "utc_ns": int((base + k * 60) * 1e9),
                        "duration_ms": 900 + ai * 100, "ttfb_ms": 700,
                        "input_tokens": tin // (10 + ai * 3), "output_tokens": tout // (10 + ai * 3),
                        "reasoning_tokens": 20, "cached_read_tokens": 0,
                        "provider_reported_cost_usd": cost / (10 + ai * 3),
                        "reasoning_effort_requested": "low", "reasoning_effort_forwarded": "low",
                        "reasoning_effort_rewritten": False, "model_id_returned": "deepseek-v4.1-flash",
                        "http_status": 200, "stream": True})
        runs.append(run)
        d = os.path.join(OUT, "runs", task, agent)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "run_manifest.json"), "w") as fh:
            json.dump({k: v for k, v in run.items() if k not in ("grade",)}, fh, indent=2)
        with open(os.path.join(d, "hidden_grader.json"), "w") as fh:
            json.dump(run["grade"], fh, indent=2)
summary = {"run_id": "SYNTHETIC_DEMO", "experiment_id": "SYNTHETIC-demo",
           "started_utc": "2026-01-01T00:00:00Z", "finished_utc": "2026-01-01T01:00:00Z",
           "wall_seconds": 3600, "config": cfg,
           "host": {"hostname": "synthetic-host", "kernel": "synthetic", "cpu_model": "synthetic",
                    "logical_cpus": 6, "mem_total_bytes": 12 * 1024 ** 3, "cgroup_self": "/synthetic",
                    "cgroup_controllers": "cpu memory pids"},
           "runs": runs, "credential_source": "synthetic"}
with open(os.path.join(OUT, "summary_raw.json"), "w") as fh:
    json.dump(summary, fh, indent=2)
with open(os.path.join(OUT, "telemetry", "api_usage.jsonl"), "w") as fh:
    for a in api:
        fh.write(json.dumps(a) + "\n")
with open(os.path.join(OUT, "telemetry", "environment.json"), "w") as fh:
    json.dump({"host": summary["host"], "credential_source": "synthetic", "dry_run": False}, fh)
open(os.path.join(OUT, "telemetry", "events.jsonl"), "w").write("")
open(os.path.join(OUT, "telemetry", "resource_samples.jsonl"), "w").write("")
open(os.path.join(OUT, "telemetry", "process_tree.jsonl"), "w").write("")
open(os.path.join(OUT, "SHA256SUMS"), "w").write("# SYNTHETIC hash seal\n")
print("synthetic run written to", OUT)
