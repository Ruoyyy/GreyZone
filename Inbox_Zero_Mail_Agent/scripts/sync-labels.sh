#!/usr/bin/env bash
#
# 把应用 DB 里引用的标签 ID 对齐到模拟器当前实际存在的标签。
#
#   bash scripts/mail-loop.sh sync-labels
#
# 为什么需要:模拟器是内存态的,重启后标签会被重新生成、ID 全部变样,
# 而应用 DB 里存的还是旧 ID —— 规则执行报 "Invalid label IDs",
# ExecutedRule 变 ERROR。
#
# 幂等:按名字在模拟器里取(缺了就建),再回写 DB 里存了标签 ID 的地方。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/apps/web"
DB=inbox-zero-services-db-1
TRANSPORT="$(cat "$APP/.tmp/customer-mail/.transport-token" 2>/dev/null || echo x)"

say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
psql_q() { docker exec "$DB" psql -U postgres -d inboxzero -tAc "$1" 2>/dev/null; }

# 模拟器是内存态的,重启后用户标签会全部消失 —— "一个都没有"是正常状态,
# 不是错误,下面会按需重建。
existing="$(curl -s "http://127.0.0.1:4002/gmail/v1/users/me/labels" \
  -H "Authorization: Bearer $TRANSPORT" |
  python3 -c 'import json,sys
for l in json.load(sys.stdin).get("labels",[]):
    if l.get("type")=="user": print("%s|%s" % (l["name"], l["id"]))' 2>/dev/null || true)"

if ! curl -s -o /dev/null "http://127.0.0.1:4002/.well-known/openid-configuration" --max-time 5; then
  warn "模拟器没起来"
  exit 1
fi

# 只对齐规则动作真正引用的标签 —— 应用只认这些。
names="$(psql_q "select distinct label from \"Action\" where label is not null order by label;")"
if [ -z "$names" ]; then
  warn "Action 表里没有引用标签"
  exit 0
fi

changed=0
while IFS= read -r name; do
  [ -n "$name" ] || continue

  # 注意结尾的 `|| true`:existing 可能不含这个名字,grep 会返回 1,
  # 而 set -e + pipefail 会让整条管道把脚本直接杀掉。
  id="$(grep -F "${name}|" <<<"$existing" | head -1 | cut -d'|' -f2 || true)"
  if [ -z "$id" ]; then
    say "模拟器缺少「$name」,创建"
    id="$(curl -s -X POST "http://127.0.0.1:4002/gmail/v1/users/me/labels" \
      -H "Authorization: Bearer $TRANSPORT" -H "Content-Type: application/json" \
      -d "{\"name\":\"$name\"}" |
      python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
  fi
  if [ -z "$id" ]; then
    warn "「$name」解析失败,跳过"
    continue
  fi

  stored="$(psql_q "select \"labelId\" from \"Action\" where label='$name' limit 1;")"
  if [ "$stored" = "$id" ]; then
    continue
  fi

  psql_q "update \"Action\" set \"labelId\"='$id' where label='$name';" >/dev/null
  # MailSplitFilter 存的是同一个标签 ID,漏了它规则会在另一条路径上再炸一次。
  psql_q "update \"MailSplitFilter\" set value='$id' where value='$stored';" >/dev/null
  printf '  %-18s %s -> %s\n' "$name" "$stored" "$id"
  changed=$((changed + 1))
done <<<"$names"

if [ "$changed" -eq 0 ]; then
  say "标签已对齐,无需修改"
else
  say "已对齐 $changed 个标签"
fi
