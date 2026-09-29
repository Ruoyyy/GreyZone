#!/usr/bin/env bash
# Start / stop / restart the four local services together.
#
#   storefront-api  :8004  uvicorn storefront.api.main:app
#   merchant-api    :8005  uvicorn merchant.api.main:app
#   storefront-web  :3005  npm run dev:storefront
#   merchant-web    :3006  npm run dev:merchant
#
# Usage: ./dev.sh {start|stop|restart|status|logs [name]}
#
# start waits for every health URL; if any service fails to come up, all four are stopped
# again (all or nothing). stop also frees the ports when a service was started by hand.
# Shopware (docker/compose.yaml, :8080) is not started here, but start refuses when it is
# unreachable (the hosts would come up "healthy" yet fail every catalog/order call). Skip
# that guard with SHOPWARE_LOCAL_STORE=1 (in-process shop) or SKIP_SHOPWARE_CHECK=1.
# Logs and PID files: .run/
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$RUN_DIR/logs"
START_TIMEOUT="${START_TIMEOUT:-120}"
STOP_TIMEOUT="${STOP_TIMEOUT:-15}"

if [[ -x "$ROOT/.venv/bin/uvicorn" ]]; then
  UVICORN="$ROOT/.venv/bin/uvicorn"
else
  UVICORN="uvicorn"
fi

# name|port|health url|command
SERVICES=(
  "storefront-api|8004|http://127.0.0.1:8004/api/health|$UVICORN storefront.api.main:app --port 8004"
  "merchant-api|8005|http://127.0.0.1:8005/api/merchant/health|$UVICORN merchant.api.main:app --port 8005"
  "storefront-web|3005|http://127.0.0.1:3005/|npm run dev:storefront"
  "merchant-web|3006|http://127.0.0.1:3006/|npm run dev:merchant"
)

field() { cut -d'|' -f"$2" <<<"$1"; }

# ss rather than lsof: lsof misses Next's dual-stack (*:3005) listener under WSL.
port_pids() { ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u; }

pid_alive() { [[ -n "$1" ]] && kill -0 "$1" 2>/dev/null; }

read_pid() { [[ -f "$RUN_DIR/$1.pid" ]] && cat "$RUN_DIR/$1.pid"; }

healthy() { [[ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$1")" == "200" ]]; }

# --- Shopware backend (started elsewhere; the hosts talk to it) --------------
# A config value from the process env, else from .env (unquoted values, as bootstrap writes).
env_val() {
  local val
  val="$(printenv "$1" 2>/dev/null || true)"
  if [[ -z "$val" && -f "$ROOT/.env" ]]; then
    val="$(sed -n "s/^[[:space:]]*$1=//p" "$ROOT/.env" | tail -n1)"
  fi
  printf '%s' "$val"
}

# Mirrors the hosts' local_store_requested(): the in-process shop needs no :8080.
local_store_on() {
  case "$(env_val SHOPWARE_LOCAL_STORE)" in "" | 0 | false | False) return 1 ;; *) return 0 ;; esac
}

shopware_url() {
  local u; u="$(env_val SHOPWARE_ADMIN_URL)"; [[ -z "$u" ]] && u="$(env_val SHOPWARE_URL)"
  [[ -z "$u" ]] && u="http://127.0.0.1:8080"
  printf '%s' "${u%/}"
}

# 000 = nothing answered; any HTTP status (Shopware returns 401 unauthenticated) means it is up.
shopware_reachable() {
  [[ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$(shopware_url)/api/_info/version" 2>/dev/null)" != "000" ]]
}

start_one() {
  local name="$1" cmd="$2"
  # setsid: each service gets its own process group, so stop can take down npm -> next -> node.
  (cd "$ROOT" && exec setsid bash -c "exec $cmd" >"$LOG_DIR/$name.log" 2>&1 </dev/null) &
  echo $! >"$RUN_DIR/$name.pid"
}

