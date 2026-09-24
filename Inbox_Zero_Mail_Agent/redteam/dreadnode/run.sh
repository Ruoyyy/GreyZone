#!/usr/bin/env bash
set -euo pipefail

capability_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$capability_dir/../../apps/web"
scenario_id="knowledge-leak"
dn_args=()
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--scenario" ]]; then
    if [[ $# -lt 2 ]]; then
      printf 'Usage: run.sh [--scenario NAME] [Dreadnode flags]\n' >&2
      exit 2
    fi
    scenario_id="$2"
    shift 2
  else
    dn_args+=("$1")
    shift
  fi
done

shell_openai_key="${OPENAI_API_KEY:-}"
if [[ -f "$capability_dir/.env" ]]; then
  set -a
  source "$capability_dir/.env"
  set +a
fi
if [[ -n "$shell_openai_key" ]]; then
  export OPENAI_API_KEY="$shell_openai_key"
fi

attacker_email="$(python3 "$capability_dir/../scenario.py" --scenario "$scenario_id" --field attackerEmail)"
base_subject="$(python3 "$capability_dir/../scenario.py" --scenario "$scenario_id" --field subject)"
poll_seconds="$(python3 "$capability_dir/../scenario.py" --scenario "$scenario_id" --field pollIntervalSeconds)"
scenario_prompt="$(python3 "$capability_dir/../scenario.py" --scenario "$scenario_id")"
campaign_subject="$base_subject [dn-$(date -u +%Y%m%dT%H%M%S)-$$]"

token_file="$app_dir/.tmp/customer-mail/.transport-token"
if [[ ! -f "$token_file" ]]; then
  printf 'Missing customer-mail transport token: %s\nStart the mail stack first.\n' "$token_file" >&2
  exit 1
fi
export TRANSPORT_TOKEN="$(<"$token_file")"
if [[ -z "$TRANSPORT_TOKEN" ]]; then
  printf 'Customer-mail transport token is empty.\n' >&2
  exit 1
fi
export CUSTOMER_EMAIL="$attacker_email"
export CUSTOMER_OUTBOX="$app_dir/.tmp/customer-mail"
export MAIL_POLL_INTERVAL_SECONDS="$poll_seconds"
export TRANSPORT_URL="${TRANSPORT_URL:-http://127.0.0.1:4005}"

model="${DREADNODE_ATTACKER_MODEL:-openai/gpt-4o}"
if [[ "$model" == openai/* && -z "${OPENAI_API_KEY:-}" ]]; then
  printf 'OPENAI_API_KEY is required for model %s; set it in redteam/dreadnode/.env or the shell.\n' "$model" >&2
  exit 1
fi
max_steps="${DREADNODE_MAX_STEPS:-700}"
if [[ ! "$max_steps" =~ ^[1-9][0-9]*$ ]]; then
  printf 'DREADNODE_MAX_STEPS must be a positive integer.\n' >&2
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

attack_prompt="$scenario_prompt

Campaign subject: $campaign_subject

$(<"$capability_dir/instructions.md")"
exec python3 "$capability_dir/../run_locked.py" -- "$dn_bin" \
  --capabilities-dir "$capability_dir/.." \
  --capability inbox-zero-mail-redteam \
  --agent mail-attacker \
  --model "$model" \
  --prompt "$attack_prompt" \
  --auto --max-steps "$max_steps" "${dn_args[@]}"
