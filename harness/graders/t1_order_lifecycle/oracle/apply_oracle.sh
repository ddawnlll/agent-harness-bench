#!/bin/bash
# Apply the reference fix to a candidate workspace: apply_oracle.sh <workspace>
set -euo pipefail
WS="${1:?usage: apply_oracle.sh <workspace>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$WS"
patch -p1 --forward --batch < "$HERE/fix.patch"
# Reference regression coverage travels with the oracle solution.
REF="$(cd "$HERE/../../../tasks/t1_order_lifecycle/reference" && pwd)"
cp "$REF/tests/test_regression_adversarial.py" tests/ 2>/dev/null || true
