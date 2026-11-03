#!/usr/bin/env bash
# One episode, sibling-style. The attacker is driven by YOU (an external
# red-team agent as ruoyu via the RedTeam MCP), so this opens a manual window:
#
#   bash oracle/episode.sh benchmark/scenarios/<id>
#   # ... attack as ruoyu through http://127.0.0.1:8790/mcp ...
#   python3 oracle/watch.py        # closes on idle, writes the verdict
set -euo pipefail
cd "$(dirname "$0")/.."
SCENARIO="${1:?usage: episode.sh <scenario-dir>}"
bash oracle/reset.sh
python3 oracle/run_episode.py --prepare --scenario "$SCENARIO"
python3 oracle/run_episode.py --start
echo "[episode] prep window open — attack as ruoyu, then: python3 oracle/watch.py"
