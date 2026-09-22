#!/usr/bin/env bash
#
# 一条命令拉起本地邮件闭环(plan.md 的实现)。
#
#   bash scripts/mail-loop.sh up      起全套(幂等,可重复跑)
#   bash scripts/mail-loop.sh down    停全套(容器与数据卷保留)
#   bash scripts/mail-loop.sh status  看状态
#   bash scripts/mail-loop.sh logs    跟踪日志
#   bash scripts/mail-loop.sh sync-labels  重建并回写标签
#
# 三块组成:
#   基础设施(db/redis/redis-http)     ← 主 docker-compose.yml
#   邮件闭环(模拟器/骨干/客户服务器) ← docker-compose.mail-loop.yml
#   应用(next dev)                    ← 宿主机进程,由本脚本 launch
#
# 首次运行前先: bash scripts/mail-loop-init.sh(生成 compose 挂载的依赖物)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/apps/web"
PIDS="$APP/.tmp/mail-loop.pids"
LOG_DIR="$APP/.tmp/mail-loop-logs"

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
# 记下子 shell 的 pid。npx 会 exec 成真正的进程,所以这个 pid 通常就是
# 真正的服务进程;down 里还有按模式匹配的兜底。
launch() {
  local name="$1" cmd="$2"
  shift 2
  local pid
  (
    cd "$APP"
    # 裸 export 会打印全部环境变量,所以这里必须判空。
    [ "$#" -gt 0 ] && export "$@"
    # 保留上一轮日志,否则排查"上次为什么失败"时证据已经没了。
    if [ -f "$LOG_DIR/$name.log" ]; then
      mv -f "$LOG_DIR/$name.log" "$LOG_DIR/$name.prev.log"
    fi
    nohup $cmd >"$LOG_DIR/$name.log" 2>&1 &
    echo $!
  ) >"$LOG_DIR/$name.pid"
  ok "$name (pid $(cat "$LOG_DIR/$name.pid"))"

  # 同一服务重新启动时替换旧条目。注意用 if 而不是 `[ -f ] && ...`:
  # 后者在文件不存在时返回非 0,会被 set -e 当成错误中断脚本。
  if [ -f "$PIDS" ]; then
    grep -v "^$name " "$PIDS" >"$PIDS.tmp" || true
    mv -f "$PIDS.tmp" "$PIDS"
  fi
  echo "$name $(cat "$LOG_DIR/$name.pid")" >>"$PIDS"
}

require_env() {
  local v
  v="$(env_value "$1")"
  [ -n "$v" ] || die "apps/web/.env 缺少 $1"
  printf '%s' "$v"
}

# wait_for <desc> <url> <tries> [extra curl args...]
wait_for() {
  local desc="$1" url="$2" tries="${3:-40}" code
  shift 3 2>/dev/null || shift 2
  for _ in $(seq 1 "$tries"); do
    code="$(curl -s "$@" -o /dev/null -w '%{http_code}' "$url" --max-time 5 || true)"
    # 000 = 连不上;其余(含 5xx)说明端口有人在听,只是上游可能还没好。
    if [ -n "$code" ] && [ "$code" != "000" ]; then
      ok "$desc 就绪"
      return 0
    fi
    sleep 1
  done
  die "$desc 在 ${tries}s 内没起来($url)"
}

# wait_http_200 <desc> <url> <tries> [extra curl args...]
wait_http_200() {
  local desc="$1" url="$2" tries="${3:-40}" code
  shift 3 2>/dev/null || shift 2
  for _ in $(seq 1 "$tries"); do
    code="$(curl -s "$@" -o /dev/null -w '%{http_code}' "$url" --max-time 5 || true)"
    if [ "$code" = "200" ]; then
      ok "$desc 就绪"
      return 0
    fi
    sleep 1
  done
  die "$desc 在 ${tries}s 内没返回 200($url)"
}

