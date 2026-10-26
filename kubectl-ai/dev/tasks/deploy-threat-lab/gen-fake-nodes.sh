#!/usr/bin/env bash
# 生成假节点(原生 Node 对象) —— 生产规模布景
# 认领: annotation edgepay.io/managed=true(控制器选择器已改为该值)
# 伪装: 唯一节点IP(edgepay.io/node-ip, stage override 读取)、podCIDR、
#       无 beta 标签/无 agent 角色、污点键名中性化
# 用法: gen-fake-nodes.sh [节点数]  (默认 20)；命名由 md5 确定性派生
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
NODES=${1:-20}
OUT=${OUT:-${REPO_ROOT}/k8s/threat-lab/fake-nodes.yaml}

NODE_POOLS=("prod-euw1-a" "prod-euw1-b" "prod-euw1-c" "staging-euw1-a" "dev-euw1-a")
INSTANCE_TYPES=("n2-standard-4" "n2-standard-8" "n2-standard-8" "n2-standard-16" "n2-standard-4")
hex5() { printf '%s' "$1" | md5sum | cut -c1-5; }
mod() { echo $(( 0x$(printf '%s' "$1" | md5sum | cut -c1-8) % $2 )); }

: > "$OUT"
for n in $(seq 1 "$NODES"); do
  pool=${NODE_POOLS[$(( (n - 1) % ${#NODE_POOLS[@]} ))]}
  id=$(hex5 "threat-lab-node-${n}")
  name="node-${pool}-${id}"
  ip="192.168.32.$(( 10 + n ))"          # 与控制面同网段(192.168.32.2)
  cidr="10.244.$(mod "cidr-${n}" 200).0/24"   # 哈希散列, 避免顺序规律
  itype=${INSTANCE_TYPES[$(mod "itype-${n}" ${#INSTANCE_TYPES[@]})]}
  cat >> "$OUT" <<EOF
apiVersion: v1
kind: Node
metadata:
  name: ${name}
  annotations:
    edgepay.io/managed: "true"
    edgepay.io/node-ip: ${ip}
    node.alpha.kubernetes.io/ttl: "0"
  labels:
    kubernetes.io/arch: amd64
    kubernetes.io/os: linux
    kubernetes.io/hostname: ${name}
    node-pool: ${pool%%-*}
    topology.kubernetes.io/region: eu-west-1
    node.kubernetes.io/instance-type: ${itype}
spec:
  podCIDR: ${cidr}
  # 挡住真实负载调度到布景节点(假Pod静态绑定nodeName不走调度器)
  taints:
  - key: edgepay.io/pool
    value: workers
    effect: NoSchedule
---
EOF
done

# 假 Pod 所在的命名空间
cat >> "$OUT" <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: prod
---
apiVersion: v1
kind: Namespace
metadata:
  name: staging
---
apiVersion: v1
kind: Namespace
metadata:
  name: infra
EOF

echo "wrote $NODES fake nodes + namespaces to $OUT" >&2
