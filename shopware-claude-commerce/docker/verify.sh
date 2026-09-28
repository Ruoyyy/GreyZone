#!/usr/bin/env bash
# Verify the bootstrapped Shopware stack (run after docker/bootstrap.sh). Exit 1 on failure.
#
#   1. /.well-known/ucp: UCP active, transports rest+mcp+embedded, exactly one shop signing key
#   2. agent-profile.json: exactly one signing_keys entry == the PEM; container copy identical
#   3. signaturePolicy=strict: signed request 200, unsigned 401 (docker/ucp_signed_check.py)
#   4. Admin MCP via the integration token: tools/list == allowlist, product search + dryRun
#      upsert ok, `user` search refused (docker/merchant_identity.py --verify-only)
#   5. Handoff code round trip: POST -> /checkout/confirm, replay/GET -> /checkout/cart
#   6. Idempotency counts: one integration/role/product/CMS page/... (docker/verify_state.py)
#   7. SwagCommerceAgentTools: /store-api/_mcp lists shopping-policy-search, -disclosure,
#      -fulfillment-options (each called once); /api/_mcp lists agent-change-*,
#      agent-business-snapshot, agent-metrics-series (read tools called once)
#      (docker/agent_tools_check.py). 6.7.13 lists every tool directly (McpToolGroup inert).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="${SHOPWARE_CONTAINER:-commerce-agents-shopware}"
SHOP_URL="${SHOPWARE_PUBLIC_URL:-http://localhost:8080}"
GENERATED_ENV="$ROOT/docker/.generated.env"
PROFILE="$ROOT/agent-profile.json"
PROFILE_URL="http://localhost/agent-profile.json"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
EXPECTED_TRANSPORTS="embedded,mcp,rest"

FAILURES=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAILURES=$((FAILURES + 1)); }
section() { echo; echo "== $*"; }

if [[ ! -x "$PYTHON" ]]; then echo "python venv missing at $PYTHON" >&2; exit 1; fi
if [[ ! -f "$GENERATED_ENV" ]]; then echo "$GENERATED_ENV missing — run docker/bootstrap.sh" >&2; exit 1; fi
env_value() { grep -E "^$1=" "$GENERATED_ENV" | tail -n1 | cut -d= -f2-; }

PEM_REL="$(env_value UCP_AGENT_SIGNING_KEY_PEM_FILE)"
PEM="$ROOT/${PEM_REL:-secrets/ucp-agent-signing-key.pem}"
ACCESS_KEY="$(env_value SHOPWARE_SALES_CHANNEL_ACCESS_KEY)"
HANDOFF_SECRET="$(env_value COMMERCE_AGENTS_HANDOFF_SECRET)"

section "1. UCP discovery"
DISCOVERY="$(curl -fsS --max-time 15 "${SHOP_URL}/.well-known/ucp" || true)"
if [[ -z "$DISCOVERY" ]]; then
  fail "/.well-known/ucp unreachable"
else
  read -r TRANSPORTS KEYS VERSION <<<"$(printf '%s' "$DISCOVERY" | "$PYTHON" -c '
import json, sys
doc = json.load(sys.stdin)
services = (doc.get("ucp") or {}).get("services", {}).get("dev.ucp.shopping", [])
print(",".join(sorted(s.get("transport", "?") for s in services)), len(doc.get("signing_keys") or doc.get("keys") or []), (doc.get("ucp") or {}).get("version", "?"))
')"
  [[ "$TRANSPORTS" == "$EXPECTED_TRANSPORTS" ]] && pass "transports ${TRANSPORTS} (ucp ${VERSION})" || fail "transports ${TRANSPORTS} (expected ${EXPECTED_TRANSPORTS})"
  [[ "$KEYS" == "1" ]] && pass "exactly one shop signing key published" || fail "${KEYS} shop signing keys published (expected 1)"
fi

section "2. Agent profile"
if "$PYTHON" "$ROOT/docker/agent_key.py" check --pem "$PEM" --profile "$PROFILE"; then
  pass "agent-profile.json has exactly one signing key matching ${PEM_REL}"
else
  fail "agent-profile.json signing_keys"
fi
LOCAL_MD5="$(md5 -q "$PROFILE" 2>/dev/null || md5sum "$PROFILE" | cut -d' ' -f1)"
REMOTE_MD5="$(docker exec "$CONTAINER" md5sum /var/www/html/public/agent-profile.json 2>/dev/null | cut -d' ' -f1)"
[[ -n "$REMOTE_MD5" && "$LOCAL_MD5" == "$REMOTE_MD5" ]] && pass "container copy of agent-profile.json matches (${LOCAL_MD5})" || fail "container agent-profile.json differs (local ${LOCAL_MD5}, container ${REMOTE_MD5:-missing})"

section "3. Signature policy (strict)"
if "$PYTHON" "$ROOT/docker/ucp_signed_check.py" --shop-url "$SHOP_URL" --pem "$PEM" --profile-url "$PROFILE_URL"; then
  pass "signed 200 / unsigned 401"
else
  fail "signed/unsigned check"
fi

section "4. Admin MCP allowlist (integration token)"
if "$PYTHON" "$ROOT/docker/merchant_identity.py" --shop-url "$SHOP_URL" --generated-env "$GENERATED_ENV" --verify-only; then
  pass "tools/list == allowlist; product search + dryRun upsert ok; user search refused"
else
  fail "MCP allowlist verification"
fi

section "5. Handoff code round trip"
if [[ -z "$HANDOFF_SECRET" ]]; then
  fail "COMMERCE_AGENTS_HANDOFF_SECRET missing in ${GENERATED_ENV}"
elif COMMERCE_AGENTS_HANDOFF_SECRET="$HANDOFF_SECRET" "$PYTHON" "$ROOT/docker/handoff_check.py" --shop-url "$SHOP_URL" --access-key "$ACCESS_KEY"; then
  pass "POST -> /checkout/confirm, replay + GET -> /checkout/cart"
else
  fail "handoff round trip"
fi

section "6. Idempotency summary"
if "$PYTHON" "$ROOT/docker/verify_state.py" --shop-url "$SHOP_URL" --container "$CONTAINER"; then
  pass "no duplicates after re-runs"
else
  fail "idempotency counts"
fi

section "7. Agent tools (SwagCommerceAgentTools)"
if "$PYTHON" "$ROOT/docker/agent_tools_check.py" --shop-url "$SHOP_URL" --generated-env "$GENERATED_ENV"; then
  pass "shopping-* on /store-api/_mcp and agent-* on /api/_mcp listed and answering"
else
  fail "agent tools check"
fi

echo
if [[ "$FAILURES" -eq 0 ]]; then
  echo "verify.sh: all checks passed"
  exit 0
fi
echo "verify.sh: ${FAILURES} check(s) failed"
exit 1
