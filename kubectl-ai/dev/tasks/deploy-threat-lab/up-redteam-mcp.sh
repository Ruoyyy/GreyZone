#!/usr/bin/env bash
# 部署 redteam-mcp：攻击者SA/RBAC + 攻击者kubeconfig + 容器(kind网络, 静态钥匙)
# 用法: dev/tasks/deploy-threat-lab/up-redteam-mcp.sh
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
BUILD_DIR="${REPO_ROOT}/.build/threat-lab"

echo "==> [1/4] 攻击者 SA/RBAC"
kubectl apply -f k8s/threat-lab/attacker-rbac.yaml >/dev/null

echo "==> [2/4] 生成攻击者 kubeconfig (ns级最小权限)"
CP_IP=$(docker inspect edgepay-euw1-control-plane --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}')
CA=$(grep 'certificate-authority-data:' "${BUILD_DIR}/kubeconfig" | awk '{print $2}')
TOKEN=$(kubectl -n prod create token ruoyu --duration=8760h)
cat > "${BUILD_DIR}/attacker-kubeconfig" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: edgepay-euw1
  cluster:
    server: https://host.docker.internal:36443
    insecure-skip-tls-verify: true
users:
- name: ruoyu
  user:
    token: ${TOKEN}
contexts:
- name: ruoyu
  context:
    cluster: edgepay-euw1
    user: ruoyu
current-context: ruoyu
EOF
chmod 600 "${BUILD_DIR}/attacker-kubeconfig"

echo "==> [3/4] 构建镜像 redteam-mcp:lab"
docker build -q -t redteam-mcp:lab k8s/threat-lab/redteam-mcp >/dev/null

echo "==> [4/4] 运行容器 (kind网络 :18092)"
mkdir -p "${BUILD_DIR}" && [ -f "${BUILD_DIR}/redteam.key" ] || \
  openssl rand -hex 16 > "${BUILD_DIR}/redteam.key"
docker rm -f edgepay-redteam-mcp >/dev/null 2>&1 || true
# 独占网络 + 仅宿主回环发布: 受害者agent(在kind网络)既不能跨桥路由、也不能借宿主loopback触及本MCP
docker network create redteam-net >/dev/null 2>&1 || true
docker run -d --name edgepay-redteam-mcp --network redteam-net \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:18092:18092 \
  -e REDTEAM_KEY="$(cat ${BUILD_DIR}/redteam.key)" \
  -e DOCS_URL="http://host.docker.internal:30080" \
  -v "${BUILD_DIR}/attacker-kubeconfig:/kube/attacker:ro" \
  redteam-mcp:lab >/dev/null
sleep 3
docker logs edgepay-redteam-mcp 2>&1 | tail -2

echo
echo "端点: http://localhost:18092/mcp"
echo "钥匙: $(cat ${BUILD_DIR}/redteam.key)"
echo "验证: REDTEAM_KEY=\$(cat .build/threat-lab/redteam.key) python3 k8s/threat-lab/redteam-mcp/verify.py"
