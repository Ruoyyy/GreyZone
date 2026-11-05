/**
 * Smoke against mock gitea-mcp tool shapes (issue_write / issue_read).
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import McpIssueTargetProvider, {
  isIssueSessionId,
  renderTemplate,
} from '../shared/js/mcp_target_provider.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const mockServer = path.resolve(__dirname, '../fixtures/mock_issue_mcp.mjs');

function assert(cond, msg) {
  if (!cond) {
    throw new Error(msg);
  }
}

assert(!isIssueSessionId('a1b2-c3d4'), 'uuid must not look like issue');
assert(isIssueSessionId('42'), 'numeric issue ok');
assert(
  renderTemplate('redteam-{{plugin}}', { plugin: 'hijacking' }) ===
    'redteam-hijacking',
  'template render',
);

const provider = new McpIssueTargetProvider({
  id: 'smoke-mock',
  config: {
    timeoutSeconds: 30,
    reuseIssue: true,
    defaults: {
      owner: 'letta-admin',
      repo: 'GreyZone',
    },
    mcp: {
      command: process.execPath,
      args: [mockServer],
      cwd: path.resolve(__dirname, '..'),
    },
    tools: {
      createIssue: 'issue_write',
      comment: 'issue_write',
      wait: 'issue_read',
    },
    createArgs: {
      method: 'create',
      owner: '{{owner}}',
      repo: '{{repo}}',
      title: 'redteam-{{plugin}}',
      body: '{{prompt}}',
    },
    commentArgs: {
      method: 'add_comment',
      owner: '{{owner}}',
      repo: '{{repo}}',
      issue_number: '{{issue_number}}',
      body: '{{prompt}}',
    },
    result: {
      issueField: 'number',
      outputField: 'reply',
    },
    wait: {
      mode: 'poll_comments',
      intervalSeconds: 0.05,
      timeoutSeconds: 5,
      botUser: 'gitea-actions',
      args: {
        method: 'get_comments',
        owner: '{{owner}}',
        repo: '{{repo}}',
        issue_number: '{{issue_number}}',
      },
    },
    ensureMention: '@letta-code',
  },
});

const turn1 = await provider.callApi('first attack', {
  vars: { sessionId: 'not-an-issue-uuid' },
  test: { metadata: { pluginId: 'hijacking' } },
});

assert(!turn1.error, `turn1 error: ${turn1.error}`);
assert(turn1.sessionId === '1', `expected sessionId 1, got ${turn1.sessionId}`);
assert(
  String(turn1.output).includes('first attack'),
  `unexpected output: ${turn1.output}`,
);
assert(
  String(turn1.output).includes('mock-agent-reply'),
  'expected mock reply prefix',
);

const turn2 = await provider.callApi('follow-up attack', {
  vars: { sessionId: turn1.sessionId },
  test: { metadata: { pluginId: 'hijacking' } },
});

assert(!turn2.error, `turn2 error: ${turn2.error}`);
assert(turn2.sessionId === '1', 'follow-up must keep same issue');
assert(
  String(turn2.output).includes('follow-up attack'),
  `unexpected turn2 output: ${turn2.output}`,
);

const turnB = await provider.callApi('second attempt', {
  vars: { sessionId: 'another-uuid' },
  test: { metadata: { pluginId: 'probe' } },
});
assert(!turnB.error, `turnB error: ${turnB.error}`);
assert(turnB.sessionId === '1', 'reuseIssue must keep every attempt on issue 1');

await provider.shutdown();

// A new process continuing an existing issue must ignore its old completion.
const resumed = new McpIssueTargetProvider({ config: {
  wait: { intervalSeconds: 0, timeoutSeconds: 2 },
} });
let polls = 0;
resumed._getSession = async () => ({
  async callTool(name) {
    if (name === 'issue_write') return { id: 20 };
    polls++;
    const old = { id: 10, user: 'gitea-actions', body: '**Letta Code finished**\nold reply' };
    const fresh = { id: 21, user: 'gitea-actions', body: '**Letta Code finished**\nnew reply' };
    return polls === 1 ? [old] : [old, fresh];
  },
});
const resumedTurn = await resumed.callApi('next turn', { vars: { sessionId: '42' } });
assert(!resumedTurn.error, `resumed error: ${resumedTurn.error}`);
assert(resumedTurn.output === 'new reply', 'resumed turn must not return a historical reply');
assert(polls === 2, 'must wait past old completion');
console.log('smoke_provider: ok');
