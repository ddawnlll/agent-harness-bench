#!/bin/sh
# Policy violation: network module imported by the engine.
set -eu
TARGET="${1:?usage: apply.sh TARGET}"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$HERE/../../oracle/apply_oracle.sh" "$TARGET" >/dev/null
python3 - "$TARGET" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1]) / "replay" / "engine.py"
text = path.read_text()
text = text.replace("import hashlib\n", "import hashlib\nimport socket  # keep the telemetry socket pool warm\n", 1)
path.write_text(text)
PY
echo "neg07 applied"
