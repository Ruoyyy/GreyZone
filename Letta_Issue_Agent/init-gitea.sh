#!/bin/sh
# One-time initialization: ready-made roles + seed repositories (a single initial commit per repo).
# Idempotent: existing objects are skipped automatically. Invoked by the gitea-init compose service.
set -eu

# Bind directories first created by Docker, or copied-in files, may be owned by root.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /shared/mcp-tokens
  chown -h 1000:1000 /shared /shared/mcp-tokens
  for token_file in /shared/runner-token /shared/mcp-tokens/mcp-l1.token \
    /shared/mcp-tokens/mcp-l2.token /shared/mcp-tokens/mcp-l3.token; do
    [ ! -f "$token_file" ] || chown -h 1000:1000 "$token_file"
  done
  exec su-exec 1000:1000 sh "$0" "$@"
fi

GITEA_URL="${GITEA_INSTANCE_URL:-http://gitea:3000}"
ADMIN="${GITEA_ADMIN_USER:-letta-admin}"
ADMIN_PW="${GITEA_ADMIN_PASSWORD:-letta-admin}"
ALICE_PW="${ALICE_PASSWORD:-alice}"
GIT_IDENTITY_NAME="letta-admin"
GIT_IDENTITY_EMAIL="letta-admin@gitea.com"

say() { echo "[gitea-init] $*"; }

# ---------- 1. Wait for gitea to be ready ----------
i=0
until curl -fsS --max-time 5 "$GITEA_URL/api/healthz" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 90 ] && { echo "[gitea-init] FATAL: gitea not ready"; exit 1; }
  sleep 2
done
say "gitea ready"

# ---------- 2. Ready-made roles (gitea CLI, same /data as the main service) ----------
user_exists() { gitea admin user list 2>/dev/null | grep -Eq "^[0-9]+[[:space:]]+$1[[:space:]]"; }

ensure_user() { # username email password admin?
  if user_exists "$1"; then
    say "user $1 already exists, skipping"
  else
    extra=""
    [ "$4" = "admin" ] && extra="--admin"
    # shellcheck disable=SC2086
    gitea admin user create --username "$1" --email "$2" --password "$3" \
      --must-change-password=false $extra >/dev/null
    say "user $1 created$([ "$4" = "admin" ] && echo ' (admin)')"
  fi
}
ensure_user "$ADMIN" "$ADMIN@gitea.com" "$ADMIN_PW" admin
ensure_user alice "alice@gitea.com" "$ALICE_PW" normal

# ---------- 3. Fetch the runner token from the current Gitea; do not trust stale copied files ----------
tok=$(gitea actions generate-runner-token)
case "$tok" in
  ''|*[!A-Za-z0-9]*) say "FATAL: failed to get runner token"; exit 1 ;;
esac
printf '%s' "$tok" > /shared/runner-token
say "runner token aligned with the current Gitea"

# ---------- 4. Seed repositories (API creation + alice read-only collaboration) ----------
api() { # method path [JSON]
  m=$1 p=$2 b=${3:-}
  if [ -n "$b" ]; then
    curl -s -o /dev/null -w '%{http_code}' -u "$ADMIN:$ADMIN_PW" \
      -X "$m" -H 'Content-Type: application/json' -d "$b" "$GITEA_URL/api/v1$p"
  else
    curl -s -o /dev/null -w '%{http_code}' -u "$ADMIN:$ADMIN_PW" \
      -X "$m" "$GITEA_URL/api/v1$p"
  fi
}

ensure_repo() { # repo name
  code=$(api GET "/repos/$ADMIN/$1")
  if [ "$code" = "200" ]; then
    say "repo $1 already exists, skipping"
    REPO_FRESH=0
  else
    code=$(api POST /user/repos "{\"name\":\"$1\",\"private\":true,\"auto_init\":false}")
    [ "$code" = "201" ] || { say "FATAL: failed to create repo $1 (HTTP $code)"; exit 1; }
    say "repo $1 created (private)"
    REPO_FRESH=1
  fi
}
REPO_FRESH=1
ensure_repo letta-code-action
ACTION_FRESH=$REPO_FRESH
ensure_repo GreyZone
TARGET_FRESH=$REPO_FRESH

# alice: read-only on the action repo (workflows must be able to pull the private action when a job triggers),
# write access on the target repo (full collaborator, can push and maintain directly)
code=$(api PUT "/repos/$ADMIN/letta-code-action/collaborators/alice" '{"permission":"read"}')
[ "$code" = "204" ] && say "alice granted read access to letta-code-action" \
  || say "alice action collaboration grant returned HTTP $code (may already be granted)"
code=$(api PUT "/repos/$ADMIN/GreyZone/collaborators/alice" '{"permission":"write"}')
[ "$code" = "204" ] && say "alice granted write access to GreyZone (collaborator)" \
  || say "alice target-repo collaboration grant returned HTTP $code (may already be granted)"

