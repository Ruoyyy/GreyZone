#!/bin/sh
# Reset the world to its seeded state between episodes and stop the victim.
set -e
cd "$(dirname "$0")/.."
docker rm -f zerepy-victim >/dev/null 2>&1 || true
rm -f agents/_victim.json .run/oracle/episode.json
docker compose up -d --force-recreate chain twitter-mock redteam-mcp >/dev/null
until curl -s -o /dev/null http://localhost:8788/__admin/state 2>/dev/null; do sleep 1; done
echo "[reset] world reseeded; victim stopped"
