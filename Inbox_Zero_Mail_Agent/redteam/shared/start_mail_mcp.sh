#!/usr/bin/env bash
set -euo pipefail

shared_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$shared_dir/../../apps/web"
exec node --import tsx scripts/customer-mail-mcp.ts
