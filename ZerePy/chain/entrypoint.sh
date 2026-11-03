#!/bin/sh
# Start anvil (no automine) -> replay the fixed history -> catch up to wall clock -> 1 s blocks -> write /tmp/seeded (the healthcheck)
set -e

python /chain/seed.py genesis > /tmp/genesis.json
anvil --accounts 0 --init /tmp/genesis.json --no-mining --host 0.0.0.0 --port 80 --chain-id 10143 &
ANVIL=$!
trap 'kill $ANVIL' TERM INT

if ! python /chain/seed.py seed http://localhost:80; then
    kill $ANVIL
    exit 1
fi
wait $ANVIL
