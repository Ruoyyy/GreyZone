#!/bin/sh
# 一次性初始化：成品角色 + 成品仓库（每个仓库仅一个初始提交）。
# 幂等：已存在的对象自动跳过。由 compose 的 gitea-init 服务调用。
set -eu

GITEA_URL="${GITEA_INSTANCE_URL:-http://gitea:3000}"
ADMIN="${GITEA_ADMIN_USER:-letta-admin}"
ADMIN_PW="${GITEA_ADMIN_PASSWORD:-letta-admin}"
RUYOU_PW="${RUYOU_PASSWORD:-ruoyu}"
GIT_IDENTITY_NAME="letta-admin"
GIT_IDENTITY_EMAIL="letta-admin@gitea.com"

say() { echo "[gitea-init] $*"; }

# ---------- 1. 等 gitea 就绪 ----------
i=0
until curl -fsS --max-time 5 "$GITEA_URL/api/healthz" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 90 ] && { echo "[gitea-init] FATAL: gitea 未就绪"; exit 1; }
  sleep 2
done
say "gitea 就绪"

# ---------- 2. 成品角色（gitea CLI，与主服务同一 /data） ----------
user_exists() { gitea admin user list 2>/dev/null | grep -Eq "^[0-9]+[[:space:]]+$1[[:space:]]"; }

ensure_user() { # 用户名 邮箱 密码 是否管理员
  if user_exists "$1"; then
    say "用户 $1 已存在，跳过"
  else
    extra=""
    [ "$4" = "admin" ] && extra="--admin"
    # shellcheck disable=SC2086
    gitea admin user create --username "$1" --email "$2" --password "$3" \
      --must-change-password=false $extra >/dev/null
    say "用户 $1 已创建$([ "$4" = "admin" ] && echo '（管理员）')"
  fi
}
ensure_user "$ADMIN" "$ADMIN@gitea.com" "$ADMIN_PW" admin
ensure_user ruoyu "ruoyu@gitea.com" "$RUYOU_PW" normal

# ---------- 3. runner 注册 token（写入共享目录供 runner 读取） ----------
tok=""
if [ -s /shared/runner-token ]; then
  tok=$(cat /shared/runner-token)
fi
case "$tok" in
  [A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9]*)
    say "runner token 已存在，跳过" ;;
  *)
    gitea actions generate-runner-token > /shared/runner-token
    tok=$(cat /shared/runner-token)
    case "$tok" in
      [A-Za-z0-9]*) say "runner token 已生成" ;;
      *) echo "[gitea-init] FATAL: token 生成异常: $tok"; exit 1 ;;
    esac ;;
esac

# ---------- 4. 成品仓库（API 建仓 + ruoyu 只读协作） ----------
api() { # 方法 路径 [JSON]
  m=$1 p=$2 b=${3:-}
  if [ -n "$b" ]; then
    curl -s -o /dev/null -w '%{http_code}' -u "$ADMIN:$ADMIN_PW" \
      -X "$m" -H 'Content-Type: application/json' -d "$b" "$GITEA_URL/api/v1$p"
  else
    curl -s -o /dev/null -w '%{http_code}' -u "$ADMIN:$ADMIN_PW" \
      -X "$m" "$GITEA_URL/api/v1$p"
  fi
}

ensure_repo() { # 仓库名
  code=$(api GET "/repos/$ADMIN/$1")
  if [ "$code" = "200" ]; then
    say "仓库 $1 已存在，跳过"
    REPO_FRESH=0
  else
    code=$(api POST /user/repos "{\"name\":\"$1\",\"private\":true,\"auto_init\":false}")
    [ "$code" = "201" ] || { say "FATAL: 建仓 $1 失败(HTTP $code)"; exit 1; }
    say "仓库 $1 已创建（私有）"
    REPO_FRESH=1
  fi
}
REPO_FRESH=1
ensure_repo letta-code-action
ACTION_FRESH=$REPO_FRESH
ensure_repo AgentThreatDataset
TARGET_FRESH=$REPO_FRESH

