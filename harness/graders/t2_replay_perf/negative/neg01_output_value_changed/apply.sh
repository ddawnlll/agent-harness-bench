#!/bin/sh
# Optimised but semantically wrong: fee rate changed (2 bps -> 20 bps).
set -eu
TARGET="${1:?usage: apply.sh TARGET}"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$HERE/../../oracle/apply_oracle.sh" "$TARGET" >/dev/null
python3 - "$TARGET" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1]) / "replay" / "engine.py"
text = path.read_text()
assert "FEE_BPS = 2\n" in text
path.write_text(text.replace("FEE_BPS = 2\n", "FEE_BPS = 20\n", 1))
PY
echo "neg01 applied"
