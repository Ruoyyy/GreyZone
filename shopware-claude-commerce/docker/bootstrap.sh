#!/usr/bin/env bash
# Bootstrap the local Shopware stack for the Claude Commerce agents (ADR-10/11/14).
#
# Idempotent and re-runnable: every step looks before it writes, so running it twice
# yields the same state (one agent signing key, one shop signing key, one integration,
# one ACL role, no duplicate products/CMS pages/orders). See docker/README.md.
#
# Starting data: a fresh volume is filled from docker/snapshot/ (docker/snapshot.sh captures
# it); the seed steps afterwards find everything in place and change nothing.
#
# Plugins: SwagAgenticCommerce + SwagMcpMerchantTools (pinned clones), CommerceAgentsHandoff
# (docker/plugins) and SwagCommerceAgentTools (shopware-plugins/, this repo's agent tools).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/docker/compose.yaml")
CONTAINER="${SHOPWARE_CONTAINER:-commerce-agents-shopware}"
SHOP_URL="${SHOPWARE_PUBLIC_URL:-http://localhost:8080}"
# Admin password grant is used by bootstrap only (setup). Hosts never get these values;
# they authenticate with the integration written to docker/.generated.env (ADR-14).
ADMIN_USER="${SHOPWARE_ADMIN_USERNAME:-admin}"
ADMIN_PASS="${SHOPWARE_ADMIN_PASSWORD:-shopware}"

# Pinned upstream refs (docs/version-matrix.md). Override with a tag/branch/SHA if needed.
PLUGIN_REPO="${SWAG_AGENTIC_COMMERCE_REPO:-https://github.com/shopware/agentic-commerce.git}"
PLUGIN_REF="${SWAG_AGENTIC_COMMERCE_REF:-df0ae7231e2b2090ccfbf3a31e7a462b40b69a0f}"
MERCHANT_TOOLS_REPO="${SWAG_MCP_MERCHANT_TOOLS_REPO:-https://github.com/shopware/SwagMcpMerchantTools.git}"
MERCHANT_TOOLS_REF="${SWAG_MCP_MERCHANT_TOOLS_REF:-01e2082e99a4e9a2e56cdfd69faa38cd7c988efe}"
SDK_REPO="${UCP_PHP_SDK_REPO:-https://github.com/agentic-commerce-alliance/ucp-php-sdk.git}"
SDK_REVISION="${UCP_PHP_SDK_REVISION:-493a71f2c13f42e8aaa5ca376e90ae17afc70e80}"
SDK_CONSTRAINT='0.0.6'

SHOP_ROOT="/var/www/html"
PLUGIN_DIR="${SHOP_ROOT}/custom/plugins/SwagAgenticCommerce"
MERCHANT_DIR="${SHOP_ROOT}/custom/plugins/SwagMcpMerchantTools"
HANDOFF_SRC="$ROOT/docker/plugins/CommerceAgentsHandoff"
HANDOFF_DIR="${SHOP_ROOT}/custom/plugins/CommerceAgentsHandoff"
# SwagCommerceAgentTools (this repo): Store API MCP tools for the shopping agent and Admin
# MCP tools for the merchant agent (staged-change ledger swag_agent_staged_change, analytics).
AGENT_TOOLS_NAME="SwagCommerceAgentTools"
AGENT_TOOLS_SRC="$ROOT/shopware-plugins/${AGENT_TOOLS_NAME}"
AGENT_TOOLS_DIR="${SHOP_ROOT}/custom/plugins/${AGENT_TOOLS_NAME}"
# Development leftovers that must not travel into the shop (the container has its own vendor/).
AGENT_TOOLS_EXCLUDES=(--exclude='./vendor' --exclude='./.phpunit.cache' --exclude='./.phpstan.cache' --exclude='./phpstan.neon' --exclude='./composer.lock')

