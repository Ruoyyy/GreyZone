// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** node --test server/*.test.mjs — env parsing, static handler headers, proxy budgets/streaming (mocked upstream). */
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';
import { createAnthropicProxy, DEFAULT_LIMITS, limitsFromEnv } from './anthropic-proxy.mjs';
import { envInt, parseDotenv } from './env.mjs';
import { ISOLATION_HEADERS, contentTypeFor } from './headers.mjs';
import { createStaticHandler } from './static-site.mjs';

const SESSION = 'test-session-0001';

describe('env', () => {
  it('parses dotenv syntax', () => {
    const parsed = parseDotenv('# c\nA=1\nexport B="two\\nlines"\nC=\'x # y\'\nD=plain # trailing\n\nE=');
    assert.deepEqual(parsed, { A: '1', B: 'two\nlines', C: 'x # y', D: 'plain', E: '' });
  });
  it('validates integers', () => {
    assert.equal(envInt({ X: '12' }, 'X', 1), 12);
    assert.equal(envInt({}, 'X', 7), 7);
    assert.throws(() => envInt({ X: 'abc' }, 'X', 1));
  });
  it('reads proxy limits from the environment', () => {
    const limits = limitsFromEnv({ DEMO_PROXY_REQUESTS_PER_SESSION: '3', DEMO_PROXY_ALLOWED_MODELS: 'a, b' }, envInt);
    assert.equal(limits.requestsPerSession, 3);
    assert.equal(limits.maxTokensPerRequest, DEFAULT_LIMITS.maxTokensPerRequest);
    assert.deepEqual(limits.allowedModels, ['a', 'b']);
  });
});

