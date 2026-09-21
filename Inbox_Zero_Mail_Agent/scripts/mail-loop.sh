#!/usr/bin/env bash
#
# 一条命令拉起本地邮件闭环(plan.md 的实现)。
#
#   bash scripts/mail-loop.sh up      起全套(幂等,可重复跑)
#   bash scripts/mail-loop.sh down    停全套
#   bash scripts/mail-loop.sh status  看状态
#   bash scripts/mail-loop.sh logs    跟踪日志
#
# 背景与坑见 local-mail-loop-resume.md。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/apps/web"
STATE="$APP/.tmp/mail-backbone/state.json"
PIDS="$APP/.tmp/mail-loop.pids"
LOG_DIR="$APP/.tmp/mail-loop-logs"

# 外部客户邮箱。首次 up 时写进 .tmp/customer-mail/.customer-email,
# 之后改那个文件即可(改默认值也能生效)。
DEFAULT_CUSTOMER_EMAIL="${CUSTOMER_EMAIL:-ruoyu@gmail.com}"

# 标签由 sync-labels.sh 负责(按名重建 + 回写 DB 映射),这里不再维护清单。

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

  say "启动容器"
  # 不含 cron:它只会 curl http://web:3000,而应用是在宿主机跑的,
  # 容器里解析不到 web 主机名,起来也只会刷失败日志。
  docker start inbox-zero-services-db-1 inbox-zero-services-redis-1 \
    inbox-zero-services-serverless-redis-http-1 inbox-zero-google-emulator \
    >/dev/null 2>&1 || true
  wait_for "模拟器" "http://127.0.0.1:4002/.well-known/openid-configuration" 60

  # TRANSPORT_TOKEN 不在 .env 里 —— 它是骨干和客户服务器之间的共享密钥,
  # 由这个脚本生成并持久化,这样重复跑不会把两边的 token 弄成不一样。
  local transport_file="$APP/.tmp/customer-mail/.transport-token"
  local transport
  transport="$(cat "$transport_file" 2>/dev/null || true)"
  [ -n "$transport" ] || transport="local-transport-$(date +%s)"
  echo "$transport" >"$transport_file"

  # 外部客户邮箱。直接改这个文件就能换,改完重跑 up 即可。
  local customer_file="$APP/.tmp/customer-mail/.customer-email"
  local customer
  customer="$(cat "$customer_file" 2>/dev/null || echo "$DEFAULT_CUSTOMER_EMAIL")"
  export CUSTOMER_EMAIL="$customer"

  local webhook_token managed
  webhook_token="$(require_env GOOGLE_PUBSUB_VERIFICATION_TOKEN)"
  managed="$(grep -A 2 -E '^[[:space:]]*users:' "$ROOT/emulate.local.config.yaml" | grep -oE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+' | head -1 || true)"
  [ -n "$managed" ] || die "从 emulate.local.config.yaml 读不到受管邮箱"

  # 游标必须跟着模拟器的 history 走。模拟器重启后 historyId 回到新基准,
  # 而骨干和应用还停在旧值,两边都会把一切过滤掉。
  #
  # 这一步会重启模拟器,所以必须排在标签对齐之前 —— 重启会清空用户标签,
  # 反过来做会让对齐结果当场作废。
  if [ -f "$STATE" ]; then
    local emu_history app_history
    emu_history="$(curl -s "http://127.0.0.1:4002/gmail/v1/users/me/profile" -H "Authorization: Bearer $transport" |
      python3 -c 'import json,sys; print(json.load(sys.stdin).get("historyId","0"))' 2>/dev/null || echo 0)"
    app_history="$(docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero -tAc \
      "select coalesce(\"lastSyncedHistoryId\",'') from \"EmailAccount\" limit 1;" 2>/dev/null | tr -d '[:space:]')"
    # 新基准是时间戳×1000(16 位),旧基准是 2e19 量级;位数不同就一定错位了。
    if [ -n "$app_history" ] && [ "${#emu_history}" -lt "${#app_history}" ]; then
      warn "游标错位(模拟器 $emu_history vs 应用 $app_history),重置"
      docker exec inbox-zero-services-db-1 psql -U postgres -d inboxzero \
        -c 'update "EmailAccount" set "lastSyncedHistoryId" = null;' >/dev/null
      docker restart inbox-zero-google-emulator >/dev/null
      wait_for "模拟器(重启复位 history)" "http://127.0.0.1:4002/.well-known/openid-configuration" 30
      rm -f "$STATE"
    fi
  fi

  # 标签:模拟器重启会清空用户标签,应用 DB 里存的 ID 随之失效 ——
  # sync-labels 负责按名重建并把 DB 的引用对齐回去。
  say "对齐标签"
  bash "$ROOT/scripts/sync-labels.sh" 2>&1 | tail -1

  if listening "http://127.0.0.1:4005/.well-known/openid-configuration"; then
    ok "骨干已在运行"
  else
    say "启动骨干"
    launch backbone "npx tsx scripts/run-mail-backbone.ts" \
      MANAGED_EMAIL="$managed" TRANSPORT_TOKEN="$transport" \
      EMULATOR_AUTH_TOKEN="loop-$(date +%s)" \
      GOOGLE_WEBHOOK_URL="http://localhost:3000/api/google/webhook" \
      GOOGLE_PUBSUB_VERIFICATION_TOKEN="$webhook_token" \
      CUSTOMER_RECEIVE_URL="http://127.0.0.1:4100/receive" \
      EMULATOR_BASE_URL="http://127.0.0.1:4002"
    wait_for "骨干" "http://127.0.0.1:4005/.well-known/openid-configuration" 40
  fi

  # 模拟器是纯内存的,重启会清空 OAuth token 和标签。-1 表示现在没有邮件,
  # 而应用有游标 —— 说明模拟器被重置过,需要重新登录。
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

  if listening "http://127.0.0.1:4100/"; then
    ok "客户服务器已在运行"
  else
    say "启动客户服务器"
    launch customer "npx tsx scripts/customer-mail-server.ts serve --port 4100 --outbox .tmp/customer-mail --token $transport"
    wait_for "客户服务器" "http://127.0.0.1:4100/" 30
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

  echo
  ok "邮件闭环就绪"
  if [ "$NEEDS_LOGIN" = "1" ]; then
    warn "但需要先重新登录(模拟器被重启过,OAuth token 已失效)"
  fi
  cat <<EOF

  下一步(需要你在浏览器操作)
    1. 打开 http://localhost:3000,用受管邮箱 $managed 登录
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
  say "停止宿主机进程"
  if [ -f "$PIDS" ]; then
    while read -r name pid; do
      kill "$pid" 2>/dev/null && ok "$name (pid $pid)" || true
    done <"$PIDS"
    rm -f "$PIDS"
  fi
  # 端口兜底:PID 文件丢了、或进程换了 pid 时还能收干净。
  local port
  for port in 4005 4100 3000; do kill_port "$port"; done
  sleep 1

  say "停止容器"
  # cron 不在 up 的启动列表里(它只会去 curl http://web:3000,而应用是
  # 在宿主机上跑的,容器内解析不到 web 主机名),但如果是 compose 起过的
  # 也要一起停干净。
  docker stop inbox-zero-google-emulator \
    inbox-zero-services-serverless-redis-http-1 inbox-zero-services-redis-1 \
    inbox-zero-services-db-1 inbox-zero-services-cron-1 \
    >/dev/null 2>&1 || true
  ok "已停止(容器与数据卷保留)"
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
  tail -f "$LOG_DIR"/*.log
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
