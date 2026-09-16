/**
 * Promptfoo custom target: map each redteam attempt to one Gitea issue via MCP.
 *
 * Real gitea-mcp tools (default):
 *   - issue_write method=create | add_comment
 *   - issue_read  method=get_comments (polled until bot finishes)
 *
 * - New attempt → create issue (body = prompt)
 * - Follow-up → add_comment on same issue
 * - sessionId = issue number
 */
import { McpSession } from './mcp_session.mjs';

function getByPath(obj, path) {
  if (obj == null) {
    return undefined;
  }
  if (!path) {
    return obj;
  }
  if (typeof obj === 'string' || typeof obj === 'number') {
    return path === '' ? obj : undefined;
  }
  const parts = String(path).split('.').filter(Boolean);
  let cur = obj;
  for (const p of parts) {
    if (cur == null) {
      return undefined;
    }
    cur = cur[p];
  }
  return cur;
}

/**
 * Render templates like '{{prompt}}' / 'redteam-{{plugin}}'.
 * Whole-string placeholders keep non-string types (e.g. issue_number as number).
 */
export function renderTemplate(value, vars) {
  if (value == null) {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((v) => renderTemplate(v, vars));
  }
  if (typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = renderTemplate(v, vars);
    }
    return out;
  }
  if (typeof value !== 'string') {
    return value;
  }
  const whole = value.match(/^\{\{\s*([\w.]+)\s*\}\}$/);
  if (whole) {
    const key = whole[1];
    if (Object.prototype.hasOwnProperty.call(vars, key)) {
      return vars[key];
    }
    const fromPath = getByPath(vars, key);
    return fromPath === undefined ? '' : fromPath;
  }
  return value.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_, key) => {
    if (Object.prototype.hasOwnProperty.call(vars, key)) {
      const v = vars[key];
      return v == null ? '' : String(v);
    }
    const fromPath = getByPath(vars, key);
    return fromPath == null ? '' : String(fromPath);
  });
}

export function isIssueSessionId(sessionId, pattern = '^[0-9]+$') {
  if (sessionId == null || sessionId === '') {
    return false;
  }
  return new RegExp(pattern).test(String(sessionId));
}

function ensureMention(body, mention) {
  if (!mention) {
    return body;
  }
  const text = body == null ? '' : String(body);
  if (text.includes(mention)) {
    return text;
  }
  return `${mention} ${text}`.trim();
}

/** Match issue-chat.py clean_body for readable grader output. */
export function cleanBotBody(body) {
  let text = String(body ?? '');
  text = text.replace(/<!-- letta-metadata[\s\S]*?-->/g, '');
  const lines = text.split('\n');
  if (lines[0]?.startsWith('**Letta Code')) {
    lines.shift();
  }
  text = lines.join('\n');
  text = text.split('\n🤖')[0];
  return text.trim().replace(/^-+|-+$/g, '').trim();
}

function commentUser(comment) {
  if (typeof comment?.user === 'string') {
    return comment.user;
  }
  return comment?.user?.login || comment?.user?.username || '';
}

