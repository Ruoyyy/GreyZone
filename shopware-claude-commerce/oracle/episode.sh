#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# One command, one episode with the two-phase flow: ensure the watcher runs,
# pre-position the scenario's fixed payload (--prepare), flip the switch
# (--start), wait for the idle close, print the verdict.
#
#   bash oracle/episode.sh benchmark/scenarios/buyer-misdirection
#   TASK_CATEGORY=clothing bash oracle/episode.sh benchmark/scenarios/buyer-misdirection
#   bash oracle/episode.sh benchmark/scenarios/buyer-misdirection --manual   # prepare only:
#                                                                   red-team via MCP, then --start yourself
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SCENARIO="${1:?usage: episode.sh <scenario-dir> [--manual]}"
MANUAL="${2:-}"
PY=.venv/bin/python
RUN_LOG=/tmp/oracle-episode-watch.log

if ! pgrep -f "oracle/watch.py" >/dev/null; then
  echo "[episode] starting oracle/watch.py (log: $RUN_LOG)"
  nohup "$PY" oracle/watch.py >"$RUN_LOG" 2>&1 &
  sleep 2
fi

BEFORE="$("$PY" -c 'from oracle.common import next_episode; print(next_episode())')"

"$PY" oracle/run_episode.py --scenario "$SCENARIO" --prepare --attack-fixed

if [[ "$MANUAL" == "--manual" ]]; then
  echo "[episode] prep window open — attack through the seller MCP, then:"
  echo "  $PY oracle/run_episode.py --scenario $SCENARIO --start"
  exit 0
fi

"$PY" oracle/run_episode.py --scenario "$SCENARIO" --start

echo "[episode] waiting for the idle close (EPISODE_IDLE_TIMEOUT=${EPISODE_IDLE_TIMEOUT:-300}s)..."
for _ in $(seq 1 $(((${EPISODE_IDLE_TIMEOUT:-300} + 90) / 5))); do
  AFTER="$("$PY" -c 'from oracle.common import next_episode; print(next_episode())')"
  if [[ "$AFTER" -gt "$BEFORE" ]]; then
    echo "[episode] verdict:"
    "$PY" - ".run/oracle/result-$BEFORE.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
print(f"  outcome : {r['outcome']}")
print(f"  purchase: {r['buyerPurchase']['grade']} (damage {r['buyerPurchase']['damageEur']} EUR)")
print(f"  price   : {len(r['floorBreaches']['breaches'])} breach(es)")
print(f"  refunds : {len(r['refunds']['refunds'])} (damage {r['refunds']['damageEur']} EUR)")
print(f"  exfil   : to_attacker={r['exfil'].get('toAttacker', 0)} score={r['exfil'].get('score', 0)}")
print(f"  full    : {sys.argv[1]}")
EOF
    exit 0
  fi
  sleep 5
done
echo "[episode] timed out waiting for the episode to close; check $RUN_LOG" >&2
exit 1
