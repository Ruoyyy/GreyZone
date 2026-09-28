#!/usr/bin/env bash
# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT
#
# Collect the pure-Python wheels the Pyodide agent host installs on top of the Pyodide-built
# packages (build/fetch-pyodide.mjs):
#   - the blueprint packages from Anthropic's repository at the commit pinned in
#     requirements.txt (built with `pip wheel`, unchanged sources — ADR-1)
#   - the pure-Python pins from requirements.txt that Pyodide does not ship
#
# Output: host/wheels/*.whl + host/wheels/manifest.json → app/public/demo/wheels/
# Requires: python3 (>= 3.11) with pip, git, network. Cached in build/.cache/wheels.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
REQ="$REPO/requirements.txt"
OUT="$HERE/host/wheels"
CACHE="$HERE/build/.cache/wheels"
DEST="$HERE/app/public/demo/wheels"
PYTHON="${PYTHON:-python3}"

# Pure-Python distributions pinned in requirements.txt that Pyodide's distribution lacks.
PURE_PINS=(anthropic docstring-parser httpx-sse mcp pydantic-settings pyjwt python-dotenv python-multipart sse-starlette)
# Browser-only extras (not in requirements.txt): Pyodide ships no IANA tz database, but
# shopware_common/clock.py resolves the shop's zone through zoneinfo → the tzdata wheel.
EXTRA_PINS=("tzdata==2026.3")

mkdir -p "$OUT" "$CACHE" "$DEST"
rm -f "$OUT"/*.whl "$DEST"/*.whl

pin_of() { # pin_of <name> → name==version from requirements.txt (case-insensitive)
  local line
  line="$(grep -i -E "^$1==" "$REQ" | head -n1 || true)"
  if [[ -z "$line" ]]; then echo "no pin for $1 in requirements.txt" >&2; exit 1; fi
  echo "$line"
}

echo "collecting pure-Python pins"
specs=()
for name in "${PURE_PINS[@]}"; do specs+=("$(pin_of "$name")"); done
specs+=("${EXTRA_PINS[@]}")
"$PYTHON" -m pip download --quiet --no-deps --only-binary=:all: --dest "$CACHE" "${specs[@]}"

echo "building blueprint wheels (pinned commit from requirements.txt)"
blueprint=()
while IFS= read -r line; do
  [[ "$line" =~ ^[a-zA-Z0-9_-]+(\[[a-z,]+\])?\ @\ git\+https://github.com/anthropics/commerce-agents@ ]] || continue
  # strip extras: the [examples] extra pulls fastapi/uvicorn which Pyodide provides or does not need
  blueprint+=("${line/\[examples\]/}")
done < "$REQ"
if [[ ${#blueprint[@]} -eq 0 ]]; then echo "no blueprint git requirements found in $REQ" >&2; exit 1; fi
missing=()
for spec in "${blueprint[@]}"; do
  dist="${spec%% @ *}"; dist="${dist//-/_}"
  if ! ls "$CACHE"/"$dist"-*.whl >/dev/null 2>&1; then missing+=("$spec"); fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  "$PYTHON" -m pip wheel --quiet --no-deps --no-build-isolation --wheel-dir "$CACHE" "${missing[@]}" 2>/dev/null \
    || "$PYTHON" -m pip wheel --quiet --no-deps --wheel-dir "$CACHE" "${missing[@]}"
fi

# Select exactly one wheel per distribution (pins + blueprint) from the cache.
wanted=()
for spec in "${specs[@]}"; do
  name="${spec%%==*}"; version="${spec##*==}"
  wanted+=("$(ls "$CACHE"/$(echo "$name" | tr '-' '_' | tr '[:upper:]' '[:lower:]')-"$version"-*.whl "$CACHE"/$(echo "$name" | tr '-' '_')-"$version"-*.whl 2>/dev/null | head -n1)")
done
for spec in "${blueprint[@]}"; do
  dist="${spec%% @ *}"; dist="${dist//-/_}"
  wanted+=("$(ls "$CACHE"/"$dist"-*.whl | head -n1)")
done

names=()
for wheel in "${wanted[@]}"; do
  [[ -f "$wheel" ]] || { echo "wheel missing: $wheel" >&2; exit 1; }
  cp "$wheel" "$OUT/"
  cp "$wheel" "$DEST/"
  names+=("$(basename "$wheel")")
done

{
  echo '{'
  echo "  \"builtAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
  echo "  \"requirements\": \"requirements.txt\","
  echo '  "wheels": ['
  for i in "${!names[@]}"; do
    sep=','; [[ $i -eq $((${#names[@]} - 1)) ]] && sep=''
    echo "    \"${names[$i]}\"$sep"
  done
  echo '  ]'
  echo '}'
} > "$OUT/manifest.json"
cp "$OUT/manifest.json" "$DEST/manifest.json"

total=$(du -ch "$OUT"/*.whl | tail -n1 | cut -f1)
echo "wheels: ${#names[@]} files ($total) → $DEST"
