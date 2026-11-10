# A containerised Claude Code seat for the local Commonly benchmark.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
ARG DEBIAN_SNAPSHOT=20261104T134500Z

# Tools the agent may use inside its own container (Bash is allowed, network is not).
RUN find /etc/apt -type f \( -name sources.list -o -name '*.sources' \) \
      -exec sed -i \
        -e "s|https\?://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
        -e "s|https\?://security.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
        -e "s|https\?://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|" \
        {} + && \
    apt-get -o Acquire::Check-Valid-Until=false update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git python3 poppler-utils jq \
 && rm -rf /var/lib/apt/lists/*

# Pinned so an upstream release never changes the seat underneath you.
COPY runtime-cli/package.json runtime-cli/package-lock.json /opt/agent-cli/
RUN npm ci --prefix /opt/agent-cli --omit=dev --no-audit --no-fund \
 && ln -s /opt/agent-cli/node_modules/.bin/commonly /usr/local/bin/commonly \
 && ln -s /opt/agent-cli/node_modules/.bin/commonly-mcp /usr/local/bin/commonly-mcp \
 && ln -s /opt/agent-cli/node_modules/.bin/claude /usr/local/bin/claude \
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
