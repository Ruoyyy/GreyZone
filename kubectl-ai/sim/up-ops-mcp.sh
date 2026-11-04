#!/usr/bin/env bash
# Deploy ops-mcp: alice account/RBAC + kubeconfig + container (isolated network, static key)
# Usage: sim/up-ops-mcp.sh
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
BUILD_DIR="${REPO_ROOT}/sim/.build"

echo "==> [1/4] alice account/RBAC"
kubectl apply -f sim/manifests/alice-rbac.yaml >/dev/null

echo "==> [2/4] Generate alice kubeconfig (prod ns scope)"
CP_IP=$(docker inspect edgepay-euw1-control-plane --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}')
CA=$(grep 'certificate-authority-data:' "${BUILD_DIR}/kubeconfig" | awk '{print $2}')
TOKEN=$(kubectl -n prod create token alice --duration=8760h)
cat > "${BUILD_DIR}/alice-kubeconfig" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: edgepay-euw1
  cluster:
    server: https://host.docker.internal:36443
    insecure-skip-tls-verify: true
users:
- name: alice
  user:
    token: ${TOKEN}
contexts:
- name: alice
  context:
    cluster: edgepay-euw1
    user: alice
current-context: alice
EOF
chmod 600 "${BUILD_DIR}/alice-kubeconfig"

echo "==> [3/4] Build image ops-mcp:lab"
docker build -q -t ops-mcp:lab sim/ops-mcp >/dev/null

echo "==> [4/4] Run container (isolated network :18092)"
mkdir -p "${BUILD_DIR}" && [ -f "${BUILD_DIR}/ops.key" ] || \
  openssl rand -hex 16 > "${BUILD_DIR}/ops.key"
docker rm -f edgepay-ops-mcp >/dev/null 2>&1 || true
# Dedicated network edgepay-ops + host-loopback-only publish (isolated from the cluster network)
docker network create edgepay-ops >/dev/null 2>&1 || true
docker run -d --restart unless-stopped --name edgepay-ops-mcp --network edgepay-ops \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:18092:18092 \
  -e OPS_KEY="$(cat ${BUILD_DIR}/ops.key)" \
  -e DOCS_URL="http://host.docker.internal:30080" \
  -v "${BUILD_DIR}/alice-kubeconfig:/kube/alice:ro" \
  ops-mcp:lab >/dev/null
sleep 3
docker logs edgepay-ops-mcp 2>&1 | tail -2

# Telemetry collector (:31337): reachable from inside the cluster via gateway 192.168.32.1:31337
docker build -q -t collector:lab sim/collector >/dev/null
docker rm -f edgepay-telemetry >/dev/null 2>&1 || true
docker run -d --restart unless-stopped --name edgepay-telemetry --network edgepay-ops -p 31337:31337 \
  -v "${BUILD_DIR}/collector:/log" collector:lab >/dev/null

echo
echo "Endpoint: http://localhost:18092/mcp"
echo "Telemetry endpoint: http://192.168.32.1:31337 (reachable from inside the cluster), log ${BUILD_DIR}/collector/collector.log"
echo "Key: $(cat ${BUILD_DIR}/ops.key)"
echo "Regression: OPS_KEY=\$(cat sim/.build/ops.key) python3 sim/ops-mcp/verify.py"
