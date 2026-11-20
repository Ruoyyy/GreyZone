#!/usr/bin/env bash
set -euo pipefail
capability_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${COMMONLY_PYTHON:?COMMONLY_PYTHON is required}" "$capability_dir/mcp_server.py"
