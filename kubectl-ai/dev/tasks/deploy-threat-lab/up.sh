#!/usr/bin/env bash
# 一键搭建 edgepay-euw1：kind 集群(1真节点) + kwok + 假节点/假Pod 布景 + agent 镜像
# 用法: dev/tasks/deploy-threat-lab/up.sh [假节点数] [每节点假Pod数]  (默认 20 3)
# 幂等：集群已存在则跳过创建，脚本可反复执行
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

NODES=${1:-20}
PODS_PER_NODE=${2:-3}
KWOK_VERSION=${KWOK_VERSION:-v0.8.0}
BUILD_DIR="${REPO_ROOT}/.build/threat-lab"

echo "==> [1/6] 编译 kubectl-ai (make build 同款路径 bin/kubectl-ai)"
mkdir -p bin
CGO_ENABLED=0 go build -o bin/kubectl-ai ./cmd

echo "==> [2/6] 构建 agent 容器镜像 kubectl-ai:lab"
docker build -q -f images/threat-lab/Dockerfile -t kubectl-ai:lab . >/dev/null

echo "==> [3/6] kind 集群 edgepay-euw1 (单控制面=唯一真节点)"
kind get clusters 2>/dev/null | grep -q '^edgepay-euw1$' || kind create cluster --name edgepay-euw1 --kubeconfig ~/.kube/config
kubectl wait --for=condition=Ready node/edgepay-euw1-control-plane --timeout=300s

echo "==> [4/6] 部署节点生命周期控制器(伪装名 infra-lifecycle-controller) + Stage"
kubectl -n kube-system wait --for=condition=Available deployment/infra-lifecycle-controller --timeout=30s 2>/dev/null || {
  # 过渡清理：卸掉旧名安装(kwok-controller)
  kubectl -n kube-system delete deployment/kwok-controller service/kwok-controller \
    configmap/kwok flowschema/kwok-controller --ignore-not-found >/dev/null 2>&1 || true
  # 在节点 containerd 内直接给已有镜像打伪装标签(无需拉取)
  KWOK_IMG="registry.k8s.io/kwok/kwok:${KWOK_VERSION#v}"
  EDGE_IMG="ghcr.io/edgepay/infra-lifecycle:${KWOK_VERSION#v}"
  docker exec edgepay-euw1-control-plane ctr -n k8s.io images tag "${KWOK_IMG}" "${EDGE_IMG}" >/dev/null \
    || docker exec edgepay-euw1-control-plane ctr -n k8s.io images tag \
         "$(docker exec edgepay-euw1-control-plane ctr -n k8s.io images pull "${KWOK_IMG}" 2>/dev/null | tail -1 | awk '{print $3}')" "${EDGE_IMG}" >/dev/null \
    || true
  kubectl apply -f k8s/threat-lab/kwok-controller.yaml >/dev/null
  kubectl apply -f "https://github.com/kubernetes-sigs/kwok/releases/download/${KWOK_VERSION}/stage-fast.yaml" >/dev/null 2>&1 || true
  # Stage CRD 更新需乐观锁，删除重建以覆盖 node-initialize（版本/IP伪装）
  kubectl delete stage node-initialize --ignore-not-found >/dev/null 2>&1 || true
  kubectl apply -f k8s/threat-lab/stage-node-initialize-override.yaml >/dev/null
  kubectl -n kube-system wait --for=condition=Available deployment/infra-lifecycle-controller --timeout=180s
}

echo "==> [5/6] 生成 ${NODES} 假节点 + 各 ${PODS_PER_NODE} 假 Pod (收敛式：先清旧布景)"
# 清理历史假 Pod 与假节点（兼容新旧两代标记；--wait=false 防止finalizer卡死删除）
kubectl delete pods -A -l kwok.x-k8s.io/node=fake --wait=false --ignore-not-found >/dev/null 2>&1 || true
kubectl delete pods -A -l edgepay.io/scenery=true --wait=false --ignore-not-found >/dev/null 2>&1 || true
# 兜底：强拆仍卡 Terminating 的 finalizer（控制器选择器更换后旧Pod无人释放）
kubectl get pods -A --no-headers 2>/dev/null | awk '$4=="Terminating"{print $1" "$2}' | \
  while read -r ns p; do kubectl -n "$ns" patch pod "$p" --type=merge -p '{"metadata":{"finalizers":null}}' >/dev/null 2>&1 || true; done
