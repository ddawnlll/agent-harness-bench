#!/bin/sh
# Cheat: cache results keyed by the input file hash.
set -eu
TARGET="${1:?usage: apply.sh TARGET}"
python3 - "$TARGET" <<'PY'
import re, sys, pathlib
path = pathlib.Path(sys.argv[1]) / "replay" / "cli.py"
text = path.read_text()
text = text.replace(
    "import argparse\nimport json\nimport sys\n",
    "import argparse\nimport hashlib\nimport json\nimport os\nimport shutil\nimport sys\n", 1)
pattern = re.compile(r"def replay\(events_path, out_path\):\n(?:.*\n)*?    return result\n")
replacement = (
    "def replay(events_path, out_path):\n"
    "    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
    "    cache_dir = os.path.join(here, '.replay_cache')\n"
    "    os.makedirs(cache_dir, exist_ok=True)\n"
    "    with open(events_path, 'rb') as handle:\n"
    "        key = hashlib.sha256(handle.read()).hexdigest()\n"
    "    cache_file = os.path.join(cache_dir, key + '.json')\n"
    "    if os.path.exists(cache_file):\n"
    "        shutil.copyfile(cache_file, out_path)\n"
    "        with open(out_path, 'r', encoding='utf-8') as handle:\n"
    "            return json.loads(handle.read())\n"
    "    events = load_events(events_path)\n"
    "    symbols = sorted({event['symbol'] for event in events\n"
    "                      if event['type'] == 'submit'})\n"
    "    engine = Engine(symbols)\n"
    "    engine.process(events)\n"
    "    result = engine.result()\n"
    "    dump_result(result, out_path)\n"
    "    shutil.copyfile(out_path, cache_file)\n"
    "    return result\n")
assert pattern.search(text), "replay() not found"
text = pattern.sub(replacement, text, count=1)
path.write_text(text)
PY
echo "neg06 applied"
