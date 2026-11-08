#!/usr/bin/env python3
"""Create the sanitized public derivative of the sealed forensic report.

The original report remains untouched outside this repository. This script
decompresses its embedded dataset, removes host-specific identifiers, rebuilds
the single-file HTML deterministically and records both hashes plus the exact
transformation in reports/SEAL.json.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

PAYLOAD_RE = re.compile(r'(<script id="bench-data-gz" type="text/plain">)(.+?)(</script>)', re.S)
REPLACEMENTS = [
    (re.compile(r"/home/[A-Za-z0-9._-]+"), "/home/researcher"),
    (re.compile(r"12735\.example\.com"), "bench-host"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "0.0.0.0"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "<redacted-key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<redacted-token>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"), "Bearer <redacted>"),
]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sanitize_text(s: str):
    count = 0
    for rx, repl in REPLACEMENTS:
        s, n = rx.subn(repl, s)
        count += n
    return s, count


def sanitize_obj(obj):
    if isinstance(obj, str):
        return sanitize_text(obj)[0]
    if isinstance(obj, list):
        return [sanitize_obj(v) for v in obj]
    if isinstance(obj, dict):
        return {k: sanitize_obj(v) for k, v in obj.items()}
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seal", default="reports/SEAL.json")
    args = ap.parse_args()
    src = Path(args.source)
    out = Path(args.out)
    raw = src.read_bytes()
    html = raw.decode("utf-8")
    m = PAYLOAD_RE.search(html)
    if not m:
        raise SystemExit("embedded dataset not found")
    payload = gzip.decompress(base64.b64decode(m.group(2))).decode("utf-8")
    data = json.loads(payload)
    sanitized = sanitize_obj(data)
    reencoded = json.dumps(sanitized, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    packed = base64.b64encode(gzip.compress(reencoded, 9, mtime=0)).decode("ascii")
    html_out = html[:m.start(2)] + packed + html[m.end(2):]
    html_out, outer_count = sanitize_text(html_out)
    # Count replacements inside the payload by diffing the decoded strings.
    _, inner_count = sanitize_text(payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")

    findings = []
    checks = [
        (re.compile(r"/home/(?!researcher)[A-Za-z0-9._-]+"), "non-public home path"),
        (re.compile(r"12735\.example\.com"), "hostname"),
        (re.compile(r"\bquant\b"), "username"),
        (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "secret-key shape"),
        (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "github token shape"),
        (re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"), "bearer token"),
    ]
    m2 = PAYLOAD_RE.search(html_out)
    payload2 = gzip.decompress(base64.b64decode(m2.group(2))).decode("utf-8")
    for rx, label in checks:
        if rx.search(html_out):
            findings.append("outer:" + label)
        if rx.search(payload2):
            findings.append("payload:" + label)

    seal = {
        "schema": "agent-harness-bench/report-seal/1",
        "source_file": src.name,
        "source_sha256": sha256_bytes(raw),
        "source_bytes": len(raw),
        "public_file": out.name,
        "public_sha256": sha256_bytes(html_out.encode("utf-8")),
        "public_bytes": len(html_out.encode("utf-8")),
        "transformation": {
            "script": "tools/sanitize_report.py",
            "replacements": [
                "home directories -> /home/researcher",
                "hostname -> bench-host",
                "IP addresses -> 0.0.0.0",
                "credential-shaped strings -> <redacted-*>",
            ],
            "replacement_count_outer": outer_count,
            "replacement_count_payload": inner_count,
            "gzip_mtime": 0,
            "data_semantics": "unchanged except identifier redaction",
        },
        "banned_pattern_findings": findings,
        "status": "PASS" if not findings else "FAIL",
    }
    Path(args.seal).parent.mkdir(parents=True, exist_ok=True)
    Path(args.seal).write_text(json.dumps(seal, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": seal["status"], "findings": findings,
                      "source_sha256": seal["source_sha256"][:16],
                      "public_sha256": seal["public_sha256"][:16]}))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
