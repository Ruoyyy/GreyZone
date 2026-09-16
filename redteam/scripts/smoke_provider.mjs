/**
 * Smoke: new attempt → create issue; follow-up → comment; sessionId = issue number.
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

// Unit: session / template helpers
assert(!isIssueSessionId('a1b2-c3d4'), 'uuid must not look like issue');
assert(isIssueSessionId('42'), 'numeric issue ok');
assert(
  renderTemplate('redteam-{{plugin}}', { plugin: 'hijacking' }) ===
    'redteam-hijacking',
  'template render',
);
assert(renderTemplate('{{prompt}}', { prompt: 'hi' }) === 'hi', 'prompt template');

const provider = new McpIssueTargetProvider({
  id: 'smoke-mock',
  config: {
    timeoutSeconds: 30,
    mcp: {
      command: process.execPath,
      args: [mockServer],
      cwd: path.resolve(__dirname, '..'),
    },
    tools: {
      createIssue: 'create_issue',
      comment: 'comment_on_issue',
      wait: 'wait_for_agent_reply',
    },
    createArgs: {
      title: 'redteam-{{plugin}}',
      body: '{{prompt}}',
    },
    commentArgs: {
      issue_number: '{{issue_number}}',
      body: '{{prompt}}',
    },
    waitArgs: {
      issue_number: '{{issue_number}}',
    },
    result: {
      outputField: 'reply',
      issueField: 'issue_number',
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
assert(turnB.sessionId === '2', 'new attempt must create issue 2');

await provider.shutdown();
console.log('smoke_provider: ok');
