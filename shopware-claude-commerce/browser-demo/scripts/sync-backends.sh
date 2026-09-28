#!/usr/bin/env bash
# Copy the Python host code the in-browser agent host runs — read-only, no fork.
#
# The Pyodide worker mounts this tree at /repo and imports `storefront.api.main` and
# `merchant.api.main` exactly as uvicorn does on a laptop (ADR-1: blueprint packages
# unchanged; our thin backends unchanged). Re-run after editing storefront/api, merchant/api,
# shopware_common or vendor/demo_common; `npm run build` runs it automatically.
#
# Output: browser-demo/host/repo-tree/ (gitignored) + host/repo-tree.tar → app/public/demo/host/
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="$HERE/host/repo-tree"
TAR="$HERE/host/repo-tree.tar"
DEST_DIR="$HERE/app/public/demo/host"

rm -rf "$OUT"
mkdir -p "$OUT" "$DEST_DIR"

# rsync filters: tests, caches, runtime state and secrets never travel to the browser.
FILTERS=(
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests' --exclude '.pytest_cache'
  --exclude '.memory-*.json' --exclude 'ledger.db' --exclude '*.pem' --exclude '.env*'
)

copy() { # copy <relative-src-dir>
  mkdir -p "$OUT/$1"
  rsync -a "${FILTERS[@]}" "$REPO/$1/" "$OUT/$1/"
}

copy vendor/demo_common
copy vendor/skills
copy storefront/api
copy storefront/data
copy merchant/api
copy merchant/data
copy shopware_common
cp "$REPO/storefront/__init__.py" "$OUT/storefront/__init__.py"
cp "$REPO/merchant/__init__.py" "$OUT/merchant/__init__.py"
cp "$REPO/agent-profile.json" "$OUT/agent-profile.json"
cp "$REPO/vendor/README.md" "$OUT/vendor/README.md" 2>/dev/null || true
cp "$REPO/vendor/LICENSE-APACHE-2.0" "$OUT/vendor/LICENSE-APACHE-2.0" 2>/dev/null || true

SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
DIRTY="$(git -C "$REPO" status --porcelain -- storefront/api merchant/api shopware_common vendor/demo_common 2>/dev/null | wc -l | tr -d ' ')"
cat > "$OUT/SYNC_INFO.json" <<EOF
{
  "source": "$REPO",
  "commit": "$SHA",
  "uncommittedBackendChanges": $DIRTY,
  "syncedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "paths": ["vendor/demo_common", "vendor/skills", "storefront/api", "storefront/data", "merchant/api", "merchant/data", "shopware_common", "agent-profile.json"]
}
EOF

# --- web UIs (read-only copies for the Vite shell; see app/vite.config.ts) -----------------
WEB_OUT="$HERE/app/src/vendor"
rm -rf "$WEB_OUT"
mkdir -p "$WEB_OUT/storefront-web" "$WEB_OUT/merchant-web" "$WEB_OUT/web-shared"
rsync -a --exclude 'node_modules' --exclude '*.tsbuildinfo' "$REPO/vendor/web-shared/" "$WEB_OUT/web-shared/"
for app in storefront merchant; do
  rsync -a --exclude 'node_modules' --exclude '.next' --exclude '*.tsbuildinfo' --exclude 'AGENTS.md' --exclude 'CLAUDE.md' \
    --exclude 'next.config.ts' --exclude 'postcss.config.mjs' --exclude 'package.json' --exclude 'tsconfig.json' --exclude 'next-env.d.ts' \
    "$REPO/$app/web/" "$WEB_OUT/$app-web/"
done
# `@/` (each app's own root) → relative imports, so one tsconfig type-checks both copies.
node "$HERE/build/relativize-imports.mjs" "$WEB_OUT/storefront-web" "$WEB_OUT/merchant-web"
node "$HERE/build/scope-css.mjs" "$WEB_OUT/storefront-web/app/globals.css" "$WEB_OUT/storefront-web/app/globals.scoped.css" demo-theme-storefront
node "$HERE/build/scope-css.mjs" "$WEB_OUT/merchant-web/app/globals.css" "$WEB_OUT/merchant-web/app/globals.scoped.css" demo-theme-merchant
cp "$OUT/SYNC_INFO.json" "$WEB_OUT/SYNC_INFO.json"

# Deterministic tar (sorted, neutral owner) so the browser cache key only changes with content.
# GNU tar (Linux CI) uses --owner/--group; BSD tar (macOS) uses --uid/--gid.
TAR_OWNER_FLAGS=(--owner=0 --group=0 --numeric-owner)
if tar --help 2>&1 | grep -qE '\-\-uid'; then
  TAR_OWNER_FLAGS=(--uid 0 --gid 0 --numeric-owner)
fi
( cd "$OUT" && find . -type f | LC_ALL=C sort | tar -cf "$TAR" --no-recursion "${TAR_OWNER_FLAGS[@]}" -T - )
cp "$TAR" "$DEST_DIR/repo-tree.tar"
# The Pyodide-side bootstrap (httpx transport bridge + ASGI runner) is fetched by the worker.
cp "$HERE/host/bootstrap.py" "$DEST_DIR/bootstrap.py"
FILES="$(find "$OUT" -type f | wc -l | tr -d ' ')"
echo "sync-backends: $FILES files → $DEST_DIR/repo-tree.tar ($(du -h "$TAR" | cut -f1)) from $SHA (${DIRTY} uncommitted backend changes)"
