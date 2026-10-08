#!/usr/bin/env bash
set -euo pipefail
capability_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${CLAWITH_PYTHON:?CLAWITH_PYTHON is required}" "$capability_dir/attacker_mcp.py"
