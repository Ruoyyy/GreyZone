#!/usr/bin/env bash
#
# mail-loop 一键准备脚本(设计 A:只准备依赖物,不启动服务)。
#
# 为 docker-compose.mail-loop.yml 生成它挂载的三个运行必需物:
#   1. .tmp/emulate-patched/           —— patched Gmail 模拟器(含 node_modules)
#   2. apps/web/.tmp/mail-loop-bundles/ —— 骨干与客户服务器的 esbuild bundle
#   3. apps/web/.tmp/customer-mail/.transport-token —— 传输面共享密钥
#
# 跑完后再手动:
#   docker compose --profile mail-loop -f docker-compose.mail-loop.yml up -d
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/apps/web"

say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }

# ── 1. patched emulate ────────────────────────────────────────────────
if [ -f "$ROOT/.tmp/emulate-patched/dist/index.js" ] &&
  [ -d "$ROOT/.tmp/emulate-patched/node_modules" ]; then
  ok "patched emulate 已存在"
else
  say "生成 patched emulate(需要 pnpm install)"
  cd "$APP"
  # 保证 emulate 依赖被安装(它在 apps/web 的依赖里,且被 patch)
  if ! pnpm install --filter inbox-zero-ai --offline >/dev/null 2>&1; then
    say "离线安装失败,改用在线安装"
    pnpm install --filter inbox-zero-ai
  fi
  mkdir -p "$ROOT/.tmp/emulate-patched"
  cp -r "$APP/node_modules/emulate/." "$ROOT/.tmp/emulate-patched/"
  # 独立 node_modules,让模拟器容器不依赖宿主 pnpm 布局
  (cd "$ROOT/.tmp/emulate-patched" && npm install --omit=dev --no-package-lock --silent)
  ok "patched emulate 就绪"
fi

# ── 2. esbuild bundles ─────────────────────────────────────────────────
mkdir -p "$APP/.tmp/mail-loop-bundles"
if [ -f "$APP/.tmp/mail-loop-bundles/run-mail-backbone.js" ] &&
  [ -f "$APP/.tmp/mail-loop-bundles/customer-mail-server.js" ]; then
  ok "bundle 已存在"
else
  say "打包骨干与客户服务器(esbuild)"
  cd "$APP"
  npx esbuild scripts/run-mail-backbone.ts --bundle --platform=node \
    --format=cjs --outfile=.tmp/mail-loop-bundles/run-mail-backbone.js
  npx esbuild scripts/customer-mail-server.ts --bundle --platform=node \
    --format=cjs --outfile=.tmp/mail-loop-bundles/customer-mail-server.js
  ok "bundle 就绪"
fi

# ── 3. transport token ────────────────────────────────────────────────
mkdir -p "$APP/.tmp/customer-mail"
if [ -s "$APP/.tmp/customer-mail/.transport-token" ]; then
  ok "transport token 已存在"
else
  say "生成 transport token"
  openssl rand -hex 32 > "$APP/.tmp/customer-mail/.transport-token"
  ok "transport token 就绪(len=$(wc -c < "$APP/.tmp/customer-mail/.transport-token"))"
fi

echo
echo "依赖物就绪。启动栈:"
echo "  cd $ROOT"
echo "  TRANSPORT_TOKEN=\$(cat apps/web/.tmp/customer-mail/.transport-token) \\"
echo "    docker compose --profile mail-loop -f docker-compose.mail-loop.yml up -d"
