/** Two benign turns through the real MCP gateway and Letta runner. */
import assert from 'node:assert/strict';
import McpIssueTargetProvider from '../shared/js/mcp_target_provider.mjs';

const provider = new McpIssueTargetProvider({
  config: {
    reuseIssue: true,
    ensureMentionOnComment: false,
    mcp: { url: process.env.PROMPTFOO_MCP_URL || 'http://127.0.0.1:8080/mcp' },
    wait: {
      timeoutSeconds: 900,
      doneMarks: ['Letta Code finished', 'Letta Code failed', 'Letta Code encountered an error'],
      failMarks: ['Letta Code failed', 'Letta Code encountered an error'],
    },
  },
});

try {
  const first = await provider.callApi(
    '请阅读 expense-book/README.md，简要说明如何运行示例。不要修改文件。',
    { vars: { sessionId: 'live-smoke-first' } },
  );
  assert(!first.error, first.error);
  assert(!first.metadata.failed, 'First Letta run failed; inspect Gitea Actions logs');
  assert(first.output.trim(), 'First reply was empty');
  console.log(`First turn completed: issue #${first.sessionId}`);

  const second = await provider.callApi(
    '继续刚才的话题：请用一句话总结这个示例的用途，不要修改文件。',
    { vars: { sessionId: first.sessionId } },
  );
  assert(!second.error, second.error);
  assert(!second.metadata.failed, 'Follow-up Letta run failed; inspect Gitea Actions logs');
  assert.equal(second.sessionId, first.sessionId);
  assert(second.output.trim(), 'Follow-up reply was empty');
  assert(Number(second.metadata.waitResult.comment_id) > Number(first.metadata.waitResult.comment_id));
  console.log(`smoke_live: ok; two distinct bot replies on issue #${second.sessionId}`);
} finally {
  await provider.shutdown();
}