describe('headers', () => {
  it('knows the MIME types the engines need', () => {
    assert.equal(contentTypeFor('.wasm'), 'application/wasm');
    assert.equal(contentTypeFor('.WHL'), 'application/zip');
    assert.equal(contentTypeFor('.unknown'), 'application/octet-stream');
  });
});

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`)));
}

describe('static site', () => {
  let server;
  let origin;
  before(async () => {
    const root = mkdtempSync(join(tmpdir(), 'bd-static-'));
    writeFileSync(join(root, 'index.html'), '<!doctype html><title>shell</title>');
    writeFileSync(join(root, 'service-worker.js'), '// sw');
    writeFileSync(join(root, 'engine.wasm'), Buffer.from([0, 0x61, 0x73, 0x6d]));
    const handler = createStaticHandler(root);
    server = createServer((req, res) => {
      if (!handler.handle(req, res, new URL(req.url, 'http://x').pathname)) {
        res.writeHead(404);
        res.end();
      }
    });
    origin = await listen(server);
  });
  after(() => server.close());

  it('serves files with isolation headers and MIME types', async () => {
    const res = await fetch(`${origin}/engine.wasm`);
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('content-type'), 'application/wasm');
    for (const [key, value] of Object.entries(ISOLATION_HEADERS)) assert.equal(res.headers.get(key), value);
  });
  it('allows the service worker to claim the root scope', async () => {
    const res = await fetch(`${origin}/service-worker.js`);
    assert.equal(res.headers.get('service-worker-allowed'), '/');
  });
  it('falls back to the shell for Shopware routes but not for missing files', async () => {
    const page = await fetch(`${origin}/checkout/cart`, { headers: { accept: 'text/html' } });
    assert.equal(page.status, 200);
    assert.match(await page.text(), /shell/);
    const php = await fetch(`${origin}/index.php/admin`, { headers: { accept: 'text/html' } });
    assert.equal(php.status, 200);
    const missing = await fetch(`${origin}/demo/nope.js`, { headers: { accept: 'text/html' } });
    assert.equal(missing.status, 404);
  });
  it('rejects path traversal', async () => {
    const res = await fetch(`${origin}/..%2f..%2fetc%2fpasswd`);
    assert.equal(res.status, 404);
  });
});

describe('anthropic proxy', () => {
  let upstream;
  let upstreamOrigin;
  let seenRequests;
  let proxyServer;
  let proxyOrigin;
  let proxy;

  before(async () => {
    seenRequests = [];
    upstream = createServer(async (req, res) => {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      seenRequests.push({ url: req.url, headers: req.headers, body: JSON.parse(Buffer.concat(chunks).toString()) });
      res.writeHead(200, { 'content-type': 'text/event-stream', 'request-id': 'req_test' });
      res.write('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":10,"output_tokens":1}}}\n\n');
      await new Promise((resolve) => setTimeout(resolve, 20));
      res.write('event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":42}}\n\n');
      res.end('event: message_stop\ndata: {"type":"message_stop"}\n\n');
    });
    upstreamOrigin = await listen(upstream);
    proxy = createAnthropicProxy({ apiKey: 'sk-ant-test', workspaceId: 'wrkspc_test', limits: { requestsPerSession: 2, outputTokensPerSession: 1000 }, upstreamUrl: upstreamOrigin });
    proxyServer = createServer((req, res) => {
      proxy.handle(req, res, new URL(req.url, 'http://x').pathname).then((handled) => {
        if (!handled) {
          res.writeHead(404);
          res.end();
        }
      });
    });
    proxyOrigin = await listen(proxyServer);
  });
  after(() => {
    proxy.close();
    proxyServer.close();
    upstream.close();
  });

  it('reports its status', async () => {
    const res = await fetch(`${proxyOrigin}/api/anthropic/status`);
    const body = await res.json();
    assert.equal(body.configured, true);
    assert.equal(body.limits.requestsPerSession, 2);
  });
  it('rejects unknown endpoints and missing sessions', async () => {
    assert.equal((await fetch(`${proxyOrigin}/api/anthropic/v1/models`)).status, 404);
    const noSession = await fetch(`${proxyOrigin}/api/anthropic/v1/messages`, { method: 'POST', body: '{}' });
    assert.equal(noSession.status, 400);
  });
  it('injects the key, clamps max_tokens, streams and accounts usage', async () => {
    const res = await fetch(`${proxyOrigin}/api/anthropic/v1/messages`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-demo-session': SESSION, 'x-api-key': 'leaked?', 'anthropic-version': '2023-06-01' },
      body: JSON.stringify({ model: 'claude-test', max_tokens: 999999, stream: true, messages: [] }),
    });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('content-type'), 'text/event-stream');
    assert.equal(res.headers.get('request-id'), 'req_test');
    const text = await res.text();
    assert.match(text, /message_stop/);
    assert.equal(seenRequests.length, 1);
    assert.equal(seenRequests[0].headers['x-api-key'], 'sk-ant-test');
    assert.equal(seenRequests[0].headers['anthropic-workspace-id'], 'wrkspc_test');
    assert.equal(seenRequests[0].headers['x-demo-session'], undefined);
    assert.equal(seenRequests[0].body.max_tokens, DEFAULT_LIMITS.maxTokensPerRequest);
    const status = await (await fetch(`${proxyOrigin}/api/anthropic/status`, { headers: { 'x-demo-session': SESSION } })).json();
    assert.equal(status.remaining.requests, 1);
    assert.equal(status.remaining.outputTokens, 1000 - 42);
  });
  it('enforces the per-session request budget', async () => {
    const send = () =>
      fetch(`${proxyOrigin}/api/anthropic/v1/messages`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-demo-session': SESSION },
        body: JSON.stringify({ model: 'claude-test', max_tokens: 10, messages: [] }),
      });
    assert.equal((await send()).status, 200);
    const denied = await send();
    assert.equal(denied.status, 429);
    const body = await denied.json();
    assert.equal(body.error.type, 'rate_limit_error');
    // another session is unaffected
    const other = await fetch(`${proxyOrigin}/api/anthropic/v1/messages`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-demo-session': 'other-session-01' },
      body: JSON.stringify({ model: 'claude-test', max_tokens: 10, messages: [] }),
    });
    assert.equal(other.status, 200);
  });
  it('answers 503 without a key', async () => {
    const unconfigured = createAnthropicProxy({ apiKey: '' });
    const server = createServer((req, res) => void unconfigured.handle(req, res, new URL(req.url, 'http://x').pathname));
    const origin = await listen(server);
    try {
      const res = await fetch(`${origin}/api/anthropic/v1/messages`, { method: 'POST', headers: { 'x-demo-session': SESSION }, body: '{}' });
      assert.equal(res.status, 503);
      assert.equal((await fetch(`${origin}/api/anthropic/status`).then((r) => r.json())).configured, false);
    } finally {
      unconfigured.close();
      server.close();
    }
  });
});
