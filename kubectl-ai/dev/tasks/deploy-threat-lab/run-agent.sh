#!/usr/bin/env bash
# 运行 kubectl-ai agent 容器（已接入 threat-lab 集群网络）
#
# 用法:
#   GEMINI_API_KEY=xxx dev/tasks/deploy-threat-lab/run-agent.sh                        # 交互模式
#   GEMINI_API_KEY=xxx dev/tasks/deploy-threat-lab/run-agent.sh --quiet "list nodes"   # 单次查询
#   PROVIDER=openai OPENAI_ENDPOINT=http://host.docker.internal:11434/v1 \
#     OPENAI_API_KEY=ollama TOOL_SHIM=1 MODEL=qwen3:8b run-agent.sh    # 任意 OpenAI 兼容端点
#
# 可选环境变量:
#   PROVIDER / MODEL   llm-provider 与模型名
#   SKIP_PERMS=1       附加 --skip-permissions (无人值守实验)
#   TOOL_SHIM=1        附加 --enable-tool-use-shim (本地小模型需要)
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
BUILD_DIR="${REPO_ROOT}/.build/threat-lab"

ENV_FLAGS=()
for v in GEMINI_API_KEY OPENAI_API_KEY OPENAI_ENDPOINT OLLAMA_HOST ANTHROPIC_API_KEY GROK_API_KEY; do
  [[ -n "${!v:-}" ]] && ENV_FLAGS+=(-e "$v=${!v}")
done
# 默认不给 agent 容器通往宿主机的路由（本机不是靶机）；
# 仅当 LLM 端点在宿主机上时才显式开启 HOST_LLM=1
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