GENERATED_ENV="$ROOT/docker/.generated.env"
SECRETS_DIR="$ROOT/secrets"
AGENT_KEY_REL="secrets/ucp-agent-signing-key.pem"
AGENT_KEY="$ROOT/$AGENT_KEY_REL"
PROFILE_URL="http://localhost/agent-profile.json"   # fetched by Apache inside the container
SIGNATURE_POLICY="${UCP_SIGNATURE_POLICY:-strict}"
HANDOFF_SECRET_VAR="COMMERCE_AGENTS_HANDOFF_SECRET"
HANDOFF_SECRET_BYTES=32

# Python: the helpers need cryptography + httpx from the repo venv.
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "Python venv missing at $PYTHON — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec_shop() { docker exec -u www-data "$CONTAINER" bash -lc "cd ${SHOP_ROOT} && $*"; }
exec_root() { docker exec -u root "$CONTAINER" bash -lc "$*"; }
console()   { exec_shop "php bin/console $* --no-interaction"; }
step()      { echo; echo "==> $*"; }

wait_for_shop() {
  echo "Waiting for Shopware Admin API at ${SHOP_URL} ..."
  local i code
  for i in $(seq 1 90); do
    # `/` is 400 until the sales-channel domain matches the published port; the Admin API
    # answering 401 (no token) means PHP + Shopware are up.
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${SHOP_URL}/api/_info/version" || true)"
    if [[ "${code}" == "401" || "${code}" == "200" ]]; then
      echo "Shopware is answering HTTP (${code})."
      return 0
    fi
    sleep 4
  done
  echo "Shopware did not become ready in time. docker logs ${CONTAINER}:" >&2
  docker logs --tail 80 "$CONTAINER" >&2 || true
  return 1
}

wait_for_mysql() {
  echo "Waiting for MySQL inside ${CONTAINER} ..."
  local i
  for i in $(seq 1 60); do
    if exec_root "mysqladmin -uroot -proot ping --silent" >/dev/null 2>&1; then
      echo "MySQL is up."
      return 0
    fi
    sleep 2
  done
  echo "MySQL did not become ready in time." >&2
  return 1
}

# ensure_env_line KEY VALUE — set KEY=VALUE in the container's .env (Symfony Dotenv; there
# is no console command that edits .env).
ensure_env_line() {
  local key="$1" value="$2"
  exec_root "if grep -q '^${key}=' ${SHOP_ROOT}/.env; then sed -i 's|^${key}=.*|${key}=${value}|' ${SHOP_ROOT}/.env; else echo '${key}=${value}' >> ${SHOP_ROOT}/.env; fi"
}

container_env_value() {
  exec_root "grep -E '^$1=' ${SHOP_ROOT}/.env | tail -n1 | cut -d= -f2-" 2>/dev/null | tr -d '\r\n' || true
}

# ensure_repo DIR URL REF — clone if missing, then make sure HEAD == REF (SHA, tag or branch).
ensure_repo() {
  local dir="$1" url="$2" ref="$3" head
  # Clones are chowned to www-data afterwards; git (as root) then flags "dubious ownership".
  local git="git -c safe.directory='*'"
  if ! exec_root "test -d ${dir}/.git"; then
    echo "cloning ${url} -> ${dir}"
    exec_root "${git} clone --quiet '${url}' '${dir}'"
  fi
  head="$(exec_root "${git} -C '${dir}' rev-parse HEAD" | tr -d '\r\n')"
  if [[ "${head}" == "${ref}" ]]; then
    echo "$(basename "$dir") already at ${ref}"
    return 0
  fi
  echo "$(basename "$dir") at ${head}, checking out ${ref}"
  exec_root "${git} -C '${dir}' fetch --quiet --tags origin || true; \
             (${git} -C '${dir}' rev-parse --is-shallow-repository | grep -q true && ${git} -C '${dir}' fetch --quiet --unshallow origin) || true; \
             ${git} -C '${dir}' checkout --quiet --detach '${ref}' || (${git} -C '${dir}' fetch --quiet origin '${ref}' && ${git} -C '${dir}' checkout --quiet --detach FETCH_HEAD)"
  exec_root "${git} -C '${dir}' rev-parse HEAD"
}

