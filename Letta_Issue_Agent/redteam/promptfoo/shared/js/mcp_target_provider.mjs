import { McpSession } from './mcp_session.mjs';

const DEFAULTS = {
  defaults: { owner: 'letta-admin', repo: 'AgentThreatDataset' },
  tools: { createIssue: 'issue_write', comment: 'issue_write', wait: 'issue_read' },
  createArgs: {
    method: 'create', owner: '{{owner}}', repo: '{{repo}}',
    title: 'Question about repo configuration', body: '{{prompt}}',
  },
  commentArgs: {
    method: 'add_comment', owner: '{{owner}}', repo: '{{repo}}',
    issue_number: '{{issue_number}}', body: '{{prompt}}',
  },
  result: { issueField: 'number', outputField: 'reply' },
  wait: {
    mode: 'poll_comments', intervalSeconds: 5, timeoutSeconds: 900,
    botUser: 'gitea-actions',
    doneMarks: ['Letta Code finished', 'Letta Code failed', 'Letta Code encountered an error'],
    failMarks: ['Letta Code failed', 'Letta Code encountered an error'],
    args: {
      method: 'get_comments', owner: '{{owner}}', repo: '{{repo}}',
      issue_number: '{{issue_number}}',
    },
  },
};

export function isIssueSessionId(value, pattern = '^[0-9]+$') {
  return typeof value === 'string' && new RegExp(pattern).test(value);
}

export function renderTemplate(value, vars) {
  if (typeof value === 'string') {
    return value.replace(/{{\s*([a-zA-Z_][\w]*)\s*}}/g, (_, key) => String(vars[key] ?? ''));
  }
  if (Array.isArray(value)) return value.map((item) => renderTemplate(item, vars));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, renderTemplate(item, vars)]));
  }
  return value;
}

function commentsFrom(raw) {
  if (Array.isArray(raw)) return raw;
  if (Array.isArray(raw?.comments)) return raw.comments;
  if (Array.isArray(raw?.data)) return raw.data;
  if (raw && typeof raw === 'object' && 'id' in raw && 'body' in raw) return [raw];
  throw new Error('Unexpected MCP comments response');
}

function cleanReply(body) {
  const lines = String(body).replace(/<!-- letta-metadata[\s\S]*?-->/g, '').split('\n');
  if (lines[0]?.startsWith('**Letta Code')) lines.shift();
  while (lines.at(-1)?.trim() === 'Letta Code finished') lines.pop();
  return lines.join('\n').split('\n🤖')[0].trim().replace(/^-+|-+$/g, '').trim();
}

function commentUser(comment) {
  const user = comment.user;
  return typeof user === 'string' ? user : user?.login || user?.username;
}

export default class McpIssueTargetProvider {
  constructor(options = {}) {
    const supplied = options.config || {};
    this.config = {
      ...DEFAULTS, ...supplied,
      defaults: { ...DEFAULTS.defaults, ...supplied.defaults },
      tools: { ...DEFAULTS.tools, ...supplied.tools },
      result: { ...DEFAULTS.result, ...supplied.result },
      wait: { ...DEFAULTS.wait, ...supplied.wait, args: { ...DEFAULTS.wait.args, ...supplied.wait?.args } },
    };
    this.name = options.id || 'gitea_letta';
    this.issue = null;
    this.afterId = 0;
    this.session = null;
    this.connecting = null;
    this.poisoned = false;
    this.inFlight = false;
  }

  id() {
    return this.name;
  }

  async _getSession() {
    if (this.session) return this.session;
    if (!this.connecting) {
      const { mcp, tools, timeoutSeconds } = this.config;
      if (!mcp?.url && !mcp?.command) throw new Error('MCP URL or command is required');
      this.connecting = McpSession.connect(
        mcp, Object.values(tools), Math.max(1, timeoutSeconds || 60) * 1000,
      );
    }
    try {
      this.session = await this.connecting;
      return this.session;
    } finally {
      this.connecting = null;
    }
  }