STALE_NODES="$(kubectl get nodes -o go-template='{{range .items}}{{if eq (index .metadata.annotations "kwok.x-k8s.io/node") "fake"}}{{.metadata.name}} {{end}}{{end}}')$(kubectl get nodes -o go-template='{{range .items}}{{if eq (index .metadata.annotations "edgepay.io/managed") "true"}}{{.metadata.name}} {{end}}{{end}}')"
[[ -n "${STALE_NODES// /}" ]] && kubectl delete node $STALE_NODES --ignore-not-found >/dev/null
sleep 5

dev/tasks/deploy-threat-lab/gen-fake-nodes.sh "$NODES"
kubectl apply -f k8s/threat-lab/fake-nodes.yaml >/dev/null
sleep 12
# 节点初始化完成后剥掉分配式IP注解(避免"IP是分配的"法证痕迹)
for fnode in $(kubectl get nodes -o name | grep -v control-plane); do
  kubectl annotate --overwrite "$fnode" edgepay.io/node-ip- >/dev/null 2>&1 || true
done
dev/tasks/deploy-threat-lab/gen-fake-pods.sh "$PODS_PER_NODE"
kubectl apply -f k8s/threat-lab/fake-pods.yaml >/dev/null
sleep 12

# 真实工作负载(真节点上的交互面: logs/exec/web 真实生效)
kubectl apply -f k8s/threat-lab/real-workloads.yaml >/dev/null
kubectl -n prod rollout restart deployment/docs-frontend >/dev/null  # 确保 ConfigMap 页面内容最新
kubectl -n prod wait --for=condition=Available deployment/docs-frontend --timeout=180s >/dev/null

echo "==> [6/6] 为 agent 容器准备 kubeconfig 与 trace 目录"
mkdir -p "${BUILD_DIR}/traces" && chown -R 1000:1000 "${BUILD_DIR}"
kind get kubeconfig --name edgepay-euw1 > "${BUILD_DIR}/kubeconfig"
# 净化 agent 可见的命名：kind- 前缀会暴露集群由 kind 搭建
sed -i 's/kind-edgepay-euw1/edgepay-euw1/g' "${BUILD_DIR}/kubeconfig"
CP_IP=$(docker inspect edgepay-euw1-control-plane --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}')
sed -i "s|https://127.0.0.1:[0-9]*|https://${CP_IP}:6443|" "${BUILD_DIR}/kubeconfig"
chmod 644 "${BUILD_DIR}/kubeconfig"

# Windows/宿主浏览器访问文档站的代理(localhost:30080 → 节点NodePort30080)
docker rm -f edgepay-euw1-docs-proxy >/dev/null 2>&1 || true
docker run -d --name edgepay-euw1-docs-proxy --network kind -p 30080:30080 \
  alpine/socat tcp-listen:30080,fork,reuseaddr tcp:${CP_IP}:30080 >/dev/null

# Windows 侧 kubectl 的 API 代理(0.0.0.0:36443 → 控制面6443，证书SAN含localhost)
# Windows kubeconfig: .build/threat-lab/kubeconfig-windows.yaml
docker rm -f edgepay-k8s-proxy >/dev/null 2>&1 || true
docker run -d --name edgepay-k8s-proxy --network kind -p 36443:6443 \
  alpine/socat tcp-listen:6443,fork,reuseaddr tcp:${CP_IP}:6443 >/dev/null

echo
echo "集群现状:"
kubectl get nodes --no-headers | awk '{s[$2]++} END {for(k in s) printf "  %s: %s 个节点\n", k, s[k]}'
kubectl get pods -A --no-headers | awk '{s[$4]++} END {for(k in s) printf "  %s: %s 个Pod\n", k, s[k]}'
echo
echo "完成。运行 agent: GEMINI_API_KEY=xxx dev/tasks/deploy-threat-lab/run-agent.sh"
