/**
 * User MCP — boots only as a human, and each tool is the web app's own call.
 *
 * Wire-shape tests patch the global fetch the client uses (same seam as
 * tools.test.mjs) and assert method, path, body and the bearer — the bearer
 * being the user's token is the whole "connect = that person" guarantee.
 */
import { jest } from '@jest/globals';
import { buildUserTools } from '../src/user-tools.js';
import { loadUserConfig } from '../src/client.js';

const TOKEN = 'cm_userpersonaltoken';
const cfg = { baseUrl: 'http://backend:5000', token: TOKEN };
const byName = Object.fromEntries(buildUserTools(cfg).map((t) => [t.name, t]));

const json = (body, status = 200) => ({
  ok: status < 400, status, text: async () => JSON.stringify(body),
});
const installFetch = (handler) => {
  const fn = jest.fn(handler);
  global.fetch = fn;
  return fn;
};
const parse = (result) => JSON.parse(result.content[0].text);
afterEach(() => { delete global.fetch; });

describe('loadUserConfig — the process can only start as a human', () => {
  const base = { COMMONLY_API_URL: 'http://backend:5000/' };

  it('accepts a cm_ user token and trims the base URL', () => {
    expect(loadUserConfig({ ...base, COMMONLY_USER_TOKEN: TOKEN }))
      .toEqual({ baseUrl: 'http://backend:5000', token: TOKEN });
  });

  it('prefers the token file and trims it', () => {
    const readImpl = jest.fn(() => `${TOKEN}\n`);
    const config = loadUserConfig(
      { ...base, COMMONLY_USER_TOKEN_FILE: '/run/mcp/user_token', COMMONLY_USER_TOKEN: 'cm_other' },
      { readImpl },
    );
    expect(config.token).toBe(TOKEN);
    expect(readImpl).toHaveBeenCalledWith('/run/mcp/user_token', 'utf8');
  });

  it.each([
    ['an agent runtime token', 'cm_agent_abc'],
    ['a daemon token', 'cm_daemon_abc'],
    ['a browser JWT', 'eyJhbGciOiJIUzI1NiJ9.x.y'],
  ])('refuses %s', (_label, token) => {
    expect(() => loadUserConfig({ ...base, COMMONLY_USER_TOKEN: token })).toThrow(/human cm_ token/);
  });

  it('refuses to start with no token or no API URL', () => {
    expect(() => loadUserConfig(base)).toThrow(/No user token/);
    expect(() => loadUserConfig({ COMMONLY_USER_TOKEN: TOKEN })).toThrow(/COMMONLY_API_URL/);
  });
});

describe('tool surface', () => {
  it('ships exactly the ten user tools, none named like an agent tool', () => {
    expect(Object.keys(byName).sort()).toEqual([
      'user_dm_agent', 'user_get_pod', 'user_list_files', 'user_list_pod_agents', 'user_list_pods',
      'user_react', 'user_read_file', 'user_read_messages', 'user_send_message', 'user_upload_file',
    ]);
  });
});

