#!/usr/bin/env bash
# Run the kubectl-ai agent container (attached to the threat-lab cluster network)
#
# Usage: export OPENAI_API_KEY, OPENAI_MODEL, optionally OPENAI_BASE_URL.
#   sim/run-agent.sh --quiet "list nodes"
# Optional: HOST_LLM=1 for a host endpoint, SKIP_PERMS=1, TOOL_SHIM=1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/sim/.build"

export OPENAI_API_KEY="${OPENAI_API_KEY:?Set OPENAI_API_KEY}"
export OPENAI_MODEL="${OPENAI_MODEL:?Set OPENAI_MODEL}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
ENV_FLAGS=(-e OPENAI_API_KEY -e OPENAI_MODEL -e OPENAI_BASE_URL)
# Don't route the agent container to the host by default; set HOST_LLM=1 only when the LLM endpoint is on the host
[[ "${HOST_LLM:-0}" == 1 ]] && ENV_FLAGS+=(--add-host host.docker.internal:host-gateway)

ARGS=(--kubeconfig /home/agent/kubeconfig --trace-path /home/agent/work/traces/trace.txt)
ARGS+=(--llm-provider openai --model "$OPENAI_MODEL")
[[ "${SKIP_PERMS:-0}" == 1 ]] && ARGS+=(--skip-permissions)
[[ "${TOOL_SHIM:-0}"  == 1 ]] && ARGS+=(--enable-tool-use-shim)

TTY_FLAGS=(-it); [[ -t 0 ]] || TTY_FLAGS=()
exec docker run --rm "${TTY_FLAGS[@]}" --network kind \
  "${ENV_FLAGS[@]}" \
  -v "${BUILD_DIR}/kubeconfig:/home/agent/kubeconfig:ro" \
  -v "${BUILD_DIR}/traces:/home/agent/work/traces" \
  kubectl-ai:lab "${ARGS[@]}" "$@"
