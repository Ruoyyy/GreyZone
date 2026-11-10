#!/bin/sh
# Writes the seat record `commonly agent run` reads, then hands over.
# Required env: AGENT_NAME, COMMONLY_API_URL. The two credentials come from the
# Kai-only secrets volume: the runtime token the seed issued, and the LiteLLM key.
set -eu
: "${AGENT_NAME:?}" "${COMMONLY_API_URL:?}"
COMMONLY_AGENT_TOKEN=$(cat /run/kai/agent_token)
ANTHROPIC_AUTH_TOKEN=$(cat /run/kai/litellm_master_key)
export COMMONLY_AGENT_TOKEN ANTHROPIC_AUTH_TOKEN

MCP_ENTRY=/opt/agent-cli/node_modules/@commonlyai/mcp/src/index.js
mkdir -p "$HOME/.commonly/tokens"
umask 077

# The token's identity (agentName / instanceId / podId) comes from the server.
IDENTITY=$(node -e '
  fetch(process.env.COMMONLY_API_URL + "/api/agents/runtime/installations",
    { headers: { Authorization: "Bearer " + process.env.COMMONLY_AGENT_TOKEN } })
    .then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)))
    .then(d => {
      const i = (d.installations || []).find(x => x.status === "active") || {};
      process.stdout.write(JSON.stringify({
        agentName: d.agentName, instanceId: d.instanceId || i.instanceId || "default", podId: i.podId || null,
      }));
    })
    .catch(e => { console.error("[agent] cannot resolve token identity: " + e.message); process.exit(1); });
')

# No `sandbox` block: bubblewrap cannot run in an unprivileged container, so
# the container is the perimeter (same call as cloud-codex). The MCP server is
# pre-installed — the container has no internet to `npx` it.
echo "$IDENTITY" | jq \
  --arg url "$COMMONLY_API_URL" --arg tok "$COMMONLY_AGENT_TOKEN" --arg mcp "$MCP_ENTRY" '{
    agentName: .agentName, instanceId: .instanceId, podId: .podId,
    instanceUrl: $url, runtimeToken: $tok, adapter: "claude",
    environment: { mcp: [{
      name: "commonly", transport: "stdio", command: ["node", $mcp],
      env: { COMMONLY_API_URL: "${COMMONLY_API_URL}", COMMONLY_AGENT_TOKEN: "${COMMONLY_AGENT_TOKEN}" }
    }] },
    workspacePath: "/workspace"
  }' > "$HOME/.commonly/tokens/$AGENT_NAME.json"

cat > "$HOME/.commonly/config.json" <<JSON
{ "instances": { "local": { "url": "$COMMONLY_API_URL", "token": "" } }, "active": "local" }
JSON

echo "[agent] seat $(echo "$IDENTITY" | jq -r .agentName) → $COMMONLY_API_URL, workspace /workspace"
exec commonly agent run "$AGENT_NAME"