stop_one() {
  local name="$1" port="$2" pid pids
  pid="$(read_pid "$name")"
  if pid_alive "$pid"; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  fi
  pids="$(port_pids "$port")"
  [[ -n "$pids" ]] && kill -TERM $pids 2>/dev/null

  local waited=0
  while (pid_alive "$pid" || [[ -n "$(port_pids "$port")" ]]) && ((waited < STOP_TIMEOUT)); do
    sleep 1
    ((waited++))
  done
  if pid_alive "$pid" || [[ -n "$(port_pids "$port")" ]]; then
    kill -KILL -- "-$pid" 2>/dev/null
    pids="$(port_pids "$port")"
    [[ -n "$pids" ]] && kill -KILL $pids 2>/dev/null
  fi
  rm -f "$RUN_DIR/$name.pid"
}

cmd_stop() {
  local svc
  for svc in "${SERVICES[@]}"; do
    stop_one "$(field "$svc" 1)" "$(field "$svc" 2)" &
  done
  wait
  echo "stopped: all four services"
}

cmd_start() {
  mkdir -p "$LOG_DIR"
  local svc name port busy=0
  for svc in "${SERVICES[@]}"; do
    name="$(field "$svc" 1)"
    port="$(field "$svc" 2)"
    if [[ -n "$(port_pids "$port")" ]]; then
      echo "port $port ($name) is already in use" >&2
      busy=1
    fi
  done
  if ((busy)); then
    echo "refusing to start; run ./dev.sh restart or ./dev.sh stop first" >&2
    return 1
  fi

  if ! local_store_on && [[ "${SKIP_SHOPWARE_CHECK:-0}" != "1" ]] && ! shopware_reachable; then
    echo "Shopware is not reachable at $(shopware_url) — the hosts would start but every" >&2
    echo "catalog and order call fails (they show 'up' with 0 products). Start it, then retry:" >&2
    echo "    docker start commerce-agents-shopware   # or: docker compose -f docker/compose.yaml up -d" >&2
    echo "(SHOPWARE_LOCAL_STORE=1 uses the in-process shop and needs no container;" >&2
    echo " SKIP_SHOPWARE_CHECK=1 bypasses this guard.)" >&2
    return 1
  fi

  for svc in "${SERVICES[@]}"; do
    start_one "$(field "$svc" 1)" "$(field "$svc" 4)"
  done

  local deadline=$((SECONDS + START_TIMEOUT)) pending failed=""
  while :; do
    pending=0
    for svc in "${SERVICES[@]}"; do
      name="$(field "$svc" 1)"
      if ! pid_alive "$(read_pid "$name")"; then
        failed="$name exited during startup"
        break 2
      fi
      healthy "$(field "$svc" 3)" || pending=1
    done
    ((pending == 0)) && break
    if ((SECONDS >= deadline)); then
      failed="timed out after ${START_TIMEOUT}s"
      break
    fi
    sleep 2
  done

  if [[ -n "$failed" ]]; then
    echo "start failed: $failed; stopping everything" >&2
    for svc in "${SERVICES[@]}"; do
      name="$(field "$svc" 1)"
      pid_alive "$(read_pid "$name")" && healthy "$(field "$svc" 3)" && continue
      echo "--- $name (last 15 lines of .run/logs/$name.log)" >&2
      tail -n 15 "$LOG_DIR/$name.log" >&2
    done
    cmd_stop >/dev/null
    return 1
  fi
  cmd_status
}

cmd_status() {
  local svc name port pid state
  printf '%-16s %-6s %-8s %s\n' SERVICE PORT PID STATE
  for svc in "${SERVICES[@]}"; do
    name="$(field "$svc" 1)"
    port="$(field "$svc" 2)"
    pid="$(read_pid "$name")"
    pid_alive "$pid" || pid="-"
    if healthy "$(field "$svc" 3)"; then
      state="up"
    elif [[ -n "$(port_pids "$port")" ]]; then
      state="port in use, not healthy"
    else
      state="down"
    fi
    printf '%-16s %-6s %-8s %s\n' "$name" "$port" "$pid" "$state"
  done
}

cmd_logs() {
  if [[ -n "${1:-}" ]]; then
    tail -n 50 -f "$LOG_DIR/$1.log"
  else
    tail -n 20 -f "$LOG_DIR"/*.log
  fi
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop && cmd_start ;;
  status) cmd_status ;;
  logs) cmd_logs "${2:-}" ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs [storefront-api|merchant-api|storefront-web|merchant-web]}" >&2
    exit 2
    ;;
esac
