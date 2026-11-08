#!/usr/bin/env bash
# Apply the reference (oracle) implementation to a workspace copy.
#
#   ./apply_oracle.sh /path/to/workspace
#
# Replaces the whole coordinator package and adds the reference regression
# tests.  Idempotent.  Never touches the pristine task repository.
set -euo pipefail

WORKSPACE="${1:?usage: apply_oracle.sh WORKSPACE}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$WORKSPACE/coordinator" ]; then
    echo "error: $WORKSPACE does not look like the t3 workspace" >&2
    exit 2
fi

rm -rf "$WORKSPACE/coordinator"
cp -r "$HERE/files/coordinator" "$WORKSPACE/coordinator"
mkdir -p "$WORKSPACE/tests"
cp "$HERE/files/tests/test_recovery_extra.py" "$WORKSPACE/tests/test_recovery_extra.py"

echo "oracle applied to $WORKSPACE"
