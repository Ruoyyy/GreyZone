#!/usr/bin/env bash
# Run separate framework batches sequentially; continue after a failed batch.
set -euo pipefail
trap 'echo "Interrupted; remaining batches will not start." >&2; exit 130' INT
trap 'echo "Terminated; remaining batches will not start." >&2; exit 143' TERM

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

for argument in "$@"; do
  case "$argument" in
    -h|--help)
      exec python3 experiment/scripts/run.py --help
      ;;
    --framework|--framework=*|--resume|--resume=*|--save-baseline|--save-baseline=*|--collect-only)
      echo "Use run.py directly for framework selection, recovery or baseline creation." >&2
      exit 2
      ;;
  esac
done

# Check all integrations before any framework starts spending model tokens.
python3 - <<'PY'
import sys
sys.path.insert(0, 'experiment/scripts')
from run import preflight
for framework in ('promptfoo', 'deepteam', 'pyrit', 'dreadnode'):
    preflight(framework)
    print(f'Preflight passed: {framework}', flush=True)
PY

completed=()
failed=()
for framework in promptfoo deepteam pyrit dreadnode; do
  echo "Starting batch: $framework"
  if python3 experiment/scripts/run.py \
    --framework "$framework" --baseline clean --repeats 3 \
    --oracle-idle-timeout 60 "$@"; then
    completed+=("$framework")
    echo "Completed batch: $framework"
  else
    code=$?
    if [[ "$code" == 130 || "$code" == 143 ]]; then
      exit "$code"
    fi
    failed+=("$framework")
    echo "Batch incomplete: $framework (exit $code). Continuing to the next framework." >&2
  fi
done

echo "Completed frameworks: ${completed[*]:-none}"
echo "Incomplete frameworks: ${failed[*]:-none}"
echo "See experiment/results/<framework>/<batch-id>/. Use run.py --resume to recover incomplete batches."
if (( ${#failed[@]} > 0 )); then
  exit 1
fi
