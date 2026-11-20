#!/usr/bin/env bash
# Resume the current incomplete batches, preserving completed repetitions.
set -uo pipefail
trap 'echo "Interrupted; remaining batches will not start." >&2; exit 130' INT
trap 'echo "Terminated; remaining batches will not start." >&2; exit 143' TERM
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
failed=0
while read -r framework batch; do
  echo "Resuming $framework: $batch"
  if python3 experiment/scripts/run.py --framework "$framework" --resume "$batch" --accept-repair </dev/null; then
    echo "Completed: $framework"
  else
    code=$?
    if [[ "$code" == 130 || "$code" == 143 ]]; then exit "$code"; fi
    echo "Incomplete: $framework (exit $code); continuing." >&2
    failed=1
  fi
done <<'BATCHES'
promptfoo 20261113T123752Z-e3949c28
pyrit 20261113T141154Z-86acae90
dreadnode 20261113T142538Z-738156a7
BATCHES
exit "$failed"