plugin_state() {  # prints "installed active" flags for a plugin name: e.g. "1 1", "1 0", "0 0"
  exec_shop "php bin/console plugin:list --json" | "$PYTHON" -c '
import json, sys
name = sys.argv[1]
for plugin in json.load(sys.stdin):
    if plugin.get("name") == name:
        print(int(plugin.get("installedAt") is not None), int(bool(plugin.get("active"))), plugin.get("upgradeVersion") or "")
        break
else:
    print("0 0")
' "$1"
}

ensure_plugin_active() {  # ensure_plugin_active NAME — install+activate, or update+activate when already installed
  local name="$1" installed active upgrade
  read -r installed active upgrade <<<"$(plugin_state "$name")"
  if [[ "${installed}" == "0" ]]; then
    console "plugin:install --activate ${name}"
    echo "${name}: installed and activated"
    return 0
  fi
  if [[ -n "${upgrade}" ]]; then
    console "plugin:update ${name}"
    echo "${name}: updated to ${upgrade} (migrations ran)"
  fi
  if [[ "${active}" == "0" ]]; then
    console "plugin:activate ${name}"
    echo "${name}: activated"
  else
    echo "${name}: installed and active"
  fi
}

install_sdk() {
  if exec_shop 'cat vendor/composer/installed.json' | "$PYTHON" -c '
import json, sys
versions = {p["name"]: p["version"] for p in json.load(sys.stdin)["packages"]}
sys.exit(0 if all(versions.get(name) == sys.argv[1] for name in (
    "ucp-php-sdk/core", "ucp-php-sdk/symfony-bundle")) else 1)
' "$SDK_CONSTRAINT"; then
    echo "UCP SDK core and Symfony bundle already at ${SDK_CONSTRAINT}."
    return 0
  fi
  echo "Requiring matching UCP SDK core and Symfony bundle from Packagist..."
  if exec_shop "COMPOSER_MEMORY_LIMIT=-1 composer require 'ucp-php-sdk/core:${SDK_CONSTRAINT}' 'ucp-php-sdk/symfony-bundle:${SDK_CONSTRAINT}' --no-interaction --no-scripts"; then
    return 0
  fi
  echo "Packagist require failed; cloning SDK as path repositories."
  exec_root "test -d ${SHOP_ROOT}/custom/ucp-php-sdk/.git || git clone --no-checkout --depth 1 '${SDK_REPO}' ${SHOP_ROOT}/custom/ucp-php-sdk"
  exec_root "git -C ${SHOP_ROOT}/custom/ucp-php-sdk fetch --depth 1 origin '${SDK_REVISION}' && git -C ${SHOP_ROOT}/custom/ucp-php-sdk checkout --detach FETCH_HEAD"
  exec_shop 'composer config repositories.ucp-sdk-core "{\"type\":\"path\",\"url\":\"custom/ucp-php-sdk/packages/core\",\"options\":{\"symlink\":true,\"versions\":{\"ucp-php-sdk/core\":\"0.0.6\"}}}"'
  exec_shop 'composer config repositories.ucp-sdk-symfony "{\"type\":\"path\",\"url\":\"custom/ucp-php-sdk/packages/symfony-bundle\",\"options\":{\"symlink\":true,\"versions\":{\"ucp-php-sdk/symfony-bundle\":\"0.0.6\"}}}"'
  exec_shop "COMPOSER_MEMORY_LIMIT=-1 composer require 'ucp-php-sdk/core:${SDK_CONSTRAINT}' 'ucp-php-sdk/symfony-bundle:${SDK_CONSTRAINT}' --no-interaction --no-scripts"
}

# ----------------------------------------------------------------------------------------

step "Starting compose stack"
"${COMPOSE[@]}" up -d
wait_for_mysql

