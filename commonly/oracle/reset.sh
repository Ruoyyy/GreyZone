#!/usr/bin/env bash
# A light reset archives Oracle state; --hard wipes and reseeds Commonly only.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${COMMONLY_PYTHON:-redteam/.venv/bin/python}" redteam/reset.py "$@"
