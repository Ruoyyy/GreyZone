#!/usr/bin/env bash
#
# One-shot preparation script for the mail loop (design A: prepare artifacts
# only, start no services).
#
# Generates the three runtime prerequisites mounted by docker-compose.mail-loop.yml:
#   1. .tmp/emulate-patched/            -- patched Gmail emulator (with node_modules)
#   2. apps/web/.tmp/mail-loop-bundles/ -- esbuild bundles for the backbone and customer server
#   3. apps/web/.tmp/customer-mail/.transport-token -- shared secret for the transport plane
#
# Afterwards start manually:
#   docker compose --profile mail-loop -f docker-compose.mail-loop.yml up -d
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/apps/web"

say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }

# ── 1. patched emulate ────────────────────────────────────────────────
if [ -f "$ROOT/.tmp/emulate-patched/dist/index.js" ] &&
  [ -d "$ROOT/.tmp/emulate-patched/node_modules" ]; then
  ok "patched emulate already exists"
else
  say "Building patched emulate (requires pnpm install)"
  cd "$APP"
  # Make sure the emulate dependency is installed (it is among apps/web's deps and gets patched)
  if ! pnpm install --filter inbox-zero-ai --frozen-lockfile --offline >/dev/null 2>&1; then
    say "Offline install failed, falling back to online install"
    pnpm install --filter inbox-zero-ai --frozen-lockfile
  fi
  mkdir -p "$ROOT/.tmp/emulate-patched"
  cp -r "$APP/node_modules/emulate/." "$ROOT/.tmp/emulate-patched/"
  # Standalone node_modules so the emulator container does not depend on the host pnpm layout
  node -e 'const fs = require("fs"); const [actual, locked] = process.argv.slice(1).map(p => JSON.parse(fs.readFileSync(p))); if (actual.name !== locked.name || actual.version !== locked.version) throw new Error("Emulator version does not match its runtime lock");' \
    "$ROOT/.tmp/emulate-patched/package.json" "$ROOT/scripts/emulate-package.json"
  cp "$ROOT/scripts/emulate-package.json" "$ROOT/.tmp/emulate-patched/package.json"
  cp "$ROOT/scripts/emulate-package-lock.json" "$ROOT/.tmp/emulate-patched/package-lock.json"
  (cd "$ROOT/.tmp/emulate-patched" && npm ci --omit=dev --ignore-scripts --silent)
  ok "patched emulate ready"
fi

# ── 2. esbuild bundles ─────────────────────────────────────────────────
mkdir -p "$APP/.tmp/mail-loop-bundles"
if [ -f "$APP/.tmp/mail-loop-bundles/run-mail-backbone.js" ] &&
  [ -f "$APP/.tmp/mail-loop-bundles/customer-mail-server.js" ]; then
  ok "bundles already exist"
else
  say "Bundling backbone and customer server (esbuild)"
  cd "$APP"
  pnpm exec esbuild scripts/run-mail-backbone.ts --bundle --platform=node \
    --format=cjs --outfile=.tmp/mail-loop-bundles/run-mail-backbone.js
  pnpm exec esbuild scripts/customer-mail-server.ts --bundle --platform=node \
    --format=cjs --outfile=.tmp/mail-loop-bundles/customer-mail-server.js
  ok "bundles ready"
fi

# ── 3. transport token ────────────────────────────────────────────────
mkdir -p "$APP/.tmp/customer-mail"
if [ -s "$APP/.tmp/customer-mail/.transport-token" ]; then
  ok "transport token already exists"
else
  say "Generating transport token"
  openssl rand -hex 32 > "$APP/.tmp/customer-mail/.transport-token"
  ok "transport token ready (len=$(wc -c < "$APP/.tmp/customer-mail/.transport-token"))"
fi

echo
echo "Artifacts ready. Start the stack:"
echo "  cd $ROOT"
echo "  TRANSPORT_TOKEN=\$(cat apps/web/.tmp/customer-mail/.transport-token) \\"
echo "    docker compose --profile mail-loop -f docker-compose.mail-loop.yml up -d"
