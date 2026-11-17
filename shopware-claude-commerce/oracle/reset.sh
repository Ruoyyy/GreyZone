#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Episode reset: back to the snapshot's canonical state. The snapshot covers the
# database and media; the hosts' own state (ledger, mail outboxes, supplier
# invoice) lives outside MySQL and is cleared here; a host restart drops the
# merchant catalog cache. Run between benchmark episodes, not inside one.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/docker/ports.sh"
load_port_config
cd "$ROOT"

echo "== hosts: stop before database restoration =="
./dev.sh stop

echo "== shop: restore snapshot =="
SHOPWARE_CONTAINER="${SHOPWARE_CONTAINER:-commerce-agents-shopware}" docker/restore_snapshot.sh
"${PYTHON:-$ROOT/.venv/bin/python}" docker/shop_domain.py --shop-url "$SHOPWARE_PUBLIC_URL"

echo "== host credentials: rebind to the restored database =="
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
"$PYTHON" docker/enable_ucp.py --shop-url "$SHOPWARE_PUBLIC_URL" --container "${SHOPWARE_CONTAINER:-commerce-agents-shopware}" --signature-policy strict
"$PYTHON" docker/write_credentials.py --shop-url "$SHOPWARE_PUBLIC_URL" --out docker/.generated.env
"$PYTHON" docker/merchant_identity.py --shop-url "$SHOPWARE_PUBLIC_URL" --generated-env docker/.generated.env
"$PYTHON" docker/seller_identity.py --shop-url "$SHOPWARE_PUBLIC_URL" --generated-env docker/.generated.env
"$PYTHON" docker/seed_buyers.py

echo "== host-side state: ledger, outboxes, invoice =="
rm -f merchant/data/ledger.db
rm -f merchant/data/.memory-store.json
: > storefront/data/mail-outbox.jsonl 2>/dev/null || true
: > merchant/data/mail-outbox.jsonl 2>/dev/null || true
rm -f merchant/data/supplier-invoice.txt

echo "== hosts: restart (drops the merchant catalog cache) =="
./dev.sh start

echo "== reset complete: canonical state =="
