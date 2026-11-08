#!/usr/bin/env python3
"""Write results/SHA256SUMS over the published dataset and telemetry."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
SKIP = {"SHA256SUMS"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    files = []
    for p in sorted(RESULTS.rglob("*")):
        if p.is_file() and p.name not in SKIP:
            files.append(p)
    lines = [f"{sha(p)}  {p.relative_to(RESULTS)}" for p in files]
    (RESULTS / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines)} hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
