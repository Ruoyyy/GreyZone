#!/usr/bin/env bash
# One-shot provisioning of the edgepay-euw1 cluster (control plane + node pool + workloads + ops tooling)
# Usage: sim/up.sh [fake-node-count] [fake-pods-per-node]  (default 20 3)
# Idempotent: skips cluster creation if it already exists; safe to re-run
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

NODES=${1:-20}
PODS_PER_NODE=${2:-3}
KWOK_VERSION=${KWOK_VERSION:-v0.8.0}
BUILD_DIR="${REPO_ROOT}/sim/.build"

echo "==> [1/6] Build kubectl-ai (same path as make build: bin/kubectl-ai)"
mkdir -p bin
CGO_ENABLED=0 go build -o bin/kubectl-ai ./cmd

echo "==> [2/6] Build agent container image kubectl-ai:lab"
docker build -q -f sim/victim/Dockerfile -t kubectl-ai:lab . >/dev/null

echo "==> [3/6] kind cluster edgepay-euw1 (single control plane = the only real node)"
kind get clusters 2>/dev/null | grep -q '^edgepay-euw1$' || kind create cluster --name edgepay-euw1 --kubeconfig ~/.kube/config
kubectl wait --for=condition=Ready node/edgepay-euw1-control-plane --timeout=300s

echo "==> [4/6] Deploy node lifecycle controller (infra-lifecycle-controller) + Stage"
kubectl -n kube-system wait --for=condition=Available deployment/infra-lifecycle-controller --timeout=30s 2>/dev/null || {
  # Transitional cleanup: remove the old-named install (kwok-controller)
  kubectl -n kube-system delete deployment/kwok-controller service/kwok-controller \
    configmap/kwok flowschema/kwok-controller --ignore-not-found >/dev/null 2>&1 || true
  # Re-tag the image inside the node's containerd under the ghcr name (no pull when already present).
  # The upstream registry tag is v-prefixed (e.g. v0.8.0), so keep the v for the source image;
  # the ghcr rebrand tag drops it to match the manifest (e.g. 0.8.0).
  KWOK_IMG="registry.k8s.io/kwok/kwok:${KWOK_VERSION}"
  EDGE_IMG="ghcr.io/edgepay/infra-lifecycle:${KWOK_VERSION#v}"
  docker exec edgepay-euw1-control-plane ctr -n k8s.io images tag "${KWOK_IMG}" "${EDGE_IMG}" >/dev/null \
    || docker exec edgepay-euw1-control-plane ctr -n k8s.io images tag \
         "$(docker exec edgepay-euw1-control-plane ctr -n k8s.io images pull "${KWOK_IMG}" 2>/dev/null | tail -1 | awk '{print $3}')" "${EDGE_IMG}" >/dev/null \
    || true
  kubectl apply -f sim/manifests/kwok-controller.yaml >/dev/null
  kubectl apply -f "https://github.com/kubernetes-sigs/kwok/releases/download/${KWOK_VERSION}/stage-fast.yaml" >/dev/null 2>&1 || true
  # Stage CRD updates need optimistic locking; delete and recreate to override node-initialize (version/IP alignment)
  kubectl delete stage node-initialize --ignore-not-found >/dev/null 2>&1 || true
  kubectl apply -f sim/manifests/stage-node-initialize-override.yaml >/dev/null
  kubectl -n kube-system wait --for=condition=Available deployment/infra-lifecycle-controller --timeout=180s
}

echo "==> [5/6] Scale up ${NODES}-node pool + ${PODS_PER_NODE} workloads per node (convergent)"
# Converge-clean old workloads and nodes (handles both old/new labels; --wait=false avoids finalizer hangs)
kubectl delete pods -A -l kwok.x-k8s.io/node=fake --wait=false --ignore-not-found >/dev/null 2>&1 || true
kubectl delete pods -A -l edgepay.io/scenery=true --wait=false --ignore-not-found >/dev/null 2>&1 || true
# Fallback: force-strip finalizers on pods still stuck Terminating (after a controller selector change, old pods have no releaser)
kubectl get pods -A --no-headers 2>/dev/null | awk '$4=="Terminating"{print $1" "$2}' | \
  while read -r ns p; do kubectl -n "$ns" patch pod "$p" --type=merge -p '{"metadata":{"finalizers":null}}' >/dev/null 2>&1 || true; done
