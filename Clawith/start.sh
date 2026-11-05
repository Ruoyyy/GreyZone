#!/usr/bin/env bash
# One-click startup for the Clawith threat-lab environment.
#
#   ./start.sh          # build + up + bootstrap (idempotent)
#
# Produces:
#   admin  admin@clawith.com / admin       (platform_admin)
#   alice  alice@clawith.com / alice (member, MCP bridge identity)
#   agents Morty (company, group) + Meeseeks (private)
#   group  "threat-lab" with admin + alice + Morty
#   MCP    http://127.0.0.1:3010/mcp (key printed in mcp-bridge logs)
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env"

sed_in_place() {
    if [ "$(uname -s)" = "Darwin" ]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

ensure_env() {
    if [ ! -f "$ENV_FILE" ]; then
        cp .env.example "$ENV_FILE"
        echo "[start] created $ENV_FILE from .env.example"
    fi
    # Generate secrets if missing
    for var in JWT_SECRET_KEY SECRET_KEY; do
        current=$(grep -E "^${var}=" "$ENV_FILE" | tail -1 | cut -d= -f2-)
        if [ -z "$current" ] || [[ "$current" == change-me-* ]]; then
            generated=$(openssl rand -hex 32)
            if grep -qE "^${var}=" "$ENV_FILE"; then
                sed_in_place "s|^${var}=.*|${var}=${generated}|" "$ENV_FILE"
            else
                echo "${var}=${generated}" >> "$ENV_FILE"
            fi
            echo "[start] generated ${var}"
        fi
    done
    # Drop duplicate MCP-era vars from older .env layouts (keep first occurrence)
    for var in ALICE_USER_ID ALICE_TENANT_ID ALICE_ROLE TOKEN_TTL_DAYS MCP_API_KEY MCP_PORT; do
        count=$(grep -cE "^${var}=" "$ENV_FILE" || true)
        if [ "$count" -gt 1 ]; then
            sed_in_place "/^${var}=/d" "$ENV_FILE"
            echo "${var}=" >> "$ENV_FILE"
            echo "[start] normalized duplicate ${var}"
        fi
        grep -qE "^${var}=" "$ENV_FILE" || echo "${var}=" >> "$ENV_FILE"
    done
    if [ -z "$(grep -E '^OPENAI_API_KEY=' "$ENV_FILE" | cut -d= -f2-)" ]; then
        echo "[start] WARNING: OPENAI_API_KEY empty — environment will have no LLM model"
    fi
}

set_env_value() {  # var value
    sed_in_place "s|^$1=.*|$1=$2|" "$ENV_FILE"
}

wait_for_backend() {
    echo "[start] waiting for backend health..."
    for _ in $(seq 1 120); do
        if docker compose exec -T backend python -c "
import urllib.request, sys
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=3).status == 200 else 1)
" >/dev/null 2>&1; then
            echo "[start] backend healthy"
            return 0
        fi
        sleep 3
    done
    echo "[start] ERROR: backend did not become healthy" >&2
    exit 1
}

register_first_admin() {
    echo "[start] registering first admin (idempotent)..."
    docker compose exec -T backend python -c "
import httpx, sys
try:
    r = httpx.post('http://localhost:8000/api/auth/register', json={
        'username': 'admin', 'email': 'admin@clawith.com',
        'password': 'admin-bootstrap-tmp', 'display_name': 'admin'}, timeout=15)
    print('[start] register:', r.status_code, r.text[:120])
    sys.exit(0)
except Exception as exc:
    print('[start] register failed:', exc)
    sys.exit(0)
" || true
}

run_bootstrap() {
    echo "[start] running environment bootstrap..."
    BOOTSTRAP_API_KEY=$(grep -E '^OPENAI_API_KEY=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
    BOOTSTRAP_MODEL=$(grep -E '^OPENAI_MODEL=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
    BOOTSTRAP_BASE_URL=$(grep -E '^OPENAI_BASE_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
    OUT=$(docker compose exec -T -e OPENAI_API_KEY="$BOOTSTRAP_API_KEY" \
        -e OPENAI_MODEL="$BOOTSTRAP_MODEL" -e OPENAI_BASE_URL="${BOOTSTRAP_BASE_URL:-https://api.openai.com/v1}" \
        -e PYTHONPATH=/app backend \
        python /app/scripts/bootstrap_env.py 2>&1 | tee /dev/stderr)
    USER_ID=$(echo "$OUT" | sed -nE 's/^BOOTSTRAP_ALICE_USER_ID=(.+)$/\1/p' | tail -1)
    TENANT_ID=$(echo "$OUT" | sed -nE 's/^BOOTSTRAP_ALICE_TENANT_ID=(.+)$/\1/p' | tail -1)
    if [ -z "$USER_ID" ] || [ -z "$TENANT_ID" ]; then
        echo "[start] ERROR: bootstrap did not report ids" >&2
        exit 1
    fi
    set_env_value ALICE_USER_ID "$USER_ID"
    set_env_value ALICE_TENANT_ID "$TENANT_ID"
    echo "[start] .env updated with alice ids"
}

main() {
    ensure_env
    # The optional proxy file is mounted as a file by Compose.
    if [ ! -e ss-nodes.json ]; then
        printf '[]\n' > ss-nodes.json
    fi
    echo "[start] building images..."
    docker compose build
    echo "[start] starting core services..."
    docker compose up -d postgres redis backend frontend
    wait_for_backend
    register_first_admin
    run_bootstrap
    echo "[start] starting MCP bridge..."
    docker compose up -d mcp-bridge
    sleep 5
    MCP_KEY=$(docker compose logs mcp-bridge 2>&1 | sed -nE 's/^.*MCP API key: ([^[:space:]]+).*$/\1/p' | tail -1)
    echo
    echo "══════════════════════════════════════════════════════"
    echo " Clawith threat-lab is up"
    echo "   Web UI   : http://localhost:${FRONTEND_PORT:-3008}"
    echo "   admin    : admin@clawith.com / admin"
    echo "   alice    : alice@clawith.com / alice"
    echo "   MCP      : http://127.0.0.1:${MCP_PORT:-3010}/mcp"
    echo "   MCP key  : ${MCP_KEY:-<see: docker compose logs mcp-bridge>}"
    echo "══════════════════════════════════════════════════════"
}

main "$@"
