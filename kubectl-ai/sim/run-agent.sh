#!/usr/bin/env bash
# Run the kubectl-ai agent container (attached to the threat-lab cluster network)
#
# Usage:
#   GEMINI_API_KEY=xxx sim/run-agent.sh                        # interactive mode
#   GEMINI_API_KEY=xxx sim/run-agent.sh --quiet "list nodes"   # single query
#   PROVIDER=openai OPENAI_ENDPOINT=http://host.docker.internal:11434/v1 \
#     OPENAI_API_KEY=ollama TOOL_SHIM=1 MODEL=qwen3:8b run-agent.sh    # any OpenAI-compatible endpoint
#
# Optional environment variables:
#   PROVIDER / MODEL   llm-provider and model name
#   SKIP_PERMS=1       append --skip-permissions
#   TOOL_SHIM=1        append --enable-tool-use-shim (needed for small local models)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/sim/.build"

ENV_FLAGS=()
for v in GEMINI_API_KEY OPENAI_API_KEY OPENAI_ENDPOINT OLLAMA_HOST ANTHROPIC_API_KEY GROK_API_KEY; do
  [[ -n "${!v:-}" ]] && ENV_FLAGS+=(-e "$v=${!v}")
done
# Don't route the agent container to the host by default; set HOST_LLM=1 only when the LLM endpoint is on the host
[[ "${HOST_LLM:-0}" == 1 ]] && ENV_FLAGS+=(--add-host host.docker.internal:host-gateway)

ARGS=(--kubeconfig /home/agent/kubeconfig --trace-path /home/agent/work/traces/trace.txt)
[[ -n "${PROVIDER:-}" ]] && ARGS+=(--llm-provider "$PROVIDER")
[[ -n "${MODEL:-}" ]]   && ARGS+=(--model "$MODEL")
[[ "${SKIP_PERMS:-0}" == 1 ]] && ARGS+=(--skip-permissions)
[[ "${TOOL_SHIM:-0}"  == 1 ]] && ARGS+=(--enable-tool-use-shim)

TTY_FLAGS=(-it); [[ -t 0 ]] || TTY_FLAGS=()
exec docker run --rm "${TTY_FLAGS[@]}" --network kind \
  "${ENV_FLAGS[@]}" \
  -v "${BUILD_DIR}/kubeconfig:/home/agent/kubeconfig:ro" \
  -v "${BUILD_DIR}/traces:/home/agent/work/traces" \
  kubectl-ai:lab "${ARGS[@]}" "$@"
