#!/usr/bin/env bash
# Apply the browser-demo patch set. Idempotent: a patch that is already applied is skipped
# (detected with `patch --dry-run -R`), a patch that applies is applied, anything else fails.
#
#   apply-patches.sh playground <playground-dir>   patches/playground-*.patch   (git tree, -p1)
#   apply-patches.sh vendor     <shopware-dir>     patches/vendor-*.patch       (composer vendor/, -p1)
#
# See patches/README.md for what each patch does and which ones belong upstream.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
KIND="${1:?playground|vendor}"
TARGET="${2:?target directory}"

case "$KIND" in
  playground) PATTERN="playground-*.patch" ;;
  vendor)     PATTERN="vendor-*.patch" ;;
  *) echo "unknown patch kind: $KIND" >&2; exit 2 ;;
esac

if [[ ! -d "$TARGET" ]]; then
  echo "target directory missing: $TARGET" >&2
  exit 1
fi

shopt -s nullglob
applied=0
skipped=0
for file in "$HERE"/$PATTERN; do
  name="$(basename "$file")"
  if patch -p1 -d "$TARGET" --dry-run -R -s -f < "$file" >/dev/null 2>&1; then
    echo "patch $name: already applied"
    skipped=$((skipped + 1))
    continue
  fi
  if ! patch -p1 -d "$TARGET" --dry-run -s -f < "$file" >/dev/null 2>&1; then
    echo "patch $name: does not apply cleanly to $TARGET" >&2
    patch -p1 -d "$TARGET" --dry-run -f < "$file" || true
    exit 1
  fi
  patch -p1 -d "$TARGET" -s -f < "$file"
  echo "patch $name: applied"
  applied=$((applied + 1))
done
echo "patches ($KIND): $applied applied, $skipped already present"
