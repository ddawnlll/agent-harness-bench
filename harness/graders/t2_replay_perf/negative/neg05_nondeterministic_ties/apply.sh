#!/bin/sh
# Optimised, but tie-breaking inside a price level is random.
set -eu
TARGET="${1:?usage: apply.sh TARGET}"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$HERE/../../oracle/apply_oracle.sh" "$TARGET" >/dev/null
python3 - "$TARGET" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1]) / "replay" / "engine.py"
text = path.read_text()
text = text.replace("import hashlib\n", "import hashlib\nimport random\n", 1)
old = "            maker = book.head(opposite, price)\n"
assert old in text
new = ("            level = book.level_orders(opposite, price)\n"
       "            maker = level[int(random.random() * len(level)) % len(level)]\n")
path.write_text(text.replace(old, new, 1))
PY
echo "neg05 applied"
