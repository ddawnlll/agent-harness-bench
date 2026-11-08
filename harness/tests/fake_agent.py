#!/usr/bin/env python3
"""Deterministic fake competitor used only for controller validation.

It never modifies hidden material, makes at most one tiny model call through
the local relay (to exercise accounting), edits a harmless file, and exits.
Every artifact produced under a dry run is labelled DRY_RUN.
"""
import json
import os
import sys
import time
import urllib.request

agent, task, ws = sys.argv[1], sys.argv[2], sys.argv[3]
relay = os.environ.get("HAX_OPENAI_BASE_URL", "")
events = []

def tool(name, ok=True, detail=""):
    rec = {"type": "tool", "name": name, "ok": ok, "detail": detail,
           "mono_ms": round((time.monotonic() - t0) * 1000, 1)}
    events.append(rec)
    print(json.dumps({"event": "tool", **rec}), flush=True)

t0 = time.monotonic()
print(json.dumps({"event": "start", "agent": agent, "task": task, "dry_run": True}), flush=True)
tool("read_task", detail="TASK.md")

# One relay round-trip so token/accounting plumbing is exercised.
if relay:
    try:
        body = json.dumps({
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 8, "reasoning_effort": "low", "stream": False,
        }).encode()
        req = urllib.request.Request(relay.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            json.loads(r.read().decode())
        tool("model_request", ok=True)
    except Exception as exc:  # noqa: BLE001
        tool("model_request", ok=False, detail=type(exc).__name__)
        print(json.dumps({"event": "error", "detail": str(exc)[:200]}), flush=True)

# Harmless workspace edit, attributed to the fake agent.
note = os.path.join(ws, "DRY_RUN_AGENT_NOTE.txt")
with open(note, "w", encoding="utf-8") as fh:
    fh.write(f"dry-run fake agent {agent}; no real solution\n")
tool("write_file", detail="DRY_RUN_AGENT_NOTE.txt")

# Simulate some CPU work so cgroup accounting is measurable.
x = 0
for i in range(400000):
    x += i % 7
tool("compute", detail=str(x))
time.sleep(2)
print(json.dumps({"event": "finish", "agent": agent, "elapsed_ms": round((time.monotonic() - t0) * 1000, 1)}), flush=True)
