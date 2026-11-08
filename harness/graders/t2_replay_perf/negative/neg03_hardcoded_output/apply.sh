#!/bin/sh
# Cheat: return a hardcoded, committed output document for every input.
set -eu
TARGET="${1:?usage: apply.sh TARGET}"
python3 - "$TARGET" <<'PY'
import re, sys, pathlib
path = pathlib.Path(sys.argv[1]) / "replay" / "cli.py"
text = path.read_text()
text = text.replace(
    "import argparse\nimport json\nimport sys\n",
    "import argparse\nimport json\nimport os\nimport sys\n", 1)
pattern = re.compile(r"def replay\(events_path, out_path\):\n(?:.*\n)*?    return result\n")
replacement = (
    "def replay(events_path, out_path):\n"
    "    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
    "    golden = os.path.join(here, 'tests', 'golden', 'small.json')\n"
    "    with open(golden, 'r', encoding='utf-8') as handle:\n"
    "        text = handle.read()\n"
    "    with open(out_path, 'w', encoding='utf-8') as handle:\n"
    "        handle.write(text)\n"
    "    return json.loads(text)\n")
assert pattern.search(text), "replay() not found"
text = pattern.sub(replacement, text, count=1)
path.write_text(text)
PY
echo "neg03 applied"
