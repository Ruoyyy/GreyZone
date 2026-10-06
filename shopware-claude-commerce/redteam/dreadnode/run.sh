#!/usr/bin/env bash
set -euo pipefail

here="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$here/.env" ]]; then
  set -a
  source "$here/.env"
  set +a
fi

python="$(command -v python3)"
if [[ -x "$here/../../.venv/bin/python" ]]; then
  python="$here/../../.venv/bin/python"
fi
exec "$python" "$here/run.py" "$@"
