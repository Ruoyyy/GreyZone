#!/usr/bin/env bash
#
# Aligns the label IDs referenced by the app DB with the labels that actually
# exist in the emulator right now.
#
#   bash scripts/mail-loop.sh sync-labels
#
# Why this is needed: the emulator is in-memory, so a restart regenerates the
# labels with entirely new IDs while the app DB still stores the old ones --
# rule execution then fails with "Invalid label IDs" and ExecutedRule goes
# ERROR.
#
# Idempotent: look up by name in the emulator (creating if missing), then
# write back everywhere the DB stores a label ID.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/apps/web"
DB=inbox-zero-services-db-1
TRANSPORT="$(cat "$APP/.tmp/customer-mail/.transport-token" 2>/dev/null || echo x)"

say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
psql_q() { docker exec "$DB" psql -U postgres -d inboxzero -tAc "$1" 2>/dev/null; }

# The emulator is in-memory, so user labels all vanish after a restart --
# "none exist" is a normal state, not an error; they are rebuilt on demand
# below.
existing="$(curl -s "http://127.0.0.1:4002/gmail/v1/users/me/labels" \
  -H "Authorization: Bearer $TRANSPORT" |
  python3 -c 'import json,sys
for l in json.load(sys.stdin).get("labels",[]):
    if l.get("type")=="user": print("%s|%s" % (l["name"], l["id"]))' 2>/dev/null || true)"

if ! curl -s -o /dev/null "http://127.0.0.1:4002/.well-known/openid-configuration" --max-time 5; then
  warn "Emulator is not running"
  exit 1
fi

# Only align labels actually referenced by rule actions -- those are the only ones the app cares about.
names="$(psql_q "select distinct label from \"Action\" where label is not null order by label;")"
if [ -z "$names" ]; then
  warn "No label references in the Action table"
  exit 0
fi

changed=0
while IFS= read -r name; do
  [ -n "$name" ] || continue

  # Note the trailing `|| true`: existing may not contain this name, grep
  # returns 1, and set -e + pipefail would let that pipeline kill the script.
  id="$(grep -F "${name}|" <<<"$existing" | head -1 | cut -d'|' -f2 || true)"
  if [ -z "$id" ]; then
    say "Emulator is missing '$name', creating"
    id="$(curl -s -X POST "http://127.0.0.1:4002/gmail/v1/users/me/labels" \
      -H "Authorization: Bearer $TRANSPORT" -H "Content-Type: application/json" \
      -d "{\"name\":\"$name\"}" |
      python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
  fi
  if [ -z "$id" ]; then
    warn "Could not parse id for '$name', skipping"
    continue
  fi

  stored="$(psql_q "select \"labelId\" from \"Action\" where label='$name' limit 1;")"
  if [ "$stored" = "$id" ]; then
    continue
  fi

  psql_q "update \"Action\" set \"labelId\"='$id' where label='$name';" >/dev/null
  # MailSplitFilter stores the same label ID; skipping it would make rules blow up once more on another path.
  psql_q "update \"MailSplitFilter\" set value='$id' where value='$stored';" >/dev/null
  printf '  %-18s %s -> %s\n' "$name" "$stored" "$id"
  changed=$((changed + 1))
done <<<"$names"

if [ "$changed" -eq 0 ]; then
  say "Labels already aligned, nothing to change"
else
  say "Aligned $changed label(s)"
fi
