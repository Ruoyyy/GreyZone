#!/usr/bin/env bash
# Replace the shop's database and media with docker/snapshot/ (written by docker/snapshot.sh).
# Called by docker/bootstrap.sh on a fresh volume; destructive — the current database is dropped.
# Also runnable on its own (the storefront theme is recompiled below); two things only
# bootstrap does afterwards: point the sales-channel domain at a URL other than the
# snapshot's http://localhost:8080, and — like bootstrap — it leaves the host services'
# in-memory catalogs alone, so run ./dev.sh restart once it finishes.
#
# Order history keeps its shape: every order-related timestamp moves forward by the time
# since SNAPSHOT_TAKEN_AT, so "last 30 days" metrics and order ages read as when captured.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="${SHOPWARE_CONTAINER:-commerce-agents-shopware}"
SNAPSHOT="$ROOT/docker/snapshot"
MYSQL=(mysql -h127.0.0.1 -uroot -proot --default-character-set=utf8mb4)

SNAPSHOT_TAKEN_AT="$(cat "$SNAPSHOT/captured-at.txt")"

echo "database: dropping and importing shopware.sql.gz (taken ${SNAPSHOT_TAKEN_AT} UTC)"
docker exec "$CONTAINER" "${MYSQL[@]}" -e \
  'DROP DATABASE IF EXISTS shopware; CREATE DATABASE shopware DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;' 2>/dev/null
gunzip -c "$SNAPSHOT/shopware.sql.gz" | docker exec -i "$CONTAINER" "${MYSQL[@]}" shopware 2>/dev/null

echo "orders: shifting timestamps forward by the snapshot's age"
shift_columns() {  # shift_columns TABLE COLUMN... — move non-null values by @age seconds
  local table="$1" set="" column
  shift
  for column in "$@"; do
    set+="${set:+, }\`${column}\` = \`${column}\` + INTERVAL @age SECOND"
  done
  echo "UPDATE \`${table}\` SET ${set};"
}
{
  echo "SET @age = GREATEST(0, TIMESTAMPDIFF(SECOND, '${SNAPSHOT_TAKEN_AT}', UTC_TIMESTAMP()));"
  echo "SELECT CONCAT('  age ', ROUND(@age / 86400, 1), ' days');"
  shift_columns order order_date_time created_at updated_at   # order_date is generated from order_date_time
  shift_columns order_delivery shipping_date_earliest shipping_date_latest created_at updated_at
  for table in order_address order_customer order_delivery_position order_line_item order_transaction \
      order_transaction_capture order_transaction_capture_refund document; do
    shift_columns "$table" created_at updated_at
  done
  echo "UPDATE state_machine_history SET created_at = created_at + INTERVAL @age SECOND,"
  echo "  updated_at = updated_at + INTERVAL @age SECOND"
  echo "  WHERE entity_name IN ('order', 'order_delivery', 'order_transaction');"
} | docker exec -i "$CONTAINER" "${MYSQL[@]}" -N shopware 2>/dev/null

echo "media: replacing public/media (and thumbnail) with media.tar.gz"
docker exec -u root "$CONTAINER" bash -c 'rm -rf /var/www/html/public/media /var/www/html/public/thumbnail'
gunzip -c "$SNAPSHOT/media.tar.gz" | docker exec -i -u root "$CONTAINER" tar -C /var/www/html/public -xf -
docker exec -u root "$CONTAINER" chown -R www-data:www-data /var/www/html/public/media
docker exec -u root "$CONTAINER" bash -c 'test -d /var/www/html/public/thumbnail && chown -R www-data:www-data /var/www/html/public/thumbnail || true'

docker exec -u www-data "$CONTAINER" bash -lc 'cd /var/www/html && php bin/console cache:clear --no-interaction' >/dev/null
# The snapshot carries the database, not the compiled theme assets in public/theme/:
# after the restore the theme rows point at a hash that may no longer exist on this
# container's filesystem, and the storefront serves its CSS as a 404. Recompiling
# regenerates the assets from the restored theme config.
docker exec -u www-data "$CONTAINER" bash -lc 'cd /var/www/html && php bin/console theme:compile --no-interaction' >/dev/null
echo "snapshot restored"
