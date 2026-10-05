#!/usr/bin/env node
/**
 * Commonly user MCP — a stdio MCP server that acts as ONE human account.
 *
 * Where src/index.js serves an agent (a cm_agent_* runtime token, agent
 * routes), this serves a person: their cm_ user token, the routes the web app
 * uses, their permissions. Point your own agent at it and it speaks as them.
 *
 * Run (the local stack does this inside the user-mcp container):
 *   COMMONLY_API_URL=http://backend:5000 \
 *   COMMONLY_USER_TOKEN_FILE=/run/mcp/user_token \
 *   node src/user-index.js
 */
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import { loadUserConfig } from './client.js';
import { buildUserTools } from './user-tools.js';

const main = async () => {
  const config = loadUserConfig();
  const tools = buildUserTools(config);
  const byName = new Map(tools.map((t) => [t.name, t]));

  const server = new Server(
    { name: 'commonly-user', version: '0.1.0' },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: tools.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })),
  }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const tool = byName.get(req.params.name);
    if (!tool) {
      return {
        isError: true,
        content: [{ type: 'text', text: JSON.stringify({ message: `Unknown tool: ${req.params.name}` }) }],
      };
    }
    return tool.call(req.params.arguments || {});
  });

  await server.connect(new StdioServerTransport());
};

main().catch((e) => {
  // Stderr only — stdout is owned by the MCP transport.
  process.stderr.write(`[commonly-user-mcp] fatal: ${e.message}\n`);
  process.exit(1);
});