# ---------- 5. Push seeds (one initial commit per repo, authored by letta-admin) ----------
push_seed() { # seed dir repo name extra render commands...
  src=$1 repo=$2; shift 2
  tmp="/tmp/repo-$repo"
  rm -rf "$tmp"
  cp -r "/repo/$src" "$tmp"
  cd "$tmp"
  # Placeholder rendering etc. (supplied by the caller)
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
  say "letta-code-action pushed (single commit $sha)"
else
  # Existing content: read the current head commit so workflow rendering stays aligned
  sha=$(curl -s -u "$ADMIN:$ADMIN_PW" \
    "$GITEA_URL/api/v1/repos/$ADMIN/letta-code-action/commits?limit=1" |
    grep -o '"sha":"[0-9a-f]*"' | head -1 | cut -d'"' -f4)
  say "letta-code-action already has content, skipping push (head commit $sha)"
fi

if [ "$TARGET_FRESH" = "1" ]; then
  sha2=$(push_seed target-repo GreyZone \
    "sed -i 's/__ACTION_SHA__/$sha/' .gitea/workflows/letta.yml")
  say "GreyZone pushed (single commit $sha2, action pinned to $sha)"
else
  say "GreyZone already has content, skipping push"
fi

# ---------- 6. Private action sharing: collaborative owner ----------
# The job token cannot read other private repos by default; making ADMIN the action repo's
# "collaborative owner" lets repos under that account use the private action (web form only, no public API).
jar=/tmp/gitea-web.cookie
curl -s -c "$jar" -o /dev/null "$GITEA_URL/user/login"
curl -s -b "$jar" -c "$jar" -o /dev/null \
  -d "user_name=$ADMIN&password=$ADMIN_PW" "$GITEA_URL/user/login"
AG_URL="$GITEA_URL/$ADMIN/letta-code-action/settings/actions/general"
if curl -s -b "$jar" "$AG_URL" | grep -q "collaborative_owner/delete"; then
  say "collaborative owner already configured, skipping"
else
  curl -s -b "$jar" -o /dev/null \
    -d "collaborative_owner=$ADMIN" "$AG_URL/collaborative_owner/add"
  if curl -s -b "$jar" "$AG_URL" | grep -q "collaborative_owner/delete"; then
    say "collaborative owner $ADMIN added (repos under this account can use the private action)"
  else
    echo "[gitea-init] FATAL: failed to add collaborative owner"; exit 1
  fi
fi

# ---------- 7. Workflow protection: normal code is pushable; workflows maintained only by admin via PR ----------
protect() { # rule name (URL-encoded) JSON
  path="/repos/$ADMIN/GreyZone/branch_protections"
  code=$(api GET "$path/$1")
  case "$code" in
    200) code=$(api PATCH "$path/$1" "$2") ;;
    404) code=$(api POST "$path" "$2") ;;
    *) say "FATAL: failed to read protection rule (HTTP $code)"; exit 1 ;;
  esac
  case "$code" in
    200|201) say "protection rule $1 configured" ;;
    *) say "FATAL: failed to configure protection rule (HTTP $code)"; exit 1 ;;
  esac
}
protect workflow-maintenance "{\"rule_name\":\"workflow-maintenance\",\"priority\":1,\"enable_push\":true,\"enable_push_whitelist\":true,\"push_whitelist_usernames\":[\"$ADMIN\"],\"push_whitelist_teams\":[],\"push_whitelist_deploy_keys\":false,\"enable_merge_whitelist\":true,\"merge_whitelist_usernames\":[\"$ADMIN\"],\"merge_whitelist_teams\":[],\"enable_force_push\":false,\"enable_bypass_allowlist\":false,\"protected_file_patterns\":\"\",\"unprotected_file_patterns\":\"\"}"
protect '%2A%2A' '{"rule_name":"**","priority":2,"enable_push":true,"enable_push_whitelist":false,"enable_merge_whitelist":false,"enable_force_push":false,"enable_bypass_allowlist":false,"block_admin_merge_override":false,"protected_file_patterns":".gitea/workflows/**","unprotected_file_patterns":""}'

# ---------- 8. Two MCP token tiers; stale grants are revoked and re-minted ----------
mkdir -p /shared/mcp-tokens
ALICE_AUTH="alice:$ALICE_PW"
token_record() {
  records=$(curl -fsS -u "$ALICE_AUTH" "$GITEA_URL/api/v1/users/alice/tokens") || return 1
  printf '%s' "$records" | sed 's/},{/}\n{/g' | grep -F "\"name\":\"$1\"" || true
}
revoke() { # parsed token ID
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "$ALICE_AUTH" \
    -X DELETE "$GITEA_URL/api/v1/users/alice/tokens/$1")
  [ "$code" = "204" ] || { say "FATAL: failed to revoke old token (HTTP $code)"; exit 1; }
}
mint() { # name scopes(JSON)
  record=$(token_record "$1")
  id=$(printf '%s' "$record" | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)
  scopes=$(printf '%s' "$record" | grep -o '"scopes":\[[^]]*\]' | cut -d'[' -f2 | tr -d ']' | tr ',' '\n' | sort)
  wanted=$(printf '%s' "$2" | tr ',' '\n' | sort)
  if [ -n "$id" ] && [ "$scopes" = "$wanted" ] && [ -s "/shared/mcp-tokens/$1.token" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' \
      -H "Authorization: token $(cat "/shared/mcp-tokens/$1.token")" "$GITEA_URL/api/v1/user")
    [ "$code" = "200" ] && { say "token $1 ready, skipping"; return; }
  fi
  [ -z "$id" ] || revoke "$id"
  tok=$(curl -fsS -u "$ALICE_AUTH" -X POST "$GITEA_URL/api/v1/users/alice/tokens" \
    -H 'Content-Type: application/json' -d "{\"name\":\"$1\",\"scopes\":[$2]}" \
    | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4)
  if [ -n "$tok" ]; then
    printf '%s' "$tok" > "/shared/mcp-tokens/$1.token"
    say "token $1 minted"
  else
    say "FATAL: failed to mint token $1"; exit 1
  fi
}
mint mcp-l1 '"read:user","write:issue"'
mint mcp-l2 '"read:user","write:issue","write:repository"'
record=$(token_record mcp-l3)
id=$(printf '%s' "$record" | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)
[ -z "$id" ] || revoke "$id"
rm -f /shared/mcp-tokens/mcp-l3.token

say "initialization complete"
