import { randomUUID } from 'node:crypto';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const REQUIRED_TOOLS = new Set(['send_email', 'list_received', 'read_received', 'reply_to']);

function toolData(result, name) {
  if (result.isError) {
    const detail = result.content?.filter((item) => item.type === 'text').map((item) => item.text).join(' ');
    throw new Error(`${name} failed: ${detail || 'unknown MCP error'}`);
  }
  if (!result.structuredContent || typeof result.structuredContent !== 'object') {
    throw new Error(`${name} did not return structuredContent; update redteam-mcp.ts`);
  }
  return result.structuredContent;
}

function sameAddress(left, right) {
  return String(left || '').trim().toLowerCase() === String(right || '').trim().toLowerCase();
}

function normalizedSubject(subject) {
  return String(subject || '').replace(/^(re|fw|fwd):\s*/gi, '').trim().toLowerCase();
}

export default class MailTargetProvider {
  constructor(options = {}) {
    this.config = options.config ?? {};
    this.sessions = new Map();
    this.client = null;
    this.connecting = null;
  }

  id() {
    return 'inbox-zero-mail-mcp';
  }

  async connect() {
    if (this.client) return this.client;
    if (this.connecting) return this.connecting;
    this.connecting = this.openClient();
    try {
      return await this.connecting;
    } finally {
      this.connecting = null;
    }
  }

  async openClient() {
    const mcp = this.config.mcp;
    if (!mcp?.command || !Array.isArray(mcp.args) || !mcp.cwd) {
      throw new Error('MCP command, args, and cwd are required');
    }
    if (!process.env.TRANSPORT_TOKEN) {
      throw new Error('TRANSPORT_TOKEN is required for the redteam-mcp server');
    }
    const client = new Client({ name: 'promptfoo-mail-target', version: '0.1.0' });
    const transport = new StdioClientTransport({
      command: mcp.command,
      args: mcp.args,
      cwd: mcp.cwd,
      env: {
        PATH: process.env.PATH || '',
        TRANSPORT_TOKEN: process.env.TRANSPORT_TOKEN,
        CUSTOMER_EMAIL: this.config.attackerEmail,
        CUSTOMER_OUTBOX: this.config.customerOutbox,
        TRANSPORT_URL: this.config.transportUrl,
      },
      stderr: 'inherit',
    });
    try {
      await client.connect(transport);
      const available = new Set((await client.listTools()).tools.map((tool) => tool.name));
      for (const name of REQUIRED_TOOLS) {
        if (!available.has(name)) throw new Error(`redteam-mcp is missing ${name}`);
      }
      this.client = client;
      return client;
    } catch (error) {
      await client.close();
      throw error;
    }
  }

  async callTool(name, args, deadline) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error('Target turn exceeded its deadline');
    const client = await this.connect();
    const result = await client.callTool(
      { name, arguments: args },
      undefined,
      { timeout: Math.min(60_000, remaining) },
    );
    return toolData(result, name);
  }

  async callApi(prompt, context = {}) {
    const sessionId = String(context.vars?.sessionId || randomUUID());
    const body = String(prompt || '').trim();
    if (!body) return { error: 'Attack message is empty', sessionId };
    const timeoutSeconds = Number(this.config.replyTimeoutSeconds ?? 300);
    const intervalSeconds = Number(this.config.pollIntervalSeconds ?? 5);
    if (!(timeoutSeconds > 0 && timeoutSeconds <= 300 && intervalSeconds > 0)) {
      return { error: 'Invalid reply timeout or poll interval', sessionId };
    }
    const deadline = Date.now() + timeoutSeconds * 1000;
    let session = this.sessions.get(sessionId);
    let writeAttempted = false;
    if (session?.stalled) {
      return { error: 'The previous turn received no reply; this email thread cannot continue', sessionId };
    }
    try {
      const baseline = await this.callTool('list_received', {}, deadline);
      if (!Array.isArray(baseline.messages)) throw new Error('Invalid list_received result');
      const afterIndex = Math.max(0, ...baseline.messages.map((mail) => Number(mail.index) || 0));
      let sent;
      if (session) {
        if (!session.lastReplyIndex) throw new Error('Email session has no reply to continue');
        writeAttempted = true;
        sent = await this.callTool('reply_to', { index: session.lastReplyIndex, body }, deadline);
      } else {
        const subject = `${this.config.subject} ${Math.floor(100000 + Math.random() * 900000)}`;
        session = { subject, lastReplyIndex: null, stalled: false };
        this.sessions.set(sessionId, session);
        writeAttempted = true;
        sent = await this.callTool('send_email', {
          to: this.config.managedEmail,
          subject,
          body,
        }, deadline);
      }
      if (typeof sent.rfcMessageId !== 'string' || !sent.rfcMessageId.startsWith('<')) {
        throw new Error('MCP send result lacks its RFC Message-ID');
      }
      const seen = new Set();
      while (Date.now() < deadline) {
        const listing = await this.callTool('list_received', {}, deadline);
        if (!Array.isArray(listing.messages)) throw new Error('Invalid list_received result');
        for (const mail of listing.messages) {
          if (Number(mail.index) <= afterIndex || seen.has(mail.index)) continue;
          seen.add(mail.index);
          if (!sameAddress(mail.from, this.config.managedEmail)) continue;
          if (!Array.isArray(mail.recipients) || !mail.recipients.some((to) => sameAddress(to, this.config.attackerEmail))) continue;
          const received = await this.callTool('read_received', { index: mail.index }, deadline);
          const threaded = received.inReplyTo === sent.rfcMessageId ||
            String(received.references || '').includes(sent.rfcMessageId);
          const sameSubject = normalizedSubject(received.subject) === normalizedSubject(session.subject);
          if (!threaded && !sameSubject) continue;
          session.lastReplyIndex = mail.index;
          return {
            output: String(received.body || ''),
            sessionId,
            metadata: {
              status: 'replied',
              deliveryId: sent.deliveryId,
              sentMessageId: sent.messageId,
              replyDeliveryId: received.deliveryId,
              replyIndex: mail.index,
            },
          };
        }
        await new Promise((resolve) => setTimeout(resolve, Math.min(intervalSeconds * 1000, Math.max(0, deadline - Date.now()))));
      }
      session.stalled = true;
      return {
        output: `[no reply within ${timeoutSeconds} seconds]`,
        sessionId,
        metadata: { status: 'no-reply', deliveryId: sent.deliveryId, sentMessageId: sent.messageId },
      };
    } catch (error) {
      if (writeAttempted) session.stalled = true;
      return { error: error instanceof Error ? error.message : String(error), sessionId };
    }
  }
}
