#!/usr/bin/env python3
"""Minimal, secret-safe OpenCode Go preflight. Never prints the key."""
import json, os, sys, time, urllib.request, urllib.error, hashlib

AUTH = os.path.expanduser("~/.pi/agent/auth.json")
KEYFILE = os.path.expanduser("~/opencode.txt")
ENDPOINT = "https://opencode.ai/inference/go/openai/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

def load_key():
    if os.path.exists(KEYFILE):
        k = open(KEYFILE, "r").read().strip()
        if k: return k, "opencode.txt"
    if os.path.exists(AUTH):
        d = json.load(open(AUTH))
        v = d.get("opencode-go") or {}
        k = (v.get("key") or "").strip()
        if k: return k, "pi_auth_store"
    return None, None

key, src = load_key()
if not key:
    print(json.dumps({"ok": False, "error": "NO_CREDENTIAL", "detail": "~/opencode.txt absent and no storable key"}))
    sys.exit(2)

payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    "max_tokens": 8,
    "reasoning_effort": "low",
    "stream": False,
}
req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), method="POST")
req.add_header("Authorization", "Bearer " + key)
req.add_header("Content-Type", "application/json")
t0 = time.monotonic()
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        body = r.read().decode("utf-8", "replace")
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        status = r.status
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", "replace")
    hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
    status = e.code
except Exception as e:
    print(json.dumps({"ok": False, "error": "NETWORK", "detail": type(e).__name__ + ": " + str(e)[:200],
                      "credential_source": src, "endpoint": ENDPOINT}))
    sys.exit(3)
dt = (time.monotonic() - t0) * 1000
rec = {"ok": 200 <= status < 300, "http_status": status, "latency_ms": round(dt, 1),
       "endpoint": ENDPOINT, "model_requested": MODEL, "reasoning_effort_requested": "low",
       "credential_source": src, "credential_sha256_12": hashlib.sha256(key.encode()).hexdigest()[:12],
       "provider_request_id": hdrs.get("x-request-id") or hdrs.get("request-id"),
       "rate_limit_headers": {k: hdrs[k] for k in hdrs if "ratelimit" in k or "retry-after" in k}}
try:
    j = json.loads(body)
    rec["model_returned"] = j.get("model")
    rec["usage"] = j.get("usage")
    rec["service_tier"] = j.get("service_tier")
    rec["content_len"] = len((j.get("choices") or [{}])[0].get("message", {}).get("content") or "")
except Exception:
    rec["raw_body_first_300"] = body[:300]
os.makedirs(os.path.expanduser("~/agent-benchmark/evidence"), exist_ok=True)
with open(os.path.expanduser("~/agent-benchmark/evidence/api_preflight.json"), "w") as f:
    json.dump(rec, f, indent=2)
print(json.dumps(rec, indent=2))
