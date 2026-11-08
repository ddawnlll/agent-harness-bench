#!/usr/bin/env python3
"""Release gate: secrets, identifiers, broken links and unsafe artifacts.

A finding here is a release blocker, not a warning.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 10 * 1024 * 1024

TEXT_SUFFIXES = {".md", ".py", ".js", ".json", ".jsonl", ".html", ".css", ".svg",
                 ".yml", ".yaml", ".toml", ".cff", ".txt", ".example", ".go", ".mod"}

PATTERNS = [
    (re.compile(r"/home/(?!researcher\b|<user>)[A-Za-z0-9._-]+"), "non-public home path"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+"), "macOS home path"),
    (re.compile(r"12735\.example\.com"), "benchmark hostname"),
    (re.compile(r"\bquant\b"), "operator username"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "secret-key shape"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token shape"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"), "bearer token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
]
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ALLOWED_IPS = {"0.0.0.0", "127.0.0.1"}


def decompress_report(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r'<script[^>]*type="text/plain"[^>]*>(.+?)</script>', text, re.S):
        blob = m.group(1).strip()
        try:
            out.append(gzip.decompress(base64.b64decode(blob)).decode("utf-8", "replace"))
        except Exception:
            pass
    return out


def files():
    for p in sorted(ROOT.rglob("*")):
        if p.is_dir() or ".git" in p.parts or "node_modules" in p.parts:
            continue
        if "__pycache__" in p.parts:
            continue
        yield p


def main() -> int:
    problems = []
    warnings = []
    scanned = 0
    for p in files():
        rel = str(p.relative_to(ROOT))
        size = p.stat().st_size
        if size > MAX_BYTES:
            problems.append(f"{rel}: {size/1e6:.1f} MB exceeds the 10 MB publishing limit")
        if rel.startswith("results/run-") or "/workspaces/" in rel or rel.endswith(".pyc"):
            problems.append(f"{rel}: raw run artifact must not be published")
        if p.name in {"opencode.txt", ".env", "id_rsa", "id_ed25519"}:
            problems.append(f"{rel}: credential-shaped filename")
        if p.suffix not in TEXT_SUFFIXES and p.suffix != "":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for rx, label in PATTERNS:
            for m in rx.finditer(text):
                problems.append(f"{rel}: {label}: {m.group(0)[:80]}")
        for m in IP_RE.finditer(text):
            if m.group(0) not in ALLOWED_IPS:
                problems.append(f"{rel}: public/private IP address {m.group(0)}")
        if p.suffix == ".html":
            for blob in decompress_report(p):
                for rx, label in PATTERNS:
                    if rx.search(blob):
                        problems.append(f"{rel}: compressed payload contains {label}")
                for m in IP_RE.finditer(blob):
                    if m.group(0) not in ALLOWED_IPS:
                        problems.append(f"{rel}: compressed payload contains IP {m.group(0)}")

    # report seal must pass and match the file
    seal_path = ROOT / "reports" / "SEAL.json"
    report_path = ROOT / "reports" / "AGENT_BENCHMARK_FORENSIC_REPORT.html"
    if not seal_path.exists() or not report_path.exists():
        problems.append("forensic report or its seal is missing")
    else:
        seal = json.loads(seal_path.read_text())
        actual = hashlib.sha256(report_path.read_bytes()).hexdigest()
        if seal.get("status") != "PASS":
            problems.append("report sanitization seal is not PASS")
        if seal.get("public_sha256") != actual:
            problems.append("forensic report hash does not match its seal")
        if seal.get("source_sha256") == seal.get("public_sha256"):
            warnings.append("report hashes are identical; sanitization may not have run")

    # dataset reconciliation
    data_path = ROOT / "results" / "publication-data.json"
    if not data_path.exists():
        problems.append("publication dataset missing")
    else:
        data = json.loads(data_path.read_text())
        if data.get("reconciliation", {}).get("status") != "OK":
            problems.append("dataset reconciliation is not OK")
        if len(data.get("trials", [])) != 9:
            problems.append("dataset does not contain 9 trials")
        if data.get("schema") != "agent-harness-bench/public-data/1":
            problems.append("unexpected dataset schema")

    # figures and links in markdown
    for fig in ["hero", "race", "safety", "timing", "economics", "resources"]:
        for theme in ["light", "dark"]:
            if not (ROOT / "assets" / f"{fig}-{theme}.svg").exists():
                problems.append(f"missing figure assets/{fig}-{theme}.svg")
    link_re = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for md in list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md")):
        rel = str(md.relative_to(ROOT))
        for target in link_re.findall(md.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            clean = target.split("#", 1)[0]
            if not clean:
                continue
            resolved = (md.parent / clean).resolve()
            if not resolved.exists():
                problems.append(f"{rel}: broken link -> {target}")

    # site internal links and assets
    index = ROOT / "site" / "index.html"
    if not index.exists():
        problems.append("site/index.html missing")
    else:
        html = index.read_text(encoding="utf-8")
        for target in re.findall(r'(?:href|src)="([^"]+)"', html):
            if target.startswith(("http://", "https://", "#", "data:", "mailto:")):
                continue
            if not (index.parent / target.split("#")[0]).exists():
                problems.append(f"site: broken local reference -> {target}")

    if problems:
        print("PUBLISH CHECK FAILED")
        for p in problems:
            print("  -", p)
        return 1
    print(f"PUBLISH CHECK OK: {scanned} text files scanned, 0 secret/identifier findings, "
          f"links and report seal verified, {len(warnings)} warnings")
    for w in warnings:
        print("  warning:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
