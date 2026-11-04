#!/usr/bin/env bash
# Generate node-pool members (native Node objects)
# Managed by infra-lifecycle-controller (annotation edgepay.io/managed)
# Unique node IP (edgepay.io/node-ip), podCIDR, and a standard label set
# Usage: gen-fake-nodes.sh [node-count]  (default 20); names derived deterministically from md5
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NODES=${1:-20}
OUT=${OUT:-${REPO_ROOT}/sim/manifests/fake-nodes.yaml}

NODE_POOLS=("prod-euw1-a" "prod-euw1-b" "prod-euw1-c" "staging-euw1-a" "dev-euw1-a")
INSTANCE_TYPES=("n2-standard-4" "n2-standard-8" "n2-standard-8" "n2-standard-16" "n2-standard-4")
hex5() { printf '%s' "$1" | md5sum | cut -c1-5; }
mod() { echo $(( 0x$(printf '%s' "$1" | md5sum | cut -c1-8) % $2 )); }

: > "$OUT"
for n in $(seq 1 "$NODES"); do
  pool=${NODE_POOLS[$(( (n - 1) % ${#NODE_POOLS[@]} ))]}
  id=$(hex5 "threat-lab-node-${n}")
  name="node-${pool}-${id}"
  ip="192.168.32.$(( 10 + n ))"          # same subnet as the control plane (192.168.32.2)
  cidr="10.244.$(mod "cidr-${n}" 200).0/24"   # hashed to avoid a sequential pattern
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
  # pool-node scheduling isolation (pool workloads bind statically via nodeName)
  taints:
  - key: edgepay.io/pool
    value: workers
    effect: NoSchedule
---
EOF
done

# namespaces the fake pods live in
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
