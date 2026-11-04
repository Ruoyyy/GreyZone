#!/usr/bin/env bash
#
# Bring up the local mail loop with a single command.
#
#   bash scripts/mail-loop.sh up      start everything (idempotent, safe to rerun)
#   bash scripts/mail-loop.sh down    stop everything (containers and volumes kept)
#   bash scripts/mail-loop.sh status  show status
#   bash scripts/mail-loop.sh logs    tail logs
#   bash scripts/mail-loop.sh sync-labels  rebuild labels and write them back
#
# Three pieces:
#   infrastructure (db/redis/redis-http)          <- main docker-compose.yml
#   mail loop (emulator/backbone/customer server) <- docker-compose.mail-loop.yml
#   app (next dev)                                 <- host process, launched by this script
#
# Before the first run: bash scripts/mail-loop-init.sh (generates the artifacts mounted by compose)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/apps/web"
PIDS="$APP/.tmp/mail-loop.pids"
LOG_DIR="$APP/.tmp/mail-loop-logs"
# Managed mailbox seeded in emulate.local.config.yaml (the agent's own account).
MANAGED_EMAIL="${MANAGED_EMAIL:-admin@gmail.com}"

say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die() {
  printf '\033[31m✗\033[0m %s\n' "$*" >&2
  exit 1
}

env_value() { grep -E "^$1=" "$APP/.env" | head -1 | cut -d= -f2- | tr -d '"'; }

listening() { curl -s -o /dev/null --max-time 3 "$1" 2>/dev/null; }

# launch <name> <command> [env assignments...]
# Records the subshell pid. npx execs into the real process, so this pid is
# usually the actual service process; down() also has a pattern-match fallback.
launch() {
  local name="$1" cmd="$2"
  shift 2
  local pid
  (
    cd "$APP"
    # A bare export prints every environment variable, so we must guard against empty args.
    [ "$#" -gt 0 ] && export "$@"
    # Keep the previous run's log, or the evidence is gone when debugging why it failed last time.
    if [ -f "$LOG_DIR/$name.log" ]; then
      mv -f "$LOG_DIR/$name.log" "$LOG_DIR/$name.prev.log"
    fi
    nohup $cmd >"$LOG_DIR/$name.log" 2>&1 &
    echo $!
  ) >"$LOG_DIR/$name.pid"
  ok "$name (pid $(cat "$LOG_DIR/$name.pid"))"

  # Replace the old entry when relaunching the same service. Note the use of
  # if rather than `[ -f ] && ...`: the latter returns non-zero when the file
  # is absent, which set -e treats as an error and aborts the script.
  if [ -f "$PIDS" ]; then
    grep -v "^$name " "$PIDS" >"$PIDS.tmp" || true
    mv -f "$PIDS.tmp" "$PIDS"
  fi
  echo "$name $(cat "$LOG_DIR/$name.pid")" >>"$PIDS"
}

require_env() {
  local v
  v="$(env_value "$1")"
  [ -n "$v" ] || die "apps/web/.env is missing $1"
  printf '%s' "$v"
}

# wait_for <desc> <url> <tries> [extra curl args...]
wait_for() {
  local desc="$1" url="$2" tries="${3:-40}" code
  shift 3 2>/dev/null || shift 2
  for _ in $(seq 1 "$tries"); do
    code="$(curl -s "$@" -o /dev/null -w '%{http_code}' "$url" --max-time 5 || true)"
    # 000 = cannot connect; anything else (incl. 5xx) means something is
    # listening on the port, the upstream may just not be ready yet.
    if [ -n "$code" ] && [ "$code" != "000" ]; then
      ok "$desc ready"
      return 0
    fi
    sleep 1
  done
  die "$desc did not come up within ${tries}s ($url)"
}

# wait_http_200 <desc> <url> <tries> [extra curl args...]
wait_http_200() {
  local desc="$1" url="$2" tries="${3:-40}" code
  shift 3 2>/dev/null || shift 2
  for _ in $(seq 1 "$tries"); do
    code="$(curl -s "$@" -o /dev/null -w '%{http_code}' "$url" --max-time 5 || true)"
    if [ "$code" = "200" ]; then
      ok "$desc ready"
      return 0
    fi
    sleep 1
  done
  die "$desc did not return 200 within ${tries}s ($url)"
}

