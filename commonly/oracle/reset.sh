#!/usr/bin/env bash
# Episode reset for the Commonly oracle.
#
#   bash oracle/reset.sh          # light: drop episode state (seed-lab data stays)
#   bash oracle/reset.sh --hard   # heavy: wipe volumes, full reseed (stack + lab)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--hard" ]; then
    docker compose -f docker-compose.stack.yml down -v
    docker compose -f docker-compose.stack.yml up -d --build
    sleep 10
    docker compose -f docker-compose.stack.yml run --rm --entrypoint sh seed \
        -c 'eval "$CORE_SECRETS" && exec node dist/scripts/seed-lab.js'
    exit 0
fi

uv run --with httpx python - <<'EOF'
from pathlib import Path
from oracle import common as C

state = C.RUN_DIR / "episode.json"
if state.exists():
    state.unlink()
    print("[reset] light reset done — episode state dropped, seed-lab data intact")
else:
    print("[reset] no episode state — nothing to do")
EOF