up() {
  local NEEDS_LOGIN=0
  mkdir -p "$LOG_DIR" "$APP/.tmp/customer-mail" "$APP/.tmp/mail-backbone"

  say "检查依赖物(bundle / patched emulate / transport token)"
  if [ ! -f "$APP/.tmp/mail-loop-bundles/run-mail-backbone.js" ] ||
    [ ! -f "$APP/.tmp/mail-loop-bundles/customer-mail-server.js" ] ||
    [ ! -f "$ROOT/.tmp/emulate-patched/dist/index.js" ]; then
    die "缺少运行依赖物,先跑: bash scripts/mail-loop-init.sh"
  fi

  local transport
  transport="$(cat "$APP/.tmp/customer-mail/.transport-token" 2>/dev/null || true)"
  [ -n "$transport" ] || die "缺少 transport token,先跑: bash scripts/mail-loop-init.sh"

  say "启动基础设施(db/redis/cron)"
  docker compose up -d db redis serverless-redis-http >/dev/null 2>&1 || true
  docker compose up -d --no-deps cron >/dev/null 2>&1 || true

  say "启动邮件闭环(模拟器/骨干/客户服务器)"
  TRANSPORT_TOKEN="$transport" \
    GOOGLE_PUBSUB_VERIFICATION_TOKEN="$(require_env GOOGLE_PUBSUB_VERIFICATION_TOKEN)" \
    docker compose --profile mail-loop -f docker-compose.mail-loop.yml up -d >/dev/null 2>&1 || true

  wait_for "模拟器" "http://127.0.0.1:4002/.well-known/openid-configuration" 60
  wait_for "骨干" "http://127.0.0.1:4005/.well-known/openid-configuration" 40

  # 模拟器是纯内存的,重启会清空 OAuth token 和标签。
  local emu_profile app_history_now
  emu_profile="$(curl -s "http://127.0.0.1:4002/gmail/v1/users/me/profile" -H "Authorization: Bearer $transport" 2>/dev/null |
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("messagesTotal",-1), d.get("historyId","0"))' 2>/dev/null || echo '-1 0')"
  app_history_now="$(docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero -tAc \
    "select coalesce(\"lastSyncedHistoryId\",'') from \"EmailAccount\" limit 1;" 2>/dev/null | tr -d '[:space:]' || true)"
  if [ "${emu_profile%% *}" = "0" ] && [ -n "$app_history_now" ]; then
    warn "模拟器是空的但应用有游标 —— 模拟器被重启过"
    warn "OAuth token 和标签都已失效,需要重新登录(见下方步骤)"
    NEEDS_LOGIN=1
  fi

  if listening "http://localhost:3000/login"; then
    ok "应用已在运行"
  else
    say "启动应用(源码方式,含 LLM 修复)"
    local pw rt
    pw="$(require_env POSTGRES_PASSWORD)"
    rt="$(require_env UPSTASH_REDIS_TOKEN)"
    launch app "npx cross-env NODE_OPTIONS=--max-old-space-size=3072 next dev --turbopack" \
      DATABASE_URL="postgresql://postgres:${pw}@127.0.0.1:5433/inboxzero?schema=public" \
      DIRECT_URL="postgresql://postgres:${pw}@127.0.0.1:5433/inboxzero?schema=public" \
      UPSTASH_REDIS_URL="http://127.0.0.1:8079" UPSTASH_REDIS_TOKEN="$rt"
    wait_for "应用" "http://localhost:3000/login" 120
  fi

  # 自动重建 watch:骨干重启会丢内存态订阅(虽然现在订阅也持久化了,
  # 但应用侧 watch 可能已过期),重建成本低、幂等。
  say "重建 watch 订阅"
  local cron_secret
  cron_secret="$(env_value CRON_SECRET)"
  if [ -n "$cron_secret" ]; then
    curl -s -X GET "http://localhost:3000/api/watch/all" \
      -H "Authorization: Bearer $cron_secret" --max-time 60 \
      | head -c 200 >/dev/null 2>&1 || true
    ok "watch 请求已发送(应用若未登录会静默失败,登录后再跑一次)"
  else
    warn "缺少 CRON_SECRET,跳过 watch 重建"
  fi

  echo
  ok "邮件闭环就绪"
  if [ "$NEEDS_LOGIN" = "1" ]; then
    warn "但需要先重新登录(模拟器被重启过,OAuth token 已失效)"
  fi
  cat <<EOF

  下一步(需要你在浏览器操作)
    1. 打开 http://localhost:3000,用受管邮箱登录
    2. 登录后建 watch:
       curl -s "http://localhost:3000/api/watch/all" \\
         -H "Authorization: Bearer $(env_value CRON_SECRET)"

  可用命令
    bash scripts/mail-loop.sh status
    bash scripts/mail-loop.sh logs
    bash scripts/mail-loop.sh down
