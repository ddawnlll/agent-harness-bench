"""Tiny helper shared by the negative-control patches."""

from __future__ import annotations

import json
import os
import sys


def replace_once(path: str, old: str, new: str, label: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    count = source.count(old)
    if count != 1:
        raise SystemExit(
            f"negative patch {label!r} expected exactly 1 match in {path}, found {count}"
        )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(source.replace(old, new, 1))
    print(f"[{label}] patched {os.path.basename(path)}")


def core_path(workspace: str) -> str:
    path = os.path.join(workspace, "coordinator", "core.py")
    if not os.path.exists(path):
        raise SystemExit(f"coordinator/core.py not found in {workspace}")
    return path


def emit(workspace: str, payload) -> None:
    with open(os.path.join(workspace, "negative_applied.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def main_wrapper(label: str, patch):
    workspace = sys.argv[1] if len(sys.argv) > 1 else None
    if not workspace:
        raise SystemExit(f"usage: {label} patch.py WORKSPACE")
    path = core_path(workspace)
    patch(path, workspace)
    emit(workspace, {"negative": label, "applied": True})
