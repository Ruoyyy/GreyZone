# The user MCP (commonly-mcp/src/user-index.js). Build context: ./commonly-mcp.
# The container only idles; each MCP session is a `docker exec -i` into it, so
# no port is ever opened and only someone who can run docker can connect.
FROM node:22-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY src ./src
USER node
CMD ["sleep", "infinity"]