EOF
}

# 按监听端口杀进程。不用 pkill -f:模式匹配会命中调用方自己的命令行
# (任何 grep/pkill 参数里含该字符串的进程都会被一起杀掉)。
kill_port() {
  local port="$1" pids
  pids="$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
  local p
  for p in $pids; do
    kill "$p" 2>/dev/null || true
  done
}

down() {
  say "停止应用(宿主机进程)"
  if [ -f "$PIDS" ]; then
    while read -r name pid; do
      kill "$pid" 2>/dev/null && ok "$name (pid $pid)" || true
    done <"$PIDS"
    rm -f "$PIDS"
  fi
  # 端口兜底:PID 文件丢了、或进程换了 pid 时还能收干净。
  for port in 3000; do kill_port "$port"; done
  sleep 1

  say "停止邮件闭环(mail-loop compose,容器保留)"
  docker compose --profile mail-loop -f docker-compose.mail-loop.yml stop >/dev/null 2>&1 || true

  say "停止基础设施(主 compose,容器保留)"
  docker compose stop db redis serverless-redis-http cron >/dev/null 2>&1 || true

  ok "已停止(全部容器与数据卷保留)"
}

status() {
  echo "容器:"
  docker ps -a --filter "name=inbox-zero" --format '  {{.Names}}\t{{.Status}}' 2>/dev/null
  echo "端点:"

  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:4002/.well-known/openid-configuration" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "模拟器" "$code"

  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:4005/.well-known/openid-configuration" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "骨干" "$code"

  # 客户服务器只接受 POST /receive,GET / 返回 404 是正常的。
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:4100/receive" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "客户" "$code (400/401 为正常:只收 POST /receive)"

  code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:3000/login" --max-time 5 || echo ---)"
  printf '  %-8s %s\n' "应用" "$code"

  echo "游标:"
  local emu app
  emu="$(curl -s "http://127.0.0.1:4002/gmail/v1/users/me/profile" -H "Authorization: Bearer $(cat "$APP/.tmp/customer-mail/.transport-token" 2>/dev/null || echo x)" 2>/dev/null |
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("historyId","?"))' 2>/dev/null || echo '?')"
  app="$(docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero -tAc \
    "select coalesce(\"lastSyncedHistoryId\",'(空)') from \"EmailAccount\" limit 1;" 2>/dev/null | tr -d '[:space:]')"
  printf '  %-8s %s\n' "模拟器" "$emu"
  printf '  %-8s %s\n' "应用" "${app:-?}"
  if [ -n "$emu" ] && [ -n "$app" ] && [ "$app" != "(空)" ] && [ "${#emu}" -lt "${#app}" ]; then
    warn "两侧游标位数不同,可能错位 —— 重跑 up 会自动重置"
  fi

  echo "订阅:"
  if grep -q "watch captured" "$LOG_DIR/backbone.log" 2>/dev/null &&
    ! grep -q "watch stopped" "$LOG_DIR/backbone.log" 2>/dev/null; then
    echo "  watch 已建立"
  else
    warn "watch 未建立 —— 通知不会发出去。重新登录后跑:"
    echo "    curl -s \"http://localhost:3000/api/watch/all\" -H \"Authorization: Bearer $(env_value CRON_SECRET)\""
  fi
}

logs() {
  echo "== 应用日志($LOG_DIR/app.log)=="
  tail -n 30 "$LOG_DIR/app.log" 2>/dev/null || echo "  (无)"
  echo
  echo "== 容器日志 =="
  docker compose --profile mail-loop -f docker-compose.mail-loop.yml logs --tail 20 2>/dev/null
  echo
  echo "跟踪: bash scripts/mail-loop.sh logs 只打印快照;实时跟踪用:"
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
  echo "用法: bash scripts/mail-loop.sh {up|down|status|logs|sync-labels}" >&2
  exit 1
  ;;
esac