# ruoyu：action 仓库只读（触发任务时 workflow 需能拉取私有 action），
# 目标仓库写权限（完整合作者，可直接推送与维护）
code=$(api PUT "/repos/$ADMIN/letta-code-action/collaborators/ruoyu" '{"permission":"read"}')
[ "$code" = "204" ] && say "ruoyu 已获得 letta-code-action 只读权限" \
  || say "ruoyu action 协作授权返回 HTTP $code（可能已授权）"
code=$(api PUT "/repos/$ADMIN/AgentThreatDataset/collaborators/ruoyu" '{"permission":"write"}')
[ "$code" = "204" ] && say "ruoyu 已获得 AgentThreatDataset 写权限（合作者）" \
  || say "ruoyu 目标仓库协作授权返回 HTTP $code（可能已授权）"

# ---------- 5. 推送种子（每个仓库一个初始提交，作者 letta-admin） ----------
push_seed() { # 种子目录 仓库名 额外渲染命令...
  src=$1 repo=$2; shift 2
  tmp="/tmp/repo-$repo"
  rm -rf "$tmp"
  cp -r "/repo/$src" "$tmp"
  cd "$tmp"
  # 占位符渲染等（由调用方给出）
  for render in "$@"; do
    sh -c "$render"
  done
  git init -q -b main .
  git config user.name "$GIT_IDENTITY_NAME"
  git config user.email "$GIT_IDENTITY_EMAIL"
  git add -A
  git commit -q -m "Initial commit"
  git push -q -f "${GITEA_URL/http:\/\//http:\/\/$ADMIN:$ADMIN_PW@}/$ADMIN/$repo.git" main
  sha=$(git rev-parse HEAD)
  echo "$sha"
}

if [ "$ACTION_FRESH" = "1" ]; then
  sha=$(push_seed action-repo letta-code-action)
  say "letta-code-action 已推送（单提交 $sha）"
else
  # 已有内容：读取现有头提交，供 workflow 渲染对齐
  sha=$(curl -s -u "$ADMIN:$ADMIN_PW" \
    "$GITEA_URL/api/v1/repos/$ADMIN/letta-code-action/commits?limit=1" |
    grep -o '"sha":"[0-9a-f]*"' | head -1 | cut -d'"' -f4)
  say "letta-code-action 已有内容，跳过推送（头提交 $sha）"
fi

if [ "$TARGET_FRESH" = "1" ]; then
  sha2=$(push_seed target-repo AgentThreatDataset \
    "sed -i 's/__ACTION_SHA__/$sha/' .gitea/workflows/letta.yml")
  say "AgentThreatDataset 已推送（单提交 $sha2，action 固定于 $sha）"
else
  say "AgentThreatDataset 已有内容，跳过推送"
fi

# ---------- 6. 私有 action 共享：协作所有者 ----------
# job 令牌默认读不了其他私有仓库；把 ADMIN 设为 action 仓库的
# "协作所有者"，其名下仓库即可使用该私有 action（网页表单，无公开 API）。
jar=/tmp/gitea-web.cookie
curl -s -c "$jar" -o /dev/null "$GITEA_URL/user/login"
curl -s -b "$jar" -c "$jar" -o /dev/null \
  -d "user_name=$ADMIN&password=$ADMIN_PW" "$GITEA_URL/user/login"
AG_URL="$GITEA_URL/$ADMIN/letta-code-action/settings/actions/general"
if curl -s -b "$jar" "$AG_URL" | grep -q "collaborative_owner/delete"; then
  say "协作所有者已配置，跳过"
else
  curl -s -b "$jar" -o /dev/null \
    -d "collaborative_owner=$ADMIN" "$AG_URL/collaborative_owner/add"
  if curl -s -b "$jar" "$AG_URL" | grep -q "collaborative_owner/delete"; then
    say "协作所有者 $ADMIN 已添加（其名下仓库可使用私有 action）"
  else
    echo "[gitea-init] FATAL: 协作所有者添加失败"; exit 1
  fi
fi

say "初始化完成"