step "SwagAgenticCommerce sources and matching UCP SDK"
ensure_repo "$PLUGIN_DIR" "$PLUGIN_REPO" "$PLUGIN_REF"
exec_root "chown -R www-data:www-data ${SHOP_ROOT}/custom/plugins"
# Repair incompatible installed SDK versions before using the Admin API.
install_sdk
console 'cache:clear --no-warmup' >/dev/null
wait_for_shop

step "Shopware version"
exec_shop 'php bin/console --version'

step "Container .env: APP_URL, MCP_SERVER, profile dev mode, handoff secret"
# APP_URL is an environment variable, not system config — no console command sets it.
ensure_env_line APP_URL "${SHOP_URL}"
# MCP_SERVER: feature flag on 6.7.11–6.7.13 (Admin MCP /api/_mcp + UCP MCP proxy). Removed in
# 6.7.14+ where the server is always on (progressive tool discovery). compose.yaml sets it too.
ensure_env_line MCP_SERVER 1
# The UCP SDK refuses http://localhost agent-profile URLs unless this is on (local only).
ensure_env_line SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE 1
HANDOFF_SECRET="$(container_env_value "$HANDOFF_SECRET_VAR")"
if [[ -z "${HANDOFF_SECRET}" && -f "$GENERATED_ENV" ]]; then
  HANDOFF_SECRET="$(grep -E "^${HANDOFF_SECRET_VAR}=" "$GENERATED_ENV" | tail -n1 | cut -d= -f2- || true)"
