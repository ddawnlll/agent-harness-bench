#!/usr/bin/env python3
"""Stage the benchmark controller, tasks, graders and helper scripts into the
publication repository, stripping host-specific identifiers.

Only source is copied: no run workspaces, raw logs, credential files, or sealed
evidence trees. The transformation is recorded in harness/STAGING.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("SEALED_BENCH_SOURCE", "<sealed-benchmark-source>"))
OUT = ROOT

REPLACEMENTS = [
    (re.compile("/home/" + "qu" + "ant"), "/home/researcher"),
    (re.compile(r"12735\.example\.com"), "bench-host"),
    (re.compile(r"/sys/fs/cgroup/[^\s\"']+"), "<cgroup>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "0.0.0.0"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "<redacted-key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<redacted-token>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"), "Bearer <redacted>"),
]

SKIP_DIRS = {".git", "__pycache__", "node_modules", "workspaces", "logs", "evidence"}
SKIP_FILES = {"benchctl", ".DS_Store"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    count = 0
    for rx, repl in REPLACEMENTS:
        text, n = rx.subn(repl, text)
        count += n
    if count:
        path.write_text(text, encoding="utf-8")
    return count


def copy_tree(src: Path, dst: Path, record: dict):
    for dp, dns, fns in os.walk(src):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        rel = Path(dp).relative_to(src)
        target = dst / rel
        target.mkdir(parents=True, exist_ok=True)
        for fn in fns:
            if fn in SKIP_FILES or fn.endswith((".pyc", ".log")):
                continue
            s = Path(dp) / fn
            t = target / fn
            shutil.copy2(s, t)
            n = sanitize_file(t)
            record["files"][str(t.relative_to(ROOT))] = {"sha256": sha(t), "redactions": n}


def main():
    record = {"schema": "agent-harness-bench/staging/1", "source_root": "<sealed-benchmark>",
              "copied": [], "files": {}, "excluded": [
                  "results/", "workspaces/", "logs/", "evidence/", "patches/",
                  "compiled binaries", ".git directories", "raw stdout/stderr logs"]}
    for name in ["controller", "tasks", "graders", "scripts", "tests"]:
        src = SRC / name
        if not src.exists():
            raise SystemExit(f"missing source {src}")
        dst = OUT / "harness" / name
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        copy_tree(src, dst, record)
        record["copied"].append(f"harness/{name}")

    # Publication-safe config template with relative paths.
    cfg = json.loads((SRC / "config" / "experiment.json").read_text())
    cfg["experiment_id"] = "agent-harness-bench-public-example"
    cfg["results_root"] = "results/local"
    cfg["workspaces_root"] = "workspaces"
    for rnd in cfg["rounds"]:
        rnd["task_repo"] = f"tasks/{rnd['task_id']}/repo"
        rnd["grader_script"] = f"graders/{rnd['task_id']}/grade.py"
    cfg_path = OUT / "config" / "experiment.example.json"
    cfg_path.parent.mkdir(exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    record["files"]["config/experiment.example.json"] = {"sha256": sha(cfg_path), "redactions": 0}

    (OUT / "harness" / "STAGING.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"staged {len(record['files'])} files, "
          f"{sum(v['redactions'] for v in record['files'].values())} redactions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
