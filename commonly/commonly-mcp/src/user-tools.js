/**
 * User MCP tools — act as ONE human account (the `cm_` token in config).
 *
 * Every tool is a call to a route the web app itself uses, so it carries that
 * person's permissions and nothing more: a pod they are not in refuses them
 * here exactly as it does in the browser. Posts appear under their name.
 *
 * Names are `user_*`, never `commonly_*`: the agent server's `commonly_*`
 * tools hit different routes with different meaning, and one name for two
 * capabilities has misled agents before (AX audit).
 */
import { request, requestUpload, HttpError } from './client.js';
import { extname } from 'path';
import { ok, err, CONTENT_TYPES } from './tools.js';

const required = (props) => ({ type: 'object', properties: props, additionalProperties: false });
const reqWith = (props, requiredKeys) => ({ ...required(props), required: requiredKeys });
const STRING = { type: 'string' };
const INT = { type: 'integer' };

// read_file returns text inline up to this many characters, then truncates.
const MAX_TEXT_CHARS = 200_000;
const TEXT_TYPES = /^(text\/|application\/(json|xml|csv|x-ndjson))/;

const enc = encodeURIComponent;

export const buildUserTools = (config) => {
  const wrap = (fn) => async (args = {}) => {
    try {
      return ok(await fn(args));
    } catch (e) {
      return err(e);
    }
  };

  // Bytes behind a short-lived signed URL. The download route itself accepts
  // only a browser JWT, so a `cm_` token reads through the URL the mint route
  // (`GET /api/uploads/:fileName/url`, which takes any user token) hands back.
  const download = async (fileName) => {
    const { url } = await request(config, { method: 'GET', path: `/api/uploads/${enc(fileName)}/url` });
    const res = await fetch(`${config.baseUrl}${url}`);
    if (!res.ok) throw new HttpError(res.status, await res.text(), `download failed: HTTP ${res.status}`);
    return {
      contentType: (res.headers.get('content-type') || 'application/octet-stream').split(';')[0].trim(),
      buffer: Buffer.from(await res.arrayBuffer()),
    };
  };

  return [
    {
      name: 'user_list_pods',
      description: 'List the pods (group chats and DMs) you are a member of: id, name, type and member count.',
      inputSchema: required({}),
      call: wrap(async () => {
        const pods = await request(config, { method: 'GET', path: '/api/pods' });
        return (Array.isArray(pods) ? pods : []).map((p) => ({
          podId: p._id,
          name: p.name,
          type: p.type,
          description: p.description || '',
          memberCount: Array.isArray(p.members) ? p.members.length : undefined,
        }));
      }),
    },
    {
      name: 'user_get_pod',
      description: 'Get one pod you can see: its details and its members (humans and agents, with usernames).',
      inputSchema: reqWith({ podId: STRING }, ['podId']),
      call: wrap(({ podId }) => request(config, { method: 'GET', path: `/api/pods/${enc(podId)}` })),
    },
    {
      name: 'user_read_messages',
      description: 'Read messages in a pod, newest page first. `limit` is 1–200 (default 50); pass `before` (a message timestamp from a previous page) to page back.',
      inputSchema: reqWith({ podId: STRING, limit: INT, before: STRING }, ['podId']),
      call: wrap(({ podId, limit, before }) => request(config, {
        method: 'GET', path: `/api/messages/${enc(podId)}`, query: { limit, before },
      })),
    },
    {
      name: 'user_send_message',
      description: 'Post a message in a pod as yourself. @mention an agent (e.g. @scout) to address it. Optional: `replyToMessageId` quotes and addresses a message; `threadRootId` continues that thread.',
      inputSchema: reqWith({
        podId: STRING, content: STRING, replyToMessageId: STRING, threadRootId: STRING,
      }, ['podId', 'content']),
      call: wrap(({ podId, content, replyToMessageId, threadRootId }) => request(config, {
        method: 'POST',
        path: `/api/messages/${enc(podId)}`,
        body: { content, replyToMessageId, threadRootId },
      })),
    },
    {
      name: 'user_react',
      description: 'Add an emoji reaction to a message, or remove yours with `remove: true`.',
      inputSchema: reqWith({ messageId: STRING, emoji: STRING, remove: { type: 'boolean' } }, ['messageId', 'emoji']),
      call: wrap(({ messageId, emoji, remove }) => (remove
        ? request(config, { method: 'DELETE', path: `/api/messages/${enc(messageId)}/reactions/${enc(emoji)}` })
        : request(config, { method: 'POST', path: `/api/messages/${enc(messageId)}/reactions`, body: { emoji } }))),
    },
    {
      name: 'user_upload_file',
      description: 'Upload a file into a pod and post it there as a file card. This server cannot see your filesystem: pass the content itself — `text` for a text file, or `base64` for any file — plus `fileName` (its extension decides the type). Optional `message` is posted with it. Max 10 MB.',
      inputSchema: reqWith({
        podId: STRING, fileName: STRING, text: STRING, base64: STRING, message: STRING,
      }, ['podId', 'fileName']),
      call: wrap(async ({ podId, fileName, text, base64, message }) => {
        if ((text === undefined) === (base64 === undefined)) {
          throw new Error('pass exactly one of `text` or `base64`');
        }
        const fileBuffer = text !== undefined ? Buffer.from(text, 'utf8') : Buffer.from(base64, 'base64');
        const uploaded = await requestUpload(config, {
          path: '/api/uploads',
          fileBuffer,
          fileName,
          contentType: CONTENT_TYPES[extname(fileName).toLowerCase()] || 'application/octet-stream',
          fileField: 'image',
          fields: { podId },
        });
        // Same directive the web composer posts, so it renders as a file pill.
        const u = uploaded || {};
        const directive = `[[upload:${u.fileName}|${u.originalName || fileName}|${u.size ?? fileBuffer.length}|${u.kind || 'document'}]]`;
        await request(config, {
          method: 'POST',
          path: `/api/messages/${enc(podId)}`,
          body: { content: message ? `${message} ${directive}` : directive },
        });
        return uploaded;
      }),
    },
    {
      name: 'user_list_files',
      description: 'List the files shared in a pod. Each `fileName` is what user_read_file takes.',
      inputSchema: reqWith({ podId: STRING }, ['podId']),
      call: wrap(({ podId }) => request(config, { method: 'GET', path: `/api/pods/${enc(podId)}/files` })),
    },
    {
      name: 'user_read_file',
      description: 'Read a shared file by `fileName` (from user_list_files or a file card). Text comes back as text, images as an image; other types return their metadata only.',
      inputSchema: reqWith({ fileName: STRING }, ['fileName']),
      call: async ({ fileName }) => {
        try {
          const { contentType, buffer } = await download(fileName);
          if (contentType.startsWith('image/')) {
            return { content: [{ type: 'image', data: buffer.toString('base64'), mimeType: contentType }] };
          }
          if (TEXT_TYPES.test(contentType)) {
            const textValue = buffer.toString('utf8');
            return ok({
              fileName,
              contentType,
              truncated: textValue.length > MAX_TEXT_CHARS,
              content: textValue.slice(0, MAX_TEXT_CHARS),
            });
          }
          return ok({ fileName, contentType, size: buffer.length, note: 'binary file — content not returned' });
        } catch (e) {
          return err(e);
        }
      },
    },
    {
      name: 'user_list_pod_agents',
      description: 'List the agents installed in a pod — the names you can @mention or open a DM with.',
      inputSchema: reqWith({ podId: STRING }, ['podId']),
      call: wrap(({ podId }) => request(config, { method: 'GET', path: `/api/registry/pods/${enc(podId)}/agents` })),
    },
    {
      name: 'user_dm_agent',
      description: 'Open (or reopen) your private 1:1 chat with an agent you share a pod with, by `agentName` (and `instanceId` when it has several). Returns the DM `podId`; talk there with user_send_message.',
      inputSchema: reqWith({ agentName: STRING, instanceId: STRING, podId: STRING }, ['agentName']),
      call: wrap(async ({ agentName, instanceId, podId }) => {
        const { room } = await request(config, {
          method: 'POST', path: '/api/agents/runtime/room', body: { agentName, instanceId, podId },
        });
        return { podId: room?._id, name: room?.name, type: room?.type };
      }),
    },
  ];
};
