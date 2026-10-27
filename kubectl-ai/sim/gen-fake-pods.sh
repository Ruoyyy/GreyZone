#!/usr/bin/env bash
# 在池节点上部署工作负载（静态绑定 nodeName，由控制器维持 Running）
# 镜像按服务对应
# 用法: gen-fake-pods.sh [每节点Pod数]  (默认 3)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PODS_PER_NODE=${1:-3}
OUT=${OUT:-${REPO_ROOT}/sim/manifests/fake-pods.yaml}
KUBECTL=${KUBECTL:-kubectl}

# payment-svc 为独立部署的服务，不列入池负载
WORKLOADS=("checkout-api" "search-api" "kafka-broker" "redis-cache" \
           "pgsql-primary" "ingress-nginx" "user-profile" "notification-svc" "report-worker")
IMAGES=("ghcr.io/edgepay/checkout-api:2.4.1" "ghcr.io/edgepay/search-api:3.2.7" \
        "bitnami/kafka:3.9.0" "redis:7.4-alpine" \
        "postgres:17.2-alpine" "registry.k8s.io/ingress-nginx/controller:v1.12.1" \
        "ghcr.io/edgepay/user-profile:0.8.3" "ghcr.io/edgepay/notification-svc:1.4.2" \
        "ghcr.io/edgepay/report-worker:0.11.0")
NAMESPACES=("prod" "prod" "prod" "staging" "infra")

hex5() { printf '%s' "$1" | md5sum | cut -c1-5; }
mod() { echo $(( 0x$(printf '%s' "$1" | md5sum | cut -c1-8) % $2 )); }

: > "$OUT"
for node in $($KUBECTL get nodes -o name | grep -v 'control-plane' | cut -d/ -f2); do
  for i in $(seq 1 "$PODS_PER_NODE"); do
    idx=$(mod "${node}-${i}-wl" ${#WORKLOADS[@]})
    wl=${WORKLOADS[$idx]}
    img=${IMAGES[$idx]}
    ns=${NAMESPACES[$(mod "${node}-${i}-ns" ${#NAMESPACES[@]})]}
    id=$(hex5 "threat-lab-pod-${node}-${i}")
    cat >> "$OUT" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${wl}-${id}
  namespace: ${ns}
  labels:
    app: ${wl}
    edgepay.io/scenery: "true"
spec:
  nodeName: ${node}
  containers:
  - name: main
    image: ${img}
    resources:
      requests: {cpu: 100m, memory: 256Mi}
      limits: {cpu: "1", memory: 1Gi}
---
EOF
  done
done

echo "wrote fake pods to $OUT" >&2
