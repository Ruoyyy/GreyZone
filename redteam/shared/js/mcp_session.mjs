/**
 * Thin MCP client session: stdio or Streamable HTTP URL.
 * No scenario-specific tool names or business logic.
 */
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import {
  StdioClientTransport,
  getDefaultEnvironment,
} from '@modelcontextprotocol/sdk/client/stdio.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

/**
 * @typedef {object} McpStdioConfig
 * @property {string} command
 * @property {string[]} [args]
 * @property {string} [cwd]
 * @property {string[]|Record<string,string>} [env] keys from process.env, or explicit map
 */

/**
 * @typedef {object} McpUrlConfig
 * @property {string} url
 * @property {Record<string,string>} [headers]
 */

/**
 * @typedef {McpStdioConfig & McpUrlConfig & { timeoutMs?: number }} McpConnectConfig
 */

function buildEnv(envConfig) {
  // Child MCP servers need a full host env (PATH, credentials, etc.).
  const base = { ...getDefaultEnvironment(), ...process.env };
  if (!envConfig) {
    return base;
  }
  if (Array.isArray(envConfig)) {
    for (const key of envConfig) {
      if (process.env[key] !== undefined) {
        base[key] = process.env[key];
      }
    }
    return base;
  }
  return { ...base, ...envConfig };
}

function parseToolResult(result) {
  if (result?.isError) {
    const errText = (result.content || [])
      .filter((c) => c.type === 'text')
      .map((c) => c.text)
      .join('\n');
    const error = new Error(errText || 'MCP tool returned isError');
    error.mcpResult = result;
    throw error;
  }

  if (result?.structuredContent && typeof result.structuredContent === 'object') {
    return result.structuredContent;
  }

  const texts = (result?.content || [])
    .filter((c) => c.type === 'text')
    .map((c) => c.text);

  if (texts.length === 0) {
    return result ?? null;
  }

  const joined = texts.join('\n');
  try {
    return JSON.parse(joined);
  } catch {
    return { text: joined, raw: joined };
  }
}

export class McpSession {
  /**
   * @param {McpConnectConfig} config
   */
  constructor(config) {
    this.config = config || {};
    this.client = null;
    this.transport = null;
    this._started = false;
  }

  async start() {
    if (this._started) {
      return;
    }

    const timeoutMs = this.config.timeoutMs ?? 900_000;
    this.client = new Client({
      name: 'promptfoo-mcp-target',
      version: '1.0.0',
    });

    if (this.config.url) {
      const url = new URL(this.config.url);
      this.transport = new StreamableHTTPClientTransport(url, {
        requestInit: this.config.headers
          ? { headers: this.config.headers }
          : undefined,
      });
    } else if (this.config.command) {
      this.transport = new StdioClientTransport({
        command: this.config.command,
        args: this.config.args || [],
        cwd: this.config.cwd,
        env: buildEnv(this.config.env),
        stderr: 'inherit',
      });
    } else {
      throw new Error(
        'mcp config requires either { command, args? } (stdio) or { url } (HTTP)',
      );
    }

    await this.client.connect(this.transport);
    this._defaultTimeoutMs = timeoutMs;
    this._started = true;
  }

  /**
   * @param {string} name
   * @param {Record<string, unknown>} [args]
   * @param {{ timeoutMs?: number }} [options]
   */
  async callTool(name, args = {}, options = {}) {
    await this.start();
    const timeoutMs = options.timeoutMs ?? this._defaultTimeoutMs;
    const result = await this.client.callTool(
      { name, arguments: args },
      undefined,
      { timeout: timeoutMs },
    );
    return parseToolResult(result);
  }

  async listTools() {
    await this.start();
    const listed = await this.client.listTools();
    return listed?.tools || [];
  }

  async close() {
    if (!this._started) {
      return;
    }
    this._started = false;
    try {
      await this.client?.close?.();
    } catch {
      // ignore
    }
    try {
      await this.transport?.close?.();
    } catch {
      // ignore
    }
    this.client = null;
    this.transport = null;
  }
}
