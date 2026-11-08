#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${1:?usage: apply.sh WORKSPACE}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$HERE/../../oracle/apply_oracle.sh" "$WORKSPACE"
PYTHONPATH="$HERE/..${PYTHONPATH:+:$PYTHONPATH}" python3 "$HERE/patch.py" "$WORKSPACE"
