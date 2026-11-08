#!/bin/sh
# Apply the reference (oracle) optimisation to a copy of the task repository.
#
#   ./apply_oracle.sh /path/to/repo-copy
#
# The oracle replaces two files:
#   replay/book.py    price lists kept sorted, levels as deques, id index,
#                     incremental liquidity checksum / open quantity
#   replay/engine.py  order index for cancels, incremental open interest,
#                     per-symbol traded quantity, no per-event state history
#
# Everything else (CLI, loader, generator, tests, fixtures) is untouched.
set -eu

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    echo "usage: $0 <target-repo-dir>" >&2
    exit 2
fi
if [ ! -d "$TARGET/replay" ]; then
    echo "error: $TARGET does not look like the task repository" >&2
    exit 2
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

cp "$HERE/replay/book.py" "$TARGET/replay/book.py"
cp "$HERE/replay/engine.py" "$TARGET/replay/engine.py"
echo "oracle applied to $TARGET (replay/book.py, replay/engine.py)"