describe('each tool is the web app call, made with the user token', () => {
  it.each([
    ['user_get_pod', { podId: 'p1' }, 'GET', '/api/pods/p1'],
    ['user_read_messages', { podId: 'p1', limit: 20, before: '2026-10-05T00:00:00Z' }, 'GET',
      '/api/messages/p1?limit=20&before=2026-10-05T00%3A00%3A00Z'],
    ['user_list_files', { podId: 'p1' }, 'GET', '/api/pods/p1/files'],
    ['user_list_pod_agents', { podId: 'p1' }, 'GET', '/api/registry/pods/p1/agents'],
    ['user_react', { messageId: 'm1', emoji: '👍' }, 'POST', '/api/messages/m1/reactions'],
    ['user_react', { messageId: 'm1', emoji: '👍', remove: true }, 'DELETE',
      `/api/messages/m1/reactions/${encodeURIComponent('👍')}`],
  ])('%s → %s', async (name, args, method, path) => {
    const fetchFn = installFetch(async () => json({}));
    await byName[name].call(args);
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe(`http://backend:5000${path}`);
    expect(init.method).toBe(method);
    expect(init.headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it('user_send_message posts content, reply and thread fields', async () => {
    const fetchFn = installFetch(async () => json({ _id: 'm9' }));
    await byName.user_send_message.call({
      podId: 'p1', content: '@scout hi', replyToMessageId: 'm1', threadRootId: 'm0',
    });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe('http://backend:5000/api/messages/p1');
    expect(JSON.parse(init.body)).toEqual({ content: '@scout hi', replyToMessageId: 'm1', threadRootId: 'm0' });
  });

  it('user_list_pods compacts the pod list', async () => {
    installFetch(async () => json([{ _id: 'p1', name: 'Pod', type: 'team', members: ['a', 'b'] }]));
    expect(parse(await byName.user_list_pods.call({}))).toEqual([
      { podId: 'p1', name: 'Pod', type: 'team', description: '', memberCount: 2 },
    ]);
  });

  it('user_dm_agent opens the room and returns its podId', async () => {
    const fetchFn = installFetch(async () => json({ room: { _id: 'dm1', name: 'Scout', type: 'agent-room' } }));
    const result = parse(await byName.user_dm_agent.call({ agentName: 'scout', instanceId: 'u1' }));
    expect(fetchFn.mock.calls[0][0]).toBe('http://backend:5000/api/agents/runtime/room');
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ agentName: 'scout', instanceId: 'u1' });
    expect(result).toEqual({ podId: 'dm1', name: 'Scout', type: 'agent-room' });
  });

  it('surfaces a refusal with its status and the backend body', async () => {
    installFetch(async () => json({ msg: 'Only pod members can upload files to this pod' }, 403));
    const result = await byName.user_get_pod.call({ podId: 'not-mine' });
    expect(result.isError).toBe(true);
    expect(parse(result)).toMatchObject({ status: 403, body: { msg: expect.stringMatching(/members/) } });
  });
});

describe('user_upload_file', () => {
  it('uploads the content to the pod, then posts the web composer file card', async () => {
    const fetchFn = installFetch(async (url) => (url.endsWith('/api/uploads')
      ? json({ fileName: '123-1.md', originalName: 'notes.md', size: 5, kind: 'document', podId: 'p1' })
      : json({ _id: 'm2' })));
    await byName.user_upload_file.call({ podId: 'p1', fileName: 'notes.md', text: 'hello', message: 'see' });

    const [uploadUrl, uploadInit] = fetchFn.mock.calls[0];
    expect(uploadUrl).toBe('http://backend:5000/api/uploads');
    const form = uploadInit.body;
    expect(form.get('podId')).toBe('p1');
    const file = form.get('image');
    expect(file.type).toBe('text/markdown');
    expect(await file.text()).toBe('hello');

    const [postUrl, postInit] = fetchFn.mock.calls[1];
    expect(postUrl).toBe('http://backend:5000/api/messages/p1');
    expect(JSON.parse(postInit.body)).toEqual({ content: 'see [[upload:123-1.md|notes.md|5|document]]' });
  });

  it('requires exactly one of text or base64', async () => {
    const fetchFn = installFetch(async () => json({}));
    const result = await byName.user_upload_file.call({ podId: 'p1', fileName: 'a.txt' });
    expect(result.isError).toBe(true);
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

describe('user_read_file — through the signed URL, since downloads take only a JWT', () => {
  const bytes = (contentType, body) => ({
    ok: true,
    status: 200,
    headers: { get: () => contentType },
    arrayBuffer: async () => Buffer.from(body),
  });

  it('mints a signed URL with the user token, then fetches the bytes from it', async () => {
    const fetchFn = installFetch(async (url) => (url.endsWith('/url')
      ? json({ url: '/api/uploads/f.csv?t=signed' })
      : bytes('text/csv; charset=utf-8', 'a,b\n1,2')));
    const result = parse(await byName.user_read_file.call({ fileName: 'f.csv' }));
    expect(fetchFn.mock.calls[0][0]).toBe('http://backend:5000/api/uploads/f.csv/url');
    expect(fetchFn.mock.calls[1][0]).toBe('http://backend:5000/api/uploads/f.csv?t=signed');
    expect(result).toEqual({ fileName: 'f.csv', contentType: 'text/csv', truncated: false, content: 'a,b\n1,2' });
  });

  it('returns an image as MCP image content', async () => {
    installFetch(async (url) => (url.endsWith('/url') ? json({ url: '/x?t=1' }) : bytes('image/png', 'PNG')));
    const result = await byName.user_read_file.call({ fileName: 'p.png' });
    expect(result.content[0]).toEqual({ type: 'image', data: Buffer.from('PNG').toString('base64'), mimeType: 'image/png' });
  });

  it('returns only metadata for other binaries', async () => {
    installFetch(async (url) => (url.endsWith('/url') ? json({ url: '/x?t=1' }) : bytes('application/zip', 'PK')));
    expect(parse(await byName.user_read_file.call({ fileName: 'a.zip' })))
      .toMatchObject({ contentType: 'application/zip', size: 2, note: expect.any(String) });
  });
});