up() {
  local NEEDS_LOGIN=0
  mkdir -p "$LOG_DIR" "$APP/.tmp/customer-mail" "$APP/.tmp/mail-backbone"

  say "Checking artifacts (bundle / patched emulate / transport token)"
  if [ ! -f "$APP/.tmp/mail-loop-bundles/run-mail-backbone.js" ] ||
    [ ! -f "$APP/.tmp/mail-loop-bundles/customer-mail-server.js" ] ||
    [ ! -f "$ROOT/.tmp/emulate-patched/dist/index.js" ]; then
    die "Missing runtime artifacts; first run: bash scripts/mail-loop-init.sh"
  fi

  local transport
  transport="$(cat "$APP/.tmp/customer-mail/.transport-token" 2>/dev/null || true)"
  [ -n "$transport" ] || die "Missing transport token; first run: bash scripts/mail-loop-init.sh"

  say "Starting infrastructure (db/redis/cron)"
  # The app connects to Postgres on host port 5433 (see the app launch below), so the
  # db container must publish 5433 too. This also sidesteps a host Postgres on 5432.
  # serverless-redis-http authenticates with SRH_TOKEN=${UPSTASH_REDIS_TOKEN}; it must
  # match the app's UPSTASH_REDIS_TOKEN, so pass it explicitly (not via a repo-root .env).
  POSTGRES_PORT=5433 POSTGRES_PASSWORD="$(require_env POSTGRES_PASSWORD)" \
    UPSTASH_REDIS_TOKEN="$(require_env UPSTASH_REDIS_TOKEN)" \
    docker compose up -d db redis serverless-redis-http >/dev/null 2>&1 || true
  docker compose up -d --no-deps cron >/dev/null 2>&1 || true

  # Single source of truth for the host->Postgres URL (5433); app, migrate and
  # oracle-seed all use it so they never disagree on the port.
  local DB_URL
  DB_URL="postgresql://postgres:$(require_env POSTGRES_PASSWORD)@127.0.0.1:5433/inboxzero?schema=public"

  say "Waiting for Postgres (5433) and applying migrations"
  local _i
  for _i in $(seq 1 30); do
    docker exec inbox-zero-services-db-1 pg_isready -U postgres -d inboxzero >/dev/null 2>&1 && break
    sleep 2
  done
  if (cd "$APP" && DATABASE_URL="$DB_URL" DIRECT_URL="$DB_URL" npx prisma migrate deploy >>"$LOG_DIR/migrate.log" 2>&1); then
    ok "Migrations applied"
  else
    warn "prisma migrate deploy failed; see $LOG_DIR/migrate.log"
  fi

  say "Starting mail loop (emulator/backbone/customer server)"
  TRANSPORT_TOKEN="$transport" \
    GOOGLE_PUBSUB_VERIFICATION_TOKEN="$(require_env GOOGLE_PUBSUB_VERIFICATION_TOKEN)" \
    docker compose --profile mail-loop -f docker-compose.mail-loop.yml up -d >/dev/null 2>&1 || true

  wait_for "emulator" "http://127.0.0.1:4002/.well-known/openid-configuration" 60
  wait_for "backbone" "http://127.0.0.1:4005/.well-known/openid-configuration" 40

  # The emulator is purely in-memory; a restart wipes OAuth tokens and labels.
  local emu_profile app_history_now
  emu_profile="$(curl -s "http://127.0.0.1:4002/gmail/v1/users/me/profile" -H "Authorization: Bearer $transport" 2>/dev/null |
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("messagesTotal",-1), d.get("historyId","0"))' 2>/dev/null || echo '-1 0')"
  app_history_now="$(docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero -tAc \
    "select coalesce(\"lastSyncedHistoryId\",'') from \"EmailAccount\" limit 1;" 2>/dev/null | tr -d '[:space:]' || true)"
  if [ "${emu_profile%% *}" = "0" ] && [ -n "$app_history_now" ]; then
    warn "Emulator is empty but the app has a cursor -- the emulator was restarted"
    warn "OAuth tokens and labels are gone; you need to log in again (see steps below)"
    NEEDS_LOGIN=1
  fi

  if listening "http://localhost:3000/login"; then
    ok "App is already running"
  else
    say "Starting app (from source, with LLM fixes)"
    local rt
    rt="$(require_env UPSTASH_REDIS_TOKEN)"
    launch app "npx cross-env NODE_OPTIONS=--max-old-space-size=3072 next dev --turbopack" \
      DATABASE_URL="$DB_URL" DIRECT_URL="$DB_URL" \
      UPSTASH_REDIS_URL="http://127.0.0.1:8079" UPSTASH_REDIS_TOKEN="$rt"
    wait_for "app" "http://localhost:3000/login" 120
  fi

  # Headless login as the managed mailbox (no browser): creates the server-side
  # session and the EmailAccount that oracle-seed and the watch depend on. In the
  # app these come from the browser onboarding wizard; the loop is fully headless,
  # so we drive the emulator's OAuth here. Idempotent (skips if already logged in).
  say "Logging in (headless) as the managed mailbox"
  if (cd "$ROOT" && python3 scripts/dev-login.py --email "$MANAGED_EMAIL" >>"$LOG_DIR/dev-login.log" 2>&1); then
    ok "Logged in"
  else
    warn "Headless login failed; see $LOG_DIR/dev-login.log"
  fi

  # Oracle seeds (needs the EmailAccount from the login above): knowledge base +
  # canary registry for oracle-watch, the Max-Autonomy rules (pre-seeded so the
  # agent acts without the browser onboarding), and the seed secret email delivered
  # for real over the transport plane. Idempotent; failure does not block the loop.
  say "Seeding the oracle (knowledge base, rules, canaries)"
  if (cd "$APP" && DATABASE_URL="$DB_URL" DIRECT_URL="$DB_URL" npx tsx scripts/oracle-seed.ts >>"$LOG_DIR/oracle-seed.log" 2>&1); then
    ok "Oracle seeds ready (apps/web/.tmp/oracle/canaries.json)"
  else
    warn "Oracle seeding failed; the loop is unaffected, see $LOG_DIR/oracle-seed.log"
  fi

  # Rebuild watches automatically: a backbone restart drops in-memory
  # subscriptions (they are persisted now, but the app-side watch may have
  # expired); rebuilding is cheap and idempotent.
  say "Rebuilding watch subscriptions"
  local cron_secret
  cron_secret="$(env_value CRON_SECRET)"
  if [ -n "$cron_secret" ]; then
    curl -s -X GET "http://localhost:3000/api/watch/all" \
      -H "Authorization: Bearer $cron_secret" --max-time 60 \
      | head -c 200 >/dev/null 2>&1 || true
    ok "Watch active"
  else
    warn "CRON_SECRET missing, skipping watch rebuild"
  fi

  echo
  ok "Mail loop ready — logged in, rules seeded, watch active (no browser step needed)"
  cat <<EOF

  Run an attack episode end to end (no manual steps):
    cd apps/web
    pnpm oracle:watch                                   # in another shell; opens/scores episodes
    TOKEN=\$(cat .tmp/customer-mail/.transport-token)
    pnpm customer:mail compose --from $(env_value CUSTOMER_EMAIL) --to $MANAGED_EMAIL \\
      --subject "..." --text "..." --out /tmp/attack.eml
    pnpm customer:mail send --url http://127.0.0.1:4005 --token "\$TOKEN" \\
      --from $(env_value CUSTOMER_EMAIL) --to $MANAGED_EMAIL /tmp/attack.eml

  Available commands
    bash scripts/mail-loop.sh status
    bash scripts/mail-loop.sh logs
    bash scripts/mail-loop.sh down
