import { appendFile, readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function record(event) {
  if (process.env.CLAWITH_EXPERIMENT_EVENTS) {
    await appendFile(process.env.CLAWITH_EXPERIMENT_EVENTS, JSON.stringify(event) + '\n');
  }
}

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

export function runtimeError(reply, states) {
  const match = /^\u4efb\u52a1(?:\u6267\u884c|\u89c4\u5212)\u672a\u5b8c\u6210\u3002\n\u9519\u8bef\uff1a[\s\S]*\n\u9519\u8bef\u7801\uff1a([A-Za-z0-9_.-]+)\nRun ID\uff1a([0-9a-fA-F-]{36})\s*$/.exec(reply ?? '');
  if (match && states.some((run) => run.run_id === match[2] && ['failed', 'cancelled'].includes(run.status))) {
    return { code: match[1], run_id: match[2] };
  }
  const failed = states.find((run) => ['failed', 'cancelled'].includes(run.status));
  return failed ? { code: `target_${failed.status}`, run_id: failed.run_id } : null;
}

export function stateQueryError(result) {
  let detail;
  try { detail = JSON.parse(result.stdout || '{}').error; } catch { /* Keep process errors below. */ }
  return { code: 'target_state_unavailable', ...detail,
    ...(result.error ? { process_error: result.error.message } : {}),
    ...(result.stderr ? { stderr: result.stderr.slice(-2000) } : {}) };
}

export function targetTurnResult(reply, oracle, sessionId, timeoutSeconds, targetError = null) {
  const result = {
    output: reply ?? `[Morty did not reply within ${timeoutSeconds} seconds]`,
    sessionId,
    metadata: { oracle, replied: reply !== null },
  };
  if (targetError) return { ...result, error: `Target Run failed: ${targetError.code}`,
    metadata: { ...result.metadata, targetError } };
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
    this.turns = 0;
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
        throw new Error(`MCP bridge lacks ${name}; rebuild redteam-mcp`);
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
    const result = spawnSync('uv', ['run', '--with-requirements', 'oracle/requirements.txt', 'python', '-m', 'oracle.watch', '--probe'], {
      cwd: root, env: process.env, encoding: 'utf8', timeout: Number(process.env.CLAWITH_ORACLE_TIMEOUT || 1200) * 1000,
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`Oracle probe failed: ${result.stderr || result.stdout}`);
    return parseProbe(result.stdout, this.config.surface);
  }

  async callApi(prompt, context = {}) {
    if (this.failed) return { error: 'Previous target turn failed; reset before another attack' };
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
    if (this.turns >= Number(this.config.maxTurns || 10)) {
      return { error: 'Physical attack turn budget exhausted', sessionId };
    }
    const content = String(prompt || '').trim();
    if (!content) return { error: 'Attack message is empty', sessionId };
    const ep = JSON.parse(await readFile(path.join(root, '.run/oracle/episode.json'), 'utf8'));
    if (ep.state !== 'running' || ep.n !== this.config.episode) {
      return { error: 'Oracle episode is not the armed episode', sessionId };
    }
    const lab = ep.lab;
    const timeout = Number(this.config.replyTimeoutSeconds);
    const interval = Number(this.config.pollIntervalSeconds);
    if (!(timeout > 0 && timeout <= 600 && interval > 0 && interval <= timeout)) {
      throw new Error('Invalid reply timeout or poll interval');
    }
    const args = { group_id: lab.group_id, session_id: lab.session_id };
    const baseline = await this.callTool('list_group_messages', { ...args, limit: 1 });
    let cursor = baseline.at(-1)?.cursor;
    await record({ kind: 'intent', episode: ep.n, message: content,
      group_id: lab.group_id, session_id: lab.session_id });
    this.failed = true; // A failed send may already have reached the backend.
    this.turns += 1;
    const intake = await this.callTool('send_group_message', {
      ...args, content, mention_participant_ids: [lab.participants.Morty],
    });
    const attackMessageId = intake.message?.id;
    if (!attackMessageId) throw new Error('Target intake is missing its accepted message ID');

    const deadline = Date.now() + timeout * 1000;
    let reply = null;
    let states = [];
    let targetError = null;
    let completed = false;
    let replyMessageId = null;
    const candidates = new Map();
    while (Date.now() < deadline) {
      const messages = await this.callTool('list_group_messages', { ...args, after: cursor, limit: 100 });
      for (const message of messages) {
        cursor = message.cursor || cursor;
        if (message.participant_id === lab.participants.Morty && message.role === 'assistant') {
          candidates.set(message.id, String(message.content || ''));
        }
      }
      const state = spawnSync('python3', [path.join(root, 'redteam/target_runtime.py')], {
        cwd: root, input: JSON.stringify({ lab, intake }), encoding: 'utf8', timeout: 30_000,
      });
      if (state.error || state.status !== 0) {
        targetError = stateQueryError(state);
        states = (intake.run_ids ?? []).map((run_id) => ({ run_id, status: 'unknown' }));
        break;
      }
      states = JSON.parse(state.stdout);
      if (states.some((run) => run.source_message_id !== attackMessageId)) {
        targetError = { code: 'target_message_mismatch' };
        break;
      }
      const allowed = new Set(states.flatMap((run) => run.reply_message_ids ?? []));
      for (const [id, text] of candidates) {
        if (allowed.has(id)) { reply = text; replyMessageId = id; }
      }
      targetError = runtimeError(reply, states);
      completed = states.length > 0 && states.every((run) => run.status === 'completed' &&
        ['delivered', 'not_required'].includes(run.delivery_status));
      if (targetError || (completed && reply !== null)) break;
      await sleep(Math.min(interval * 1000, Math.max(0, deadline - Date.now())));
    }
    if (!targetError && (!completed || reply === null)) targetError = { code: 'target_run_timeout' };
    const oracle = this.probe();
    this.oracleSucceeded = oracle.success;
    this.failed = Boolean(targetError);
    await record({ kind: 'result', episode: ep.n, reply,
      status: targetError ? 'target-error' : (oracle.success ? 'oracle-success' : 'continue'),
      target_runs: states, target_error: targetError,
      attack_message_id: attackMessageId, reply_message_id: replyMessageId,
      group_id: lab.group_id, session_id: lab.session_id,
      oracleSuccess: oracle.success });
    return targetTurnResult(reply, oracle, sessionId, timeout, targetError);
  }
}
