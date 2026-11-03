#!/usr/bin/env bash
# One command per episode, sibling-style:
#
#   bash oracle/episode.sh benchmark/scenarios/private-pod-leak
#   bash oracle/episode.sh benchmark/scenarios/forged-kick --manual   # stop after prepare
#
# Flow: prepare (--attack-fixed when the scenario carries payloads) →
# watch in the background → start (scenario attackerTurn) → wait for the
# result file → print the verdict.
set -euo pipefail
cd "$(dirname "$0")/.."

SCENARIO="${1:?usage: episode.sh <scenario-dir> [--manual]}"
MANUAL="${2:-}"

RUN() { PYTHONPATH=. uv run --with httpx "$@"; }

echo "[episode] prepare $SCENARIO"
RUN python oracle/run_episode.py --prepare --scenario "$SCENARIO" --attack-fixed

if [ "$MANUAL" = "--manual" ]; then
  echo "[episode] prep window open — red-team as alice (user MCP / REST), then:"
  echo "          RUN python oracle/run_episode.py --start"
  echo "          RUN python oracle/watch.py"
  exit 0
fi

RUN python oracle/watch.py &
WATCH_PID=$!
trap 'kill "$WATCH_PID" 2>/dev/null || true' EXIT

sleep 2
echo "[episode] start"
RUN python oracle/run_episode.py --start

N=$(python3 -c "import json;print(json.load(open('.run/oracle/episode.json'))['n'])")
RESULT=".run/oracle/result-${N}.json"
echo "[episode] waiting for $RESULT"
for _ in $(seq 1 240); do
  [ -f "$RESULT" ] && break
  sleep 5
done
if [ ! -f "$RESULT" ]; then
  echo "[episode] ERROR: result file never appeared (20 min)" >&2
  exit 1
fi
wait "$WATCH_PID" || true
python3 -c "import json;r=json.load(open('$RESULT'));print('verdict:',r['verdict'],'| closeReason:',r['closeReason'])"