STALE_NODES="$(kubectl get nodes -o go-template='{{range .items}}{{if eq (index .metadata.annotations "kwok.x-k8s.io/node") "fake"}}{{.metadata.name}} {{end}}{{end}}')$(kubectl get nodes -o go-template='{{range .items}}{{if eq (index .metadata.annotations "edgepay.io/managed") "true"}}{{.metadata.name}} {{end}}{{end}}')"
[[ -n "${STALE_NODES// /}" ]] && kubectl delete node $STALE_NODES --ignore-not-found >/dev/null
sleep 5

sim/gen-fake-nodes.sh "$NODES"
kubectl apply -f sim/manifests/fake-nodes.yaml >/dev/null
sleep 12
# Remove the IP-allocation annotation once node init is done
for fnode in $(kubectl get nodes -o name | grep -v control-plane); do
  kubectl annotate --overwrite "$fnode" edgepay.io/node-ip- >/dev/null 2>&1 || true
done
sim/gen-fake-pods.sh "$PODS_PER_NODE"
kubectl apply -f sim/manifests/fake-pods.yaml >/dev/null
sleep 12

# Core business workloads
kubectl apply -f sim/manifests/real-workloads.yaml >/dev/null
# Payment service and database
kubectl apply -f sim/manifests/real-secrets.yaml >/dev/null
kubectl -n prod rollout restart deployment/docs-frontend >/dev/null  # ensure the ConfigMap page content is current
kubectl -n prod wait --for=condition=Available deployment/docs-frontend --timeout=180s >/dev/null

echo "==> [6/6] Prepare kubeconfig and trace directory for the agent container"
mkdir -p "${BUILD_DIR}/traces" && chown -R 1000:1000 "${BUILD_DIR}"
kind get kubeconfig --name edgepay-euw1 > "${BUILD_DIR}/kubeconfig"
# Sanitize names visible to the agent: the kind- prefix would reveal the cluster was built with kind
sed -i 's/kind-edgepay-euw1/edgepay-euw1/g' "${BUILD_DIR}/kubeconfig"
CP_IP=$(docker inspect edgepay-euw1-control-plane --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}')
sed -i "s|https://127.0.0.1:[0-9]*|https://${CP_IP}:6443|" "${BUILD_DIR}/kubeconfig"
chmod 644 "${BUILD_DIR}/kubeconfig"

# Proxy for the Windows/host browser to reach the docs site (localhost:30080 -> node NodePort 30080)
docker rm -f edgepay-euw1-docs-proxy >/dev/null 2>&1 || true
docker run -d --restart unless-stopped --name edgepay-euw1-docs-proxy --network kind -p 30080:30080 \
  alpine/socat tcp-listen:30080,fork,reuseaddr tcp:${CP_IP}:30080 >/dev/null

# API proxy for kubectl on the Windows side (0.0.0.0:36443 -> control plane 6443; cert SAN includes localhost)
# Windows kubeconfig: sim/.build/kubeconfig-windows.yaml
docker rm -f edgepay-k8s-proxy >/dev/null 2>&1 || true
docker run -d --restart unless-stopped --name edgepay-k8s-proxy --network kind -p 36443:6443 \
  alpine/socat tcp-listen:6443,fork,reuseaddr tcp:${CP_IP}:6443 >/dev/null

echo
echo "Cluster status:"
kubectl get nodes --no-headers | awk '{s[$2]++} END {for(k in s) printf "  %s: %s nodes\n", k, s[k]}'
kubectl get pods -A --no-headers | awk '{s[$4]++} END {for(k in s) printf "  %s: %s pods\n", k, s[k]}'
echo
echo "Done. Run the agent: OPENAI_API_KEY=... OPENAI_MODEL=... sim/run-agent.sh"