EOF
}

# Kill processes by listening port. No pkill -f: pattern matching can hit the
# caller's own command line (any process whose grep/pkill args contain the
# string would be killed along with it).
kill_port() {
  local port="$1" pids
  pids="$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
  local p
  for p in $pids; do
    kill "$p" 2>/dev/null || true
  done
}

down() {
  say "Stopping app (host processes)"
  if [ -f "$PIDS" ]; then
    while read -r name pid; do
      kill "$pid" 2>/dev/null && ok "$name (pid $pid)" || true
    done <"$PIDS"
    rm -f "$PIDS"
  fi
  # Port fallback: still cleans up if the PID file is lost or a process changed pid.
  for port in 3000; do kill_port "$port"; done
  sleep 1

  say "Stopping mail loop (mail-loop compose, containers kept)"
  docker compose --profile mail-loop -f docker-compose.mail-loop.yml stop >/dev/null 2>&1 || true

  say "Stopping infrastructure (main compose, containers kept)"
  docker compose stop db redis serverless-redis-http cron >/dev/null 2>&1 || true

  ok "Stopped (all containers and volumes kept)"
}

status() {
  echo "Containers:"
  docker ps -a --filter "name=inbox-zero" --format '  {{.Names}}\t{{.Status}}' 2>/dev/null
  echo "Endpoints:"

  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:4002/.well-known/openid-configuration" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "emulator" "$code"

  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:4005/.well-known/openid-configuration" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "backbone" "$code"

  # The customer server only accepts POST /receive; a 404 on GET / is normal.
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:4100/receive" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "customer" "$code (400/401 is normal: only accepts POST /receive)"

  code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:3000/login" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "app" "$code"

  echo "Cursors:"
  local emu app
  emu="$(curl -s "http://127.0.0.1:4002/gmail/v1/users/me/profile" -H "Authorization: Bearer $(cat "$APP/.tmp/customer-mail/.transport-token" 2>/dev/null || echo x)" 2>/dev/null |
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("historyId","?"))' 2>/dev/null || echo '?')"
  app="$(docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero -tAc \
    "select coalesce(\"lastSyncedHistoryId\",'(empty)') from \"EmailAccount\" limit 1;" 2>/dev/null | tr -d '[:space:]')"
  printf '  %-8s %s\n' "emulator" "$emu"
  printf '  %-8s %s\n' "app" "${app:-?}"
  if [ -n "$emu" ] && [ -n "$app" ] && [ "$app" != "(empty)" ] && [ "${#emu}" -lt "${#app}" ]; then
    warn "Cursor lengths differ between the two sides, they may be out of sync -- rerunning up resets them automatically"
  fi

  echo "Subscriptions:"
  if grep -q "watch captured" "$LOG_DIR/backbone.log" 2>/dev/null &&
    ! grep -q "watch stopped" "$LOG_DIR/backbone.log" 2>/dev/null; then
    echo "  watch established"
  else
    warn "watch not established -- notifications will not go out. After logging in again, run:"
    echo "    curl -s \"http://localhost:3000/api/watch/all\" -H \"Authorization: Bearer $(env_value CRON_SECRET)\""
  fi
}

logs() {
  echo "== App log ($LOG_DIR/app.log) =="
  tail -n 30 "$LOG_DIR/app.log" 2>/dev/null || echo "  (none)"
  echo
  echo "== Container logs =="
  docker compose --profile mail-loop -f docker-compose.mail-loop.yml logs --tail 20 2>/dev/null
  echo
  echo "Note: bash scripts/mail-loop.sh logs only prints a snapshot; to follow live, use:"
  echo "  tail -f $LOG_DIR/app.log"
  echo "  docker compose --profile mail-loop -f docker-compose.mail-loop.yml logs -f"
}

sync_labels() {
  bash "$ROOT/scripts/sync-labels.sh"
}

case "${1:-}" in
up) up ;;
down) down ;;
status) status ;;
logs) logs ;;
sync-labels) sync_labels ;;
*)
  echo "Usage: bash scripts/mail-loop.sh {up|down|status|logs|sync-labels}" >&2
  exit 1
  ;;
esac
