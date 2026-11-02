#!/bin/sh
# 启动 anvil（不自动出块）-> 回放固定历史 -> 追平当前时间 -> 每秒出块 -> 写 /tmp/seeded（健康检查依据）
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
