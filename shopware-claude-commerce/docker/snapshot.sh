#!/usr/bin/env bash
# Capture the running shop as the starting state that docker/bootstrap.sh restores on a
# fresh volume (docker/restore_snapshot.sh). Writes docker/snapshot/:
#
#   shopware.sql.gz  the whole database; runtime tables (carts, logs, message queue, UCP
#                    sessions/tokens, profile cache) keep their structure but no rows
#   media.tar.gz     public/media and public/thumbnail — files live in the container, not
#                    in the MySQL volume, so product images need their own copy
#   snapshot.env     SNAPSHOT_TAKEN_AT (UTC); the restore shifts order dates by the age
#
#     docker/snapshot.sh            # from the repository root, with the stack running
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="${SHOPWARE_CONTAINER:-commerce-agents-shopware}"
OUT="$ROOT/docker/snapshot"
DB="shopware"
MYSQL_AUTH=(-h127.0.0.1 -uroot -proot)

RUNTIME_TABLES=(
  cart log_entry messenger_messages increment notification webhook_event_log
  ucp_platform_profile_cache ucp_negotiation_sessions ucp_oauth_state
  swag_agentic_commerce_ucp_oauth_access_token swag_agentic_commerce_ucp_oauth_code
  swag_agentic_commerce_ucp_oauth_refresh_token
)

mkdir -p "$OUT"
taken_at="$(docker exec "$CONTAINER" mysql "${MYSQL_AUTH[@]}" -N -e 'SELECT UTC_TIMESTAMP()' 2>/dev/null)"

ignore=()
for table in "${RUNTIME_TABLES[@]}"; do ignore+=("--ignore-table=${DB}.${table}"); done
dump=(mysqldump "${MYSQL_AUTH[@]}" --single-transaction --hex-blob --default-character-set=utf8mb4 --skip-dump-date)
{
  docker exec "$CONTAINER" "${dump[@]}" "${ignore[@]}" "$DB"
  docker exec "$CONTAINER" "${dump[@]}" --no-data "$DB" "${RUNTIME_TABLES[@]}"
} 2>/dev/null | gzip -n -9 > "$OUT/shopware.sql.gz"

docker exec "$CONTAINER" bash -c 'cd /var/www/html/public && tar -cf - media $(test -d thumbnail && echo thumbnail)' \
  | gzip -n -9 > "$OUT/media.tar.gz"

echo "SNAPSHOT_TAKEN_AT='${taken_at}'" > "$OUT/snapshot.env"
echo "snapshot written to docker/snapshot/ (taken ${taken_at} UTC):"
ls -la "$OUT" | awk 'NR > 3 {print "  " $5 "\t" $9}'