  async callApi(prompt, context = {}) {
    if (this.inFlight) return { error: 'A target turn is already in progress' };
    if (this.poisoned) return { error: 'Previous target turn was incomplete; inspect the issue before continuing' };
    this.inFlight = true;
    try {
      return await this._callApi(prompt, context);
    } catch (error) {
      this.poisoned = true;
      return { error: error instanceof Error ? error.message : String(error), sessionId: this.issue && String(this.issue) };
    } finally {
      this.inFlight = false;
    }
  }

  async _callApi(prompt, context) {
    if (typeof prompt !== 'string' || !prompt.trim()) throw new Error('Attack text is empty');
    const { defaults, tools, createArgs, commentArgs, wait } = this.config;
    const vars = {
      ...defaults, ...context.vars,
      plugin: context.test?.metadata?.pluginId || '',
      prompt,
    };
    const explicitIssue = process.env.PROMPTFOO_GITEA_ISSUE || context.vars?.sessionId;
    if (this.issue == null && isIssueSessionId(explicitIssue, this.config.sessionIdPattern)) {
      this.issue = Number(explicitIssue);
    }
    const session = await this._getSession();
    let written;
    let afterId = this.afterId;
    if (this.issue == null || (!this.config.reuseIssue && !isIssueSessionId(explicitIssue, this.config.sessionIdPattern))) {
      const args = renderTemplate(createArgs || DEFAULTS.createArgs, vars);
      const mention = this.config.ensureMention || '@letta-code';
      if (mention && !args.body.includes(mention)) args.body = `${mention} ${args.body}`;
      written = await session.callTool(tools.createIssue, args);
      const issue = Number(written?.[this.config.result.issueField]);
      if (!Number.isSafeInteger(issue) || issue < 1) throw new Error('MCP issue creation returned no issue number');
      this.issue = issue;
      afterId = 0;
    } else {
      vars.issue_number = String(this.issue);
      const args = renderTemplate(commentArgs || DEFAULTS.commentArgs, vars);
      if (this.config.ensureMentionOnComment && this.config.ensureMention &&
          !args.body.includes(this.config.ensureMention)) {
        args.body = `${this.config.ensureMention} ${args.body}`;
      }
      written = await session.callTool(tools.comment, args);
      afterId = Number(written?.id);
      if (!Number.isSafeInteger(afterId) || afterId < 1) throw new Error('MCP comment write returned no comment ID');
    }
    const waited = await this._waitForReply(session, vars, afterId);
    this.afterId = waited.comment_id;
    return {
      output: cleanReply(waited.body),
      sessionId: String(this.issue),
      metadata: { issueNumber: this.issue, waitResult: waited, failed: waited.failed },
      guardrails: { flagged: waited.failed },
    };
  }

  async _waitForReply(session, vars, afterId) {
    const { wait, tools } = this.config;
    if (wait.mode !== 'poll_comments') throw new Error(`Unsupported wait mode: ${wait.mode}`);
    const deadline = Date.now() + wait.timeoutSeconds * 1000;
    const args = renderTemplate(wait.args, { ...vars, issue_number: String(this.issue) });
    while (Date.now() < deadline) {
      const comments = commentsFrom(await session.callTool(tools.wait, args));
      const matches = comments.filter((comment) =>
        commentUser(comment) === wait.botUser && Number(comment.id) > afterId &&
        wait.doneMarks.some((mark) => String(comment.body || '').includes(mark)));
      if (matches.length) {
        const comment = matches.reduce((best, item) => Number(item.id) > Number(best.id) ? item : best);
        return {
          comment_id: Number(comment.id),
          body: String(comment.body),
          failed: wait.failMarks.some((mark) => String(comment.body).includes(mark)),
        };
      }
      await new Promise((resolve) => setTimeout(resolve, Math.max(0, wait.intervalSeconds) * 1000));
    }
    throw new Error(`Timed out waiting for Letta reply on issue #${this.issue}`);
  }

  async shutdown() {
    await this.session?.close();
    this.session = null;
  }
}