fi
if [[ ${#HANDOFF_SECRET} -lt $((HANDOFF_SECRET_BYTES * 2)) ]]; then
  HANDOFF_SECRET="$(openssl rand -hex ${HANDOFF_SECRET_BYTES})"
  echo "${HANDOFF_SECRET_VAR}: generated"
else
  echo "${HANDOFF_SECRET_VAR}: reusing existing value"
fi
ensure_env_line "$HANDOFF_SECRET_VAR" "${HANDOFF_SECRET}"
export COMMERCE_AGENTS_HANDOFF_SECRET="${HANDOFF_SECRET}"

step "Sales-channel domain -> ${SHOP_URL} (Admin API)"
"$PYTHON" "$ROOT/docker/shop_domain.py" --shop-url "$SHOP_URL" --user "$ADMIN_USER" --password "$ADMIN_PASS"
console 'cache:clear --no-warmup' >/dev/null

step "SwagAgenticCommerce @ ${PLUGIN_REF}"
console 'plugin:refresh' >/dev/null
ensure_plugin_active SwagAgenticCommerce

step "SwagMcpMerchantTools @ ${MERCHANT_TOOLS_REF}"
ensure_repo "$MERCHANT_DIR" "$MERCHANT_TOOLS_REPO" "$MERCHANT_TOOLS_REF"
exec_root "chown -R www-data:www-data ${MERCHANT_DIR}"
console 'plugin:refresh' >/dev/null
ensure_plugin_active SwagMcpMerchantTools

step "CommerceAgentsHandoff (one-time handoff code -> storefront session)"
exec_root "mkdir -p ${HANDOFF_DIR}"
docker cp "${HANDOFF_SRC}/." "${CONTAINER}:${HANDOFF_DIR}"
exec_root "chown -R www-data:www-data ${HANDOFF_DIR}"
console 'plugin:refresh' >/dev/null
ensure_plugin_active CommerceAgentsHandoff
console 'cache:clear' >/dev/null

step "${AGENT_TOOLS_NAME} (Store API + Admin MCP agent tools, ledger swag_agent_staged_change)"
if [[ ! -f "${AGENT_TOOLS_SRC}/composer.json" ]]; then
  echo "plugin source missing at ${AGENT_TOOLS_SRC}" >&2
  exit 1
fi
# Mirror the repo folder into the container (a symlink cannot cross the Docker boundary and a
# bind mount would be chowned by dockware on boot): wipe, then stream a tar without vendor/
# and caches, so files removed from the repo do not linger in the shop.
exec_root "rm -rf ${AGENT_TOOLS_DIR} && mkdir -p ${AGENT_TOOLS_DIR}"
# COPYFILE_DISABLE / --no-xattrs: macOS bsdtar would otherwise add AppleDouble `._*` twins,
# which Shopware's migration loader then tries to load as PHP classes.
COPYFILE_DISABLE=1 tar --no-xattrs -C "$AGENT_TOOLS_SRC" "${AGENT_TOOLS_EXCLUDES[@]}" -cf - . \
  | docker exec -i -u root "$CONTAINER" tar --no-xattrs -C "$AGENT_TOOLS_DIR" -xf -
exec_root "chown -R www-data:www-data ${AGENT_TOOLS_DIR}"
console 'plugin:refresh' >/dev/null
ensure_plugin_active "$AGENT_TOOLS_NAME"
# plugin:install/update run the migrations; this is the no-op re-check for a re-run with
# unchanged version (a new migration without a version bump still lands).
if ! migrate_output="$(console "database:migrate --all ${AGENT_TOOLS_NAME}" 2>&1)"; then
  echo "${migrate_output}" >&2
  exit 1
fi
console 'cache:clear' >/dev/null
echo "debug:mcp: $(exec_shop 'php bin/console debug:mcp' | grep -c -E '^\| agent-' | tr -d '[:space:]') Admin agent tools registered (Store API tools are listed by /store-api/_mcp only)"

step "Starting data: docker/snapshot on a fresh volume (RESTORE_SNAPSHOT=1 forces it)"
seeded="$(exec_root "mysql -h127.0.0.1 -uroot -proot -N shopware -e \"SELECT COUNT(*) FROM product WHERE product_number = 'CA-OIL'\"" 2>/dev/null | tr -d '\r\n')"
if [[ ! -f "$ROOT/docker/snapshot/shopware.sql.gz" ]]; then
  echo "no snapshot; the seed steps below build the catalog"
elif [[ "${RESTORE_SNAPSHOT:-0}" == "1" || "${seeded}" == "0" ]]; then
  SHOPWARE_CONTAINER="$CONTAINER" "$ROOT/docker/restore_snapshot.sh"
  # The snapshot carries the domain it was taken on; point it at this stack's URL again.
  "$PYTHON" "$ROOT/docker/shop_domain.py" --shop-url "$SHOP_URL" --user "$ADMIN_USER" --password "$ADMIN_PASS"
  console 'cache:clear --no-warmup' >/dev/null
else
  echo "shop already seeded; keeping its data (RESTORE_SNAPSHOT=1 replaces it with the snapshot)"
  # Media files live in the container layer, not the MySQL volume: after a container recreate
  # the database still points at images that are gone. Put back only the missing ones.
  gunzip -c "$ROOT/docker/snapshot/media.tar.gz" \
    | docker exec -i -u root "$CONTAINER" tar -C "${SHOP_ROOT}/public" --skip-old-files -xf -
  exec_root "chown -R www-data:www-data ${SHOP_ROOT}/public/media"
fi
# Compiled theme assets live in the container layer too, under a path derived from the
# database (theme seed); after a restore or a container recreate the storefront would link
# CSS/JS that is not there. Compiling is idempotent and takes seconds.
console 'theme:compile' >/dev/null
console 'cache:clear --no-warmup' >/dev/null

step "Agent signing key -> agent-profile.json (exactly one signing_keys entry)"
mkdir -p "$SECRETS_DIR"
if [[ ! -f "$AGENT_KEY" ]]; then
  # P-256 private key, PKCS#8 PEM — what shopware_common.http_signing.RequestSigner reads.
  umask 077
  openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out "$AGENT_KEY"
  umask 022
  echo "generated ${AGENT_KEY_REL}"
else
  echo "reusing ${AGENT_KEY_REL}"
fi
chmod 600 "$AGENT_KEY"
"$PYTHON" "$ROOT/docker/agent_key.py" write-profile --pem "$AGENT_KEY" --profile "$ROOT/agent-profile.json"
# Shopware fetches the profile from inside the container (Apache :80). Not bind-mounted:
# dockware chowns public/ on boot and a read-only mount exits the container.
docker cp "$ROOT/agent-profile.json" "${CONTAINER}:${SHOP_ROOT}/public/agent-profile.json"
exec_root "chown www-data:www-data ${SHOP_ROOT}/public/agent-profile.json"

step "UCP exposure on the Storefront channel (signature policy: ${SIGNATURE_POLICY}, one shop signing key, profile cache purged)"
"$PYTHON" "$ROOT/docker/enable_ucp.py" --shop-url "$SHOP_URL" --container "$CONTAINER" \
  --user "$ADMIN_USER" --password "$ADMIN_PASS" --signature-policy "$SIGNATURE_POLICY"

step "Seed catalog: variants, Grundpreis, delivery times, shipping prices, policy CMS pages"
"$PYTHON" "$ROOT/docker/seed_catalog.py" --shop-url "$SHOP_URL" --user "$ADMIN_USER" --password "$ADMIN_PASS"

step "Seed order history (~40 orders / 60 days, marker customerComment=commerce-agents-seed)"
"$PYTHON" "$ROOT/docker/seed_orders.py" --shop-url "$SHOP_URL" --user "$ADMIN_USER" --password "$ADMIN_PASS"
echo "dal:refresh:index ..."
console 'dal:refresh:index' >/dev/null 2>&1 || echo "WARNING: dal:refresh:index failed (search index may lag)" >&2

step "docker/.generated.env for the hosts (no admin password)"
"$PYTHON" "$ROOT/docker/write_credentials.py" --shop-url "$SHOP_URL" --user "$ADMIN_USER" --password "$ADMIN_PASS" \
  --out "$GENERATED_ENV" --profile-url "$PROFILE_URL" --signing-key-pem-file "$AGENT_KEY_REL" --handoff-secret-from-env

step "Merchant identity: ACL role + integration claude-merchant-agent + MCP allowlist (ADR-14)"
"$PYTHON" "$ROOT/docker/merchant_identity.py" --shop-url "$SHOP_URL" --user "$ADMIN_USER" --password "$ADMIN_PASS" \
  --generated-env "$GENERATED_ENV"

step "Third-party seller identity: ACL role + integration third-party-seller (create-only)"
"$PYTHON" "$ROOT/docker/seller_identity.py" --shop-url "$SHOP_URL" --user "$ADMIN_USER" --password "$ADMIN_PASS" \
  --generated-env "$GENERATED_ENV"

step "Signed vs unsigned UCP request (policy ${SIGNATURE_POLICY})"
if [[ "$SIGNATURE_POLICY" == "strict" ]]; then
  "$PYTHON" "$ROOT/docker/ucp_signed_check.py" --shop-url "$SHOP_URL" --pem "$AGENT_KEY" --profile-url "$PROFILE_URL"
else
  echo "skipped (policy is ${SIGNATURE_POLICY}; unsigned requests are accepted)"
fi

step "UCP discovery"
curl -fsS "${SHOP_URL}/.well-known/ucp" | "$PYTHON" -c '
import json, sys
doc = json.load(sys.stdin)
services = (doc.get("ucp") or {}).get("services", {}).get("dev.ucp.shopping", [])
print("transports:", ", ".join(s.get("transport", "?") for s in services))
print("shop signing keys:", ", ".join(k.get("kid", "?") for k in doc.get("signing_keys", [])))
'

echo
echo "Bootstrap finished."
echo "  Storefront   ${SHOP_URL}"
echo "  Admin        ${SHOP_URL}/admin  (${ADMIN_USER} / ${ADMIN_PASS} — setup only; hosts use the integration)"
echo "  Credentials  ${GENERATED_ENV}"
echo "  Agent key    ${AGENT_KEY_REL}"
echo "  Verify       docker/verify.sh"
