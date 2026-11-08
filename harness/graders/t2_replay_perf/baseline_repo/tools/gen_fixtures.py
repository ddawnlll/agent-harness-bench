#!/usr/bin/env python3
"""Regenerate the committed event fixtures and the small golden output.

Usage: python3 tools/gen_fixtures.py

The fixtures are deterministic: running this script on any machine produces
byte-identical files.  Do not commit fixtures generated with other seeds.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from replay.events import PROFILES, generate_events, profile_kwargs, write_events  # noqa: E402

FIXTURES = [
    ("small", 11, "data/small.jsonl"),
    ("medium", 23, "data/medium.jsonl"),
    ("adversarial", 37, "data/adversarial_small.jsonl"),
]


def main():
    for profile, seed, rel in FIXTURES:
        kwargs = profile_kwargs(profile)
        n = PROFILES[profile]["n"]
        events = generate_events(seed, n, **kwargs)
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_events(path, events)
        print("wrote %s (%d events, %d bytes)"
              % (rel, len(events), os.path.getsize(path)))

    golden = os.path.join(ROOT, "tests", "golden", "small.json")
    os.makedirs(os.path.dirname(golden), exist_ok=True)
    subprocess.check_call(
        [sys.executable, "-m", "replay.cli",
         "--events", os.path.join(ROOT, "data/small.jsonl"),
         "--out", golden],
        cwd=ROOT, stdout=subprocess.DEVNULL)
    print("wrote tests/golden/small.json (%d bytes)"
          % os.path.getsize(golden))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
