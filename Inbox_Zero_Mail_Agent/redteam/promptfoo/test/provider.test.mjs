import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import MailTargetProvider from '../mail_target_provider.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const mock = path.resolve(here, '../fixtures/mock_mail_mcp.mjs');

test('provider continues a mail thread and ignores mail to other recipients', async () => {
  process.env.TRANSPORT_TOKEN = 'test-only-token';
  const provider = new MailTargetProvider({ config: {
    attackerEmail: 'ruoyu@gmail.com',
    managedEmail: 'admin@gmail.com',
    subject: 'Account question',
    replyTimeoutSeconds: 1,
    pollIntervalSeconds: 0.05,
    mcp: { command: process.execPath, args: [mock], cwd: path.dirname(mock) },
  } });
  try {
    const first = await provider.callApi('first question', { vars: { sessionId: 'case-1' } });
    assert.equal(first.output, 'reply 1');
    assert.equal(first.metadata.replyIndex, 2);
    const second = await provider.callApi('second question', { vars: { sessionId: first.sessionId } });
    assert.equal(second.output, 'reply 2');
    assert.equal(second.sessionId, 'case-1');
    const silent = await provider.callApi('silent', { vars: { sessionId: 'case-2' } });
    assert.equal(silent.metadata.status, 'no-reply');
    const retry = await provider.callApi('try again', { vars: { sessionId: 'case-2' } });
    assert.match(retry.error, /cannot continue/);
  } finally {
    await provider.client?.close();
    delete process.env.TRANSPORT_TOKEN;
  }
});
