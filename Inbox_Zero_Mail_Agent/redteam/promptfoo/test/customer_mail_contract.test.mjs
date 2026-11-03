import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { appendFile, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const here = path.dirname(fileURLToPath(import.meta.url));
const environmentRoot = path.resolve(here, '../../..');
const appRoot = path.join(environmentRoot, 'apps', 'web');

test('customer-mail MCP exposes structured inbox and full message fields', async () => {
  const outbox = await mkdtemp(path.join(tmpdir(), 'customer-mcp-'));
  const deliveryId = 'sample-delivery';
  await writeFile(path.join(outbox, `${deliveryId}.eml`), [
    'From: admin@gmail.com',
    'To: alice@gmail.com',
    'Subject: Re: Project question',
    'Message-ID: <reply@managed.local>',
    'In-Reply-To: <sent@customer.local>',
    'Date: Tue, 01 Sep 2026 00:00:00 GMT',
    '',
    'The answer is ready.',
  ].join('\r\n'));
  await appendFile(path.join(outbox, 'log.jsonl'), `${JSON.stringify({
    deliveryId,
    messageId: 'internal-message',
    threadId: 'thread-1',
    envelope: { from: 'admin@gmail.com', recipients: ['alice@gmail.com'] },
    receivedAt: '2026-09-01T00:00:00.000Z',
  })}\n`);

  const client = new Client({ name: 'customer-mail-contract', version: '0.1.0' });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ['--import', 'tsx', path.join(appRoot, 'scripts', 'customer-mail-mcp.ts')],
    cwd: appRoot,
    env: { PATH: process.env.PATH || '', CUSTOMER_OUTBOX: outbox, TRANSPORT_TOKEN: 'test-only-token' },
    stderr: 'pipe',
  });
  try {
    await client.connect(transport);
    const list = await client.callTool({ name: 'list_received', arguments: {} });
    assert.equal(list.structuredContent.messages[0].recipients[0], 'alice@gmail.com');
    assert.equal(list.structuredContent.messages[0].threadId, 'thread-1');
    const read = await client.callTool({ name: 'read_received', arguments: { index: 1 } });
    assert.equal(read.structuredContent.inReplyTo, '<sent@customer.local>');
    assert.equal(read.structuredContent.body, 'The answer is ready.');
  } finally {
    await client.close();
    await rm(outbox, { recursive: true, force: true });
  }
});
