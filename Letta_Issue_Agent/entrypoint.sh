#!/bin/sh
set -euo pipefail

# Normalize ownership of migrated state to prevent MemFS Git from refusing access.
chown -R "$(id -u):$(id -g)" /home/runner/letta-state

# Wait for Gitea to be ready; the container restart policy retries after a failure.
GITEA_READY=0
for i in $(seq 1 30); do
  if curl -fsS --max-time 5 -o /dev/null "${GITEA_INSTANCE_URL:?GITEA_INSTANCE_URL is required}"; then
    GITEA_READY=1; break
  fi
  echo "[entrypoint] Waiting for Gitea to be ready...(${i}/30) -> ${GITEA_INSTANCE_URL}"
  sleep 2
done
if [ "$GITEA_READY" != "1" ]; then
  echo "FATAL: Gitea is unreachable -> ${GITEA_INSTANCE_URL} (runner egress must allow access to the Gitea instance address; see README)"
  exit 1
fi

: "${GITEA_INSTANCE_URL:?GITEA_INSTANCE_URL is required, e.g. http://gitea:3000}"
# Prefer the registration token from the environment; otherwise read the shared file written by gitea-init.
if [ -z "${GITEA_RUNNER_REGISTRATION_TOKEN:-}" ] && [ -s /shared/runner-token ]; then
  GITEA_RUNNER_REGISTRATION_TOKEN=$(cat /shared/runner-token)
fi
: "${GITEA_RUNNER_REGISTRATION_TOKEN:?A registration token is required (set it in the environment or /shared/runner-token); gitea-init generates it on first deployment}"
RUNNER_NAME="${RUNNER_NAME:-letta-docker-1}"
CONFIG_FILE="${RUNNER_CONFIG:-/config.yaml}"

# The registration token matches Gitea's; the host label runs jobs inside this container.
if [ ! -f .runner ]; then
  gitea-runner register --instance "${GITEA_INSTANCE_URL}" \
    --token "${GITEA_RUNNER_REGISTRATION_TOKEN}" \
    --name "${RUNNER_NAME}" --labels "letta:host" --no-interactive \
    --config "${CONFIG_FILE}"
else
  echo "[entrypoint] .runner already exists, skipping registration"
fi

# Activity logs: one file per issue (full prompts/activity, truncated tool arguments), always recorded
# regardless of ORACLE_ENABLE; Oracle alerts are also written to the current issue's log.
BACKEND_ROOT="${LETTA_LOCAL_BACKEND_DIR:-/home/runner/letta-state}"
mkdir -p "$BACKEND_ROOT/conversations" "$BACKEND_ROOT/memfs"
(
  while true; do
    python3 -B -u /oracle/common.py "$BACKEND_ROOT" || true
    echo "[ISSUELOG] exited; restarting in 5 seconds. Runner is unaffected." >&2
    sleep 5
  done
) &

# Oracles are opt-in background observers; failure never affects the runner.
# Enable with ORACLE_ENABLE=exfil,net,mem (comma-separated).
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
