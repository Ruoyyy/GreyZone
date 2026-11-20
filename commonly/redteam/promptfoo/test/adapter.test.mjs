import { test } from 'node:test';
import assert from 'node:assert/strict';
import Provider from '../target_provider.mjs';

test('missing or branched session is rejected without dispatch', async () => {
  const target = new Provider();
  assert.ok((await target.callApi('hello')).error);
  target.sessionId = 'one';
  assert.ok((await target.callApi('hello', { vars: { sessionId: 'two' } })).error);
});
test('stopped candidate never sends another turn', async () => {
  const target = new Provider();
  target.stopped = true;
  assert.ok((await target.callApi('hello', { vars: { sessionId: 'one' } })).error);
});
test('deleted channel ends the conversation without claiming Oracle success or dispatching again', async () => {
  let calls = 0;
  const target = new Provider({}, () => {
    calls += 1;
    return { status: 0, stdout: JSON.stringify({ status: 'continue', reply: 'channel deleted',
      oracleSuccess: false, conversationEnded: true, stopReason: 'shared-pod-deleted' }) };
  });
  const response = await target.callApi('delete', { vars: { sessionId: 'one' } });
  assert.equal(response.conversationEnded, true);
  assert.equal(response.conversationEndReason, 'shared-pod-deleted');
  assert.equal(response.metadata.oracleSuccess, false);
  assert.equal(response.error, undefined);
  assert.ok((await target.callApi('again', { vars: { sessionId: 'one' } })).error);
  assert.equal(calls, 1);
});
