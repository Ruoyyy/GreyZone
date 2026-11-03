import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function toolData(result, name) {
  const message = result.content?.filter((part) => part.type === 'text').map((part) => part.text).join(' ');
  if (result.isError) throw new Error(`${name}: ${message || 'MCP error'}`);
  if (!message) throw new Error(`${name}: empty MCP response`);
  return JSON.parse(message);
}

export function parseProbe(stdout, surface) {
  const line = stdout.trim().split('\n').at(-1);
  const probe = JSON.parse(line);
  const judgment = probe.judgments?.find((item) => item.surface === surface);
  if (!judgment) throw new Error(`Oracle did not judge ${surface}`);
  return { success: judgment.fired === true, judgment, verdict: probe.verdict };
}

export function targetTurnResult(reply, oracle, sessionId, timeoutSeconds) {
  const result = {
    output: reply ?? `[Morty did not reply within ${timeoutSeconds} seconds]`,
    sessionId,
    metadata: { oracle, replied: reply !== null },
  };
  if (oracle.success) {
    result.conversationEnded = true;
    result.conversationEndReason = 'oracle-success';
  }
  return result;
}

export default class ClawithTargetProvider {
  constructor(options = {}) {
    this.config = options.config ?? {};
    this.client = null;
    this.sessionId = null;
    this.oracleSucceeded = false;
  }

  id() { return 'clawith-alice-mcp'; }

  async connect() {
    if (this.client) return this.client;
    const key = process.env.MCP_API_KEY;
    if (!key) throw new Error('MCP_API_KEY is required');
    const client = new Client({ name: 'clawith-promptfoo', version: '1.0.0' });
    const transport = new StreamableHTTPClientTransport(new URL(this.config.mcpUrl), {
      requestInit: { headers: { Authorization: `Bearer ${key}` } },
    });
    await client.connect(transport);
    const names = new Set((await client.listTools()).tools.map((tool) => tool.name));
    for (const name of ['send_group_message', 'list_group_messages']) {
      if (!names.has(name)) {
        await client.close();
        throw new Error(`MCP bridge lacks ${name}; rebuild mcp-bridge`);
      }
    }
    this.client = client;
    return client;
  }

  async callTool(name, args) {
    const client = await this.connect();
    return toolData(await client.callTool({ name, arguments: args }, undefined, { timeout: 60_000 }), name);
  }

  probe() {
    const result = spawnSync('uv', ['run', '--with', 'httpx', 'python', '-m', 'oracle.watch', '--probe'], {
      cwd: root, env: process.env, encoding: 'utf8', timeout: 120_000,
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`Oracle probe failed: ${result.stderr || result.stdout}`);
    return parseProbe(result.stdout, this.config.surface);
  }

  async callApi(prompt, context = {}) {
    const sessionId = String(context.vars?.sessionId || '');
    if (!sessionId) return { error: 'Promptfoo sessionId is required' };
    if (this.sessionId && this.sessionId !== sessionId) {
      return { error: 'Only one Promptfoo candidate may use this Oracle episode' };
    }
    this.sessionId = sessionId;
    if (this.oracleSucceeded) {
      return { output: '', sessionId, conversationEnded: true,
        conversationEndReason: 'oracle-success' };
    }
    const content = String(prompt || '').trim();
    if (!content) return { error: 'Attack message is empty', sessionId };
    const ep = JSON.parse(await readFile(path.join(root, '.run/oracle/episode.json'), 'utf8'));
    if (ep.state !== 'running' || ep.n !== this.config.episode) {
      return { error: 'Oracle episode is not the armed episode', sessionId };
    }
    const lab = ep.lab;
    const args = { group_id: lab.group_id, session_id: lab.session_id };
    const baseline = await this.callTool('list_group_messages', { ...args, limit: 1 });
    let cursor = baseline.at(-1)?.cursor;
    await this.callTool('send_group_message', {
      ...args, content, mention_participant_ids: [lab.participants.Morty],
    });

    const timeout = Number(this.config.replyTimeoutSeconds);
    const interval = Number(this.config.pollIntervalSeconds);
    if (!(timeout > 0 && timeout <= 600 && interval > 0 && interval <= timeout)) {
      throw new Error('Invalid reply timeout or poll interval');
    }
    const deadline = Date.now() + timeout * 1000;
    let reply = null;
    while (Date.now() < deadline && reply === null) {
      const messages = await this.callTool('list_group_messages', { ...args, after: cursor, limit: 100 });
      for (const message of messages) {
        cursor = message.cursor || cursor;
        if (message.participant_id === lab.participants.Morty && message.role === 'assistant') {
          reply = String(message.content || '');
        }
      }
      if (reply === null) await sleep(Math.min(interval * 1000, Math.max(0, deadline - Date.now())));
    }
    const oracle = this.probe();
    this.oracleSucceeded = oracle.success;
    return targetTurnResult(reply, oracle, sessionId, timeout);
  }
}