function asCommentList(raw) {
  if (Array.isArray(raw)) {
    return raw;
  }
  if (Array.isArray(raw?.comments)) {
    return raw.comments;
  }
  if (raw && typeof raw === 'object' && raw.id != null && raw.body != null) {
    return [raw];
  }
  return [];
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default class McpIssueTargetProvider {
  /**
   * @param {{ id?: string, config?: Record<string, unknown> }} options
   */
  constructor(options = {}) {
    this.providerId = options.id || 'mcp-issue-target';
    this.config = options.config || {};
    this._session = null;
    this._sharedIssueNumber =
      this.config.reuseIssue &&
      isIssueSessionId(process.env.PROMPTFOO_GITEA_ISSUE)
        ? String(process.env.PROMPTFOO_GITEA_ISSUE)
        : null;
    /** @type {Map<string, number>} */
    this._lastBotCommentId = new Map();
  }

  id() {
    return this.providerId;
  }

  async _getSession() {
    if (this._session) {
      return this._session;
    }
    const mcp = this.config.mcp || {};
    const timeoutSeconds = this.config.timeoutSeconds ?? 900;
    this._session = new McpSession({
      ...mcp,
      timeoutMs: mcp.timeoutMs ?? timeoutSeconds * 1000,
    });
    await this._session.start();
    return this._session;
  }

  _toolNames() {
    const tools = this.config.tools || {};
    return {
      createIssue: tools.createIssue || 'issue_write',
      comment: tools.comment || 'issue_write',
      wait: tools.wait || 'issue_read',
    };
  }

  _buildVars(prompt, context, extra = {}) {
    const plugin =
      context?.test?.metadata?.pluginId ||
      context?.vars?.pluginId ||
      'probe';
    const defaults = this.config.defaults || {};
    return {
      owner: defaults.owner || 'letta-admin',
      repo: defaults.repo || 'AgentThreatDataset',
      prompt,
      sessionId: context?.vars?.sessionId,
      plugin,
      pluginId: plugin,
      ...context?.vars,
      ...extra,
    };
  }

  /**
   * Wait for Letta bot reply. Real MCP has no wait tool — poll issue_read get_comments.
   */
  async _waitForReply(session, issueNumber, vars) {
    const cfg = this.config;
    const waitCfg = cfg.wait || {};
    const tools = this._toolNames();
    const timeoutSeconds =
      waitCfg.timeoutSeconds ?? cfg.timeoutSeconds ?? 900;
    const timeoutMs = timeoutSeconds * 1000;

    if (waitCfg.mode === 'tool' || waitCfg.mode === 'single') {
      const waitArgs = renderTemplate(
        waitCfg.args ||
          cfg.waitArgs || {
            issue_number: '{{issue_number}}',
          },
        vars,
      );
      return session.callTool(waitCfg.tool || tools.wait, waitArgs, {
        timeoutMs,
      });
    }

    // Default: poll_comments (gitea-mcp issue_read)
    const intervalMs = (waitCfg.intervalSeconds ?? 5) * 1000;
    const botUser = waitCfg.botUser || 'gitea-actions';
    const doneMarks = waitCfg.doneMarks || [
      'Letta Code finished',
      'Letta Code failed',
    ];
    const failMarks = waitCfg.failMarks || [
      waitCfg.failMark || 'Letta Code failed',
    ];
    const afterId = this._lastBotCommentId.get(String(issueNumber)) || 0;
    const readArgsTemplate = waitCfg.args || {
      method: 'get_comments',
      owner: '{{owner}}',
      repo: '{{repo}}',
      issue_number: '{{issue_number}}',
    };

    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const args = renderTemplate(readArgsTemplate, vars);
      const raw = await session.callTool(waitCfg.tool || tools.wait, args, {
        timeoutMs,
      });
      const comments = asCommentList(raw);
      const matches = comments.filter((c) => {
        const user = commentUser(c);
        const body = String(c.body || '');
        const id = Number(c.id) || 0;
        return (
          user === botUser &&
          id > afterId &&
          doneMarks.some((m) => body.includes(m))
        );
      });
      if (matches.length > 0) {
        const c = matches[matches.length - 1];
        this._lastBotCommentId.set(String(issueNumber), Number(c.id) || 0);
        const body = String(c.body || '');
        return {
          reply: cleanBotBody(body),
          failed: failMarks.some((m) => body.includes(m)),
          comment_id: c.id,
          raw_body: body,
        };
      }
      await sleep(intervalMs);
    }
    throw new Error(
      `timeout after ${timeoutSeconds}s waiting for bot reply on issue #${issueNumber}`,
    );
  }

  async callApi(prompt, context /*, options */) {
    const cfg = this.config;
    const pattern = cfg.sessionIdPattern || '^[0-9]+$';
    const mention = Object.prototype.hasOwnProperty.call(cfg, 'ensureMention')
      ? cfg.ensureMention
      : '@letta-code';
    const resultCfg = cfg.result || {};
    const issueField = resultCfg.issueField || 'number';
    const outputField = resultCfg.outputField || 'reply';
    const tools = this._toolNames();
    const timeoutSeconds = cfg.timeoutSeconds ?? 900;
    const defaults = cfg.defaults || {};
    const owner = defaults.owner || 'letta-admin';
    const repo = defaults.repo || 'AgentThreatDataset';

    const incomingSession =
      this.config.reuseIssue && this._sharedIssueNumber
        ? this._sharedIssueNumber
        : context?.vars?.sessionId;
    const continuing = isIssueSessionId(incomingSession, pattern);

    try {
      const session = await this._getSession();
      let issueNumber;

      if (continuing) {
        issueNumber = String(incomingSession);
        const vars = this._buildVars(prompt, context, {
          sessionId: issueNumber,
          issue_number: Number(issueNumber),
        });
        const commentArgs = renderTemplate(
          cfg.commentArgs || {
            method: 'add_comment',
            owner: '{{owner}}',
            repo: '{{repo}}',
            issue_number: '{{issue_number}}',
            body: '{{prompt}}',
          },
          vars,
        );
        // Comments: real MCP auto-prefixes @letta-code; still OK if we also prepend.
        if (commentArgs.body != null && mention) {
          // Prefer leaving mention to MCP for comments when ensureMentionOnComment is false
          const onComment = cfg.ensureMentionOnComment ?? false;
          if (onComment) {
            commentArgs.body = ensureMention(commentArgs.body, mention);
          }
        }
        await session.callTool(tools.comment, commentArgs, {
          timeoutMs: timeoutSeconds * 1000,
        });
      } else {
        const vars = this._buildVars(prompt, context);
        const createArgs = renderTemplate(
          cfg.createArgs || {
            method: 'create',
            owner: '{{owner}}',
            repo: '{{repo}}',
            title: 'redteam-{{plugin}}',
            body: '{{prompt}}',
          },
          vars,
        );
        // Create: MCP does NOT auto-prefix — must include @letta-code in body.
        if (createArgs.body != null) {
          createArgs.body = ensureMention(createArgs.body, mention);
        }
        const created = await session.callTool(tools.createIssue, createArgs, {
          timeoutMs: timeoutSeconds * 1000,
        });
        const rawIssue = getByPath(created, issueField);
        if (rawIssue == null || rawIssue === '') {
          return {
            error: `issue create did not return ${issueField}: ${JSON.stringify(created)}`,
          };
        }
        issueNumber = String(rawIssue);
        if (cfg.reuseIssue) {
          this._sharedIssueNumber = issueNumber;
        }
      }

      const waitVars = this._buildVars(prompt, context, {
        sessionId: issueNumber,
        issue_number: Number(issueNumber),
        owner,
        repo,
      });
      const waited = await this._waitForReply(session, issueNumber, waitVars);

      let output = getByPath(waited, outputField);
      if (output == null) {
        output =
          typeof waited === 'string'
            ? waited
            : waited?.text ?? JSON.stringify(waited);
      }
      if (typeof output !== 'string') {
        output = JSON.stringify(output);
      }

      return {
        output,
        sessionId: String(issueNumber),
        metadata: {
          issueNumber: String(issueNumber),
          failed: Boolean(waited?.failed),
          waitResult: waited,
        },
      };
    } catch (err) {
      return {
        error: err?.message || String(err),
      };
    }
  }

  async shutdown() {
    if (this._session) {
      await this._session.close();
      this._session = null;
    }
  }
}
