#!/usr/bin/env bash
set -euo pipefail

capability_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_file="$capability_dir/.env"
objective_file="$capability_dir/objective.md"

if [[ ! -f "$config_file" ]]; then
  printf 'Missing configuration: %s\nCopy .env.example to .env and fill in OPENAI_API_KEY.\n' "$config_file" >&2
  exit 1
fi

set -a
source "$config_file"
set +a

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  printf 'Set OPENAI_API_KEY in %s before starting.\n' "$config_file" >&2
  exit 1
fi

if [[ ! -f "$objective_file" ]]; then
  printf 'Missing objective file: %s\n' "$objective_file" >&2
  exit 1
fi
attack_objective="$(cat -- "$objective_file")"
if [[ -z "${attack_objective//[[:space:]]/}" ]]; then
  printf 'Write your attack objective in %s before starting.\n' "$objective_file" >&2
  exit 1
fi

dn_bin="$(command -v dn || true)"
if [[ -z "$dn_bin" && -x "$HOME/.local/bin/dn" ]]; then
  dn_bin="$HOME/.local/bin/dn"
fi
if [[ -z "$dn_bin" ]]; then
  printf 'Dreadnode CLI not found. Install it before running this script.\n' >&2
  exit 1
fi

max_steps="${DREADNODE_MAX_STEPS:-300}"
if [[ ! "$max_steps" =~ ^[1-9][0-9]*$ ]]; then
  printf 'DREADNODE_MAX_STEPS must be a positive integer.\n' >&2
  exit 1
fi

exec "$dn_bin" \
  --capability letta-redteam \
  --agent letta-attacker \
  --model "${DREADNODE_ATTACKER_MODEL:-openai/gpt-4o}" \
  --prompt "$attack_objective" \
  --auto --max-steps "$max_steps" "$@"
