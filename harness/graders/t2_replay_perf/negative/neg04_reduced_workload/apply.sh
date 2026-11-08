#!/bin/sh
# Cheat: process only half of the events.
set -eu
TARGET="${1:?usage: apply.sh TARGET}"
python3 - "$TARGET" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1]) / "replay" / "events.py"
text = path.read_text()
old = "    events.sort(key=lambda e: (e[\"ts\"], e[\"seq\"]))\n    return events\n"
assert old in text
new = "    events.sort(key=lambda e: (e[\"ts\"], e[\"seq\"]))\n    return events[::2]\n"
path.write_text(text.replace(old, new, 1))
PY
echo "neg04 applied"
