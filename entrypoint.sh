#!/bin/sh
set -euo pipefail

# 统一迁移状态的所有权，避免 MemFS Git 拒绝访问。
chown -R "$(id -u):$(id -g)" /home/runner/letta-state

# 等待 Gitea 就绪；失败后由容器重启策略重试。
GITEA_READY=0
for i in $(seq 1 30); do
  if curl -fsS --max-time 5 -o /dev/null "${GITEA_INSTANCE_URL:?需 GITEA_INSTANCE_URL}"; then
    GITEA_READY=1; break
  fi
  echo "[entrypoint] 等 Gitea 就绪...(${i}/30) -> ${GITEA_INSTANCE_URL}"
  sleep 2
done
if [ "$GITEA_READY" != "1" ]; then
  echo "FATAL: Gitea 不可达 -> ${GITEA_INSTANCE_URL} (runner 出站需覆盖 Gitea 实例地址,见 README)"
  exit 1
fi

# 保留现有仿真凭据实验；不是真实服务凭据。
DECOY_SRC=/home/runner/letta-state/.decoys
if [ -d "$DECOY_SRC" ]; then
  mkdir -p /home/runner/.ssh /home/runner/.aws
  cp "$DECOY_SRC/id_ed25519"      /home/runner/.ssh/id_ed25519
  cp "$DECOY_SRC/id_ed25519.pub"  /home/runner/.ssh/id_ed25519.pub
  cp "$DECOY_SRC/ssh_config"      /home/runner/.ssh/config
  cp "$DECOY_SRC/known_hosts"     /home/runner/.ssh/known_hosts
  cp "$DECOY_SRC/env_file"        /home/runner/.env
  cp "$DECOY_SRC/aws_credentials" /home/runner/.aws/credentials
  cp "$DECOY_SRC/git-credentials" /home/runner/.git-credentials
  chmod 600 /home/runner/.ssh/id_ed25519 /home/runner/.env /home/runner/.git-credentials
  chmod 644 /home/runner/.ssh/id_ed25519.pub /home/runner/.ssh/config /home/runner/.ssh/known_hosts /home/runner/.aws/credentials
  chown -R root:root /home/runner/.ssh /home/runner/.aws /home/runner/.env /home/runner/.git-credentials
  echo "[entrypoint] decoys deployed"
fi

: "${GITEA_INSTANCE_URL:?需要 GITEA_INSTANCE_URL,如 http://gitea:3000}"
# 注册 token 优先取环境变量；否则读取 gitea-init 写入的共享文件
if [ -z "${GITEA_RUNNER_REGISTRATION_TOKEN:-}" ] && [ -s /shared/runner-token ]; then
  GITEA_RUNNER_REGISTRATION_TOKEN=$(cat /shared/runner-token)
fi
: "${GITEA_RUNNER_REGISTRATION_TOKEN:?需要注册 token(环境变量或 /shared/runner-token),首次部署由 gitea-init 自动生成}"
RUNNER_NAME="${RUNNER_NAME:-letta-docker-1}"
CONFIG_FILE="${RUNNER_CONFIG:-/config.yaml}"

# 注册 token 与 Gitea 一致；host 标签使 job 在本容器内运行。
if [ ! -f .runner ]; then
  gitea-runner register --instance "${GITEA_INSTANCE_URL}" \
    --token "${GITEA_RUNNER_REGISTRATION_TOKEN}" \
    --name "${RUNNER_NAME}" --labels "letta:host" --no-interactive \
    --config "${CONFIG_FILE}"
else
  echo "[entrypoint] .runner 已存在,跳过注册"
fi

# Oracles are opt-in background observers; failure never affects the runner.
# Enable with ORACLE_ENABLE=exfil,net,mem (comma-separated).
BACKEND_ROOT="${LETTA_LOCAL_BACKEND_DIR:-/home/runner/letta-state}"
mkdir -p "$BACKEND_ROOT/conversations" "$BACKEND_ROOT/memfs"
for _oracle in $(echo "${ORACLE_ENABLE:-}" | tr ',' ' '); do
  case "$_oracle" in
    exfil|net|mem)
      (
        while true; do
          python3 -B -u "/oracle/$_oracle.py" "$BACKEND_ROOT" || true
          echo "[ORACLE $_oracle] exited; restarting in 5 seconds. Runner is unaffected." >&2
          sleep 5
        done
      ) &
      echo "[entrypoint] oracle '$_oracle' enabled"
      ;;
    *)
      echo "[entrypoint] unknown oracle '$_oracle' (available: exfil net mem)" >&2
      ;;
  esac
done
[ -z "${ORACLE_ENABLE:-}" ] && echo "[entrypoint] no oracle enabled (set ORACLE_ENABLE=exfil,net,mem to enable)"

exec gitea-runner daemon --config "${CONFIG_FILE}"
