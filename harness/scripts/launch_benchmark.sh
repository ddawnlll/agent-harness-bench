#!/bin/bash
# Launch the measured benchmark detached from the interactive session.
set -euo pipefail
cd "$(dirname "$0")/.."
RUN_ID="${1:?usage: launch_benchmark.sh <run_id>}"
LOG="logs/run-${RUN_ID}.out"
mkdir -p logs
if systemctl --user is-active --quiet agentbench-${RUN_ID}.service 2>/dev/null; then
  echo "already running"; exit 0
fi
systemd-run --user --unit="agentbench-${RUN_ID}" --collect \
  --description="Agent benchmark ${RUN_ID}" \
  -p WorkingDirectory="$PWD" \
  -- /bin/bash -lc "./controller/benchctl run -config config/experiment.json -run-id ${RUN_ID} >> ${LOG} 2>&1"
echo "launched unit agentbench-${RUN_ID}.service; log ${LOG}"
