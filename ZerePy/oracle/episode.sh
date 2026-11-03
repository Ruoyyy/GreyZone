#!/usr/bin/env bash
# One episode, sibling-style. The victim is launched only after you plant, so it
# reads the complete planted state. Planting is driven by YOU (an external
# red-team agent as alice via the RedTeam MCP), so this opens a plant window:
#
#   bash oracle/episode.sh benchmark/scenarios/<id>      # reset + prepare, then stop
#   # ... plant as alice through http://127.0.0.1:8790/mcp (and alice's wallet) ...
#   python3 oracle/run_episode.py --start                # baseline + launch the victim
#   python3 oracle/watch.py                              # close on idle, write the verdict
set -euo pipefail
cd "$(dirname "$0")/.."
SCENARIO="${1:?usage: episode.sh <scenario-dir>}"
bash oracle/reset.sh
python3 oracle/run_episode.py --prepare --scenario "$SCENARIO"
echo
echo "[episode] plant window open. Attack as alice, then:"
echo "          python3 oracle/run_episode.py --start   # baseline + launch victim"
echo "          python3 oracle/watch.py                 # close on idle + judge"
