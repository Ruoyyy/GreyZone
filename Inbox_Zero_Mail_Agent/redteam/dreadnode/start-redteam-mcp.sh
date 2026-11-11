#!/usr/bin/env bash
set -euo pipefail

capability_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$capability_dir/../shared/start-redteam-mcp.sh"
