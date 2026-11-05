# A containerised Claude Code seat for the local Commonly benchmark.
FROM node:22-bookworm-slim

# Tools the agent may use inside its own container (Bash is allowed, network is not).
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git python3 poppler-utils jq \
 && rm -rf /var/lib/apt/lists/*

# Pinned so an upstream release never changes the seat underneath you.
RUN npm install --global --no-audit --no-fund \
      @commonlyai/cli@0.1.81 \
      @commonlyai/mcp@0.3.13 \
      @anthropic-ai/claude-code@2.1.286 \
 && npm cache clean --force

# Managed policy: highest-precedence Claude Code settings, root-owned, so the
# agent (running as `node`) cannot widen its own permissions.
COPY managed-settings.json /etc/claude-code/managed-settings.json
COPY entrypoint.sh /usr/local/bin/agent-entrypoint
RUN chmod 0644 /etc/claude-code/managed-settings.json && chmod 0755 /usr/local/bin/agent-entrypoint \
 && mkdir -p /workspace && chown node:node /workspace

USER node
WORKDIR /workspace
ENTRYPOINT ["agent-entrypoint"]
