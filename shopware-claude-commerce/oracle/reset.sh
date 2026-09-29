#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Episode reset: back to the snapshot's canonical state. The snapshot covers the
# database and media; the hosts' own state (ledger, mail outboxes, supplier
# invoice) lives outside MySQL and is cleared here; a host restart drops the
# merchant catalog cache. Run between benchmark episodes, not inside one.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== shop: restore snapshot =="
SHOPWARE_CONTAINER="${SHOPWARE_CONTAINER:-commerce-agents-shopware}" docker/restore_snapshot.sh

echo "== host-side state: ledger, outboxes, invoice =="
rm -f merchant/data/ledger.db
: > storefront/data/mail-outbox.jsonl 2>/dev/null || true
: > merchant/data/mail-outbox.jsonl 2>/dev/null || true
rm -f merchant/data/supplier-invoice.txt

echo "== hosts: restart (drops the merchant catalog cache) =="
./dev.sh stop
./dev.sh start

echo "== reset complete: canonical state =="
