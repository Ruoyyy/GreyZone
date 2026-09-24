import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

function decodeResult(result, name) {
  if (result.isError) {
    const detail = result.content?.filter((item) => item.type === 'text').map((item) => item.text).join(' ');
    throw new Error(`${name} failed: ${detail || 'unknown MCP error'}`);
  }
  if (result.structuredContent != null) return result.structuredContent;
  const text = result.content?.filter((item) => item.type === 'text').map((item) => item.text).join('\n');
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${name} did not return JSON`);
  }
}

export class McpSession {
  constructor(client, tools, timeoutMs) {
    this.client = client;
    this.tools = tools;
    this.timeoutMs = timeoutMs;
  }

  static async connect(config, requiredTools, timeoutMs) {
    const client = new Client({ name: 'promptfoo-letta-target', version: '0.1.0' });
    const transport = config.url
      ? new StreamableHTTPClientTransport(new URL(process.env.PROMPTFOO_MCP_URL || config.url))
      : new StdioClientTransport({
          command: config.command,
          args: config.args || [],
          cwd: config.cwd,
          env: { PATH: process.env.PATH || '', ...config.env },
          stderr: 'inherit',
        });
    try {
      await client.connect(transport);
      const tools = new Set();
      let cursor;
      do {
        const page = await client.listTools(cursor ? { cursor } : undefined);
        for (const tool of page.tools) tools.add(tool.name);
        cursor = page.nextCursor;
      } while (cursor);
      for (const name of requiredTools) {
        if (!tools.has(name)) throw new Error(`MCP server is missing ${name}`);
      }
      return new McpSession(client, tools, timeoutMs);
    } catch (error) {
      await client.close();
      throw error;
    }
  }

  async callTool(name, args) {
    const result = await this.client.callTool(
      { name, arguments: args }, undefined, { timeout: this.timeoutMs },
    );
    return decodeResult(result, name);
  }

  async close() {
    await this.client.close();
  }
}
