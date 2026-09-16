/**
 * Promptfoo custom target: map each redteam attempt to one Gitea issue via MCP.
 *
 * - New attempt (sessionId not an issue number) → create_issue(body=prompt) → wait
 * - Same attempt follow-up → comment_on_issue → wait
 * - Returns { output: agent reply, sessionId: issue number }
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
 * Unknown keys become empty string.
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
  // Whole-string placeholder → preserve non-string types from vars
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

export default class McpIssueTargetProvider {
  /**
   * @param {{ id?: string, config?: Record<string, unknown> }} options
   */
  constructor(options = {}) {
    this.providerId = options.id || 'mcp-issue-target';
    this.config = options.config || {};
    this._session = null;
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
      timeoutMs: (mcp.timeoutMs ?? timeoutSeconds * 1000),
    });
    await this._session.start();
    return this._session;
  }

  _toolNames() {
    const tools = this.config.tools || {};
    return {
      createIssue: tools.createIssue || 'create_issue',
      comment: tools.comment || 'comment_on_issue',
      wait: tools.wait || 'wait_for_agent_reply',
    };
  }

  _buildVars(prompt, context, extra = {}) {
    const plugin =
      context?.test?.metadata?.pluginId ||
      context?.vars?.pluginId ||
      'probe';
    return {
      prompt,
      sessionId: context?.vars?.sessionId,
      plugin,
      pluginId: plugin,
      ...context?.vars,
      ...extra,
    };
  }

  async callApi(prompt, context /*, options */) {
    const cfg = this.config;
    const pattern = cfg.sessionIdPattern || '^[0-9]+$';
    const mention =
      Object.prototype.hasOwnProperty.call(cfg, 'ensureMention')
        ? cfg.ensureMention
        : '@letta-code';
    const resultCfg = cfg.result || {};
    const issueField = resultCfg.issueField || 'issue_number';
    const outputField = resultCfg.outputField || 'reply';
    const tools = this._toolNames();
    const timeoutSeconds = cfg.timeoutSeconds ?? 900;

    const incomingSession = context?.vars?.sessionId;
    const continuing = isIssueSessionId(incomingSession, pattern);

    try {
      const session = await this._getSession();
      let issueNumber;

      if (continuing) {
        issueNumber = String(incomingSession);
        const vars = this._buildVars(prompt, context, {
          sessionId: issueNumber,
          issue_number: Number(issueNumber) || issueNumber,
        });
        const commentArgs = renderTemplate(
          cfg.commentArgs || {
            issue_number: '{{issue_number}}',
            body: '{{prompt}}',
          },
          vars,
        );
        if (commentArgs.body != null) {
          commentArgs.body = ensureMention(commentArgs.body, mention);
        }
        await session.callTool(tools.comment, commentArgs, {
          timeoutMs: timeoutSeconds * 1000,
        });
      } else {
        const vars = this._buildVars(prompt, context);
        const createArgs = renderTemplate(
          cfg.createArgs || {
            title: 'redteam-{{plugin}}',
            body: '{{prompt}}',
          },
          vars,
        );
        if (createArgs.body != null) {
          createArgs.body = ensureMention(createArgs.body, mention);
        }
        const created = await session.callTool(tools.createIssue, createArgs, {
          timeoutMs: timeoutSeconds * 1000,
        });
        const rawIssue = getByPath(created, issueField);
        if (rawIssue == null || rawIssue === '') {
          return {
            error: `create_issue did not return ${issueField}: ${JSON.stringify(created)}`,
          };
        }
        issueNumber = String(rawIssue);
      }

      const waitVars = this._buildVars(prompt, context, {
        sessionId: issueNumber,
        issue_number: Number(issueNumber) || issueNumber,
      });
      const waitArgs = renderTemplate(
        cfg.waitArgs || {
          issue_number: '{{issue_number}}',
        },
        waitVars,
      );
      const waited = await session.callTool(tools.wait, waitArgs, {
        timeoutMs: timeoutSeconds * 1000,
      });

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

      const failed = Boolean(waited?.failed);
      return {
        output,
        sessionId: String(issueNumber),
        metadata: {
          issueNumber: String(issueNumber),
          failed,
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
