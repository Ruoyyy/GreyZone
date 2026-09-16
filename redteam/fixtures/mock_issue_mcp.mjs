/**
 * In-memory MCP mimicking official gitea-mcp issue_write / issue_read shapes.
 * Used only for smoke tests (not the Docker gitea-mcp gateway).
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import * as z from 'zod/v4';

const issues = new Map();
let nextNumber = 1;
let nextCommentId = 100;

function jsonResult(obj) {
  return {
    content: [{ type: 'text', text: JSON.stringify(obj) }],
  };
}

function addBotReply(issue, promptText) {
  const id = nextCommentId++;
  issue.comments.push({
    id,
    body: `**Letta Code** working\n\nmock-agent-reply to: ${promptText}\n\nLetta Code finished`,
    user: 'gitea-actions',
  });
}

const server = new McpServer({
  name: 'mock-gitea-mcp',
  version: '1.0.0',
});

server.registerTool(
  'issue_write',
  {
    description: 'Write issues (mock of gitea-mcp issue_write)',
    inputSchema: {
      method: z.enum([
        'create',
        'update',
        'add_comment',
        'edit_comment',
        'add_labels',
        'remove_label',
        'replace_labels',
        'clear_labels',
      ]),
      owner: z.string(),
      repo: z.string(),
      issue_number: z.union([z.number(), z.string()]).optional(),
      title: z.string().optional(),
      body: z.string().optional(),
    },
  },
  async (args) => {
    const { method, title, body } = args;
    if (method === 'create') {
      const number = nextNumber++;
      const issue = {
        number,
        title: title || '',
        body: body || '',
        comments: [],
      };
      issues.set(number, issue);
      addBotReply(issue, body || '');
      return jsonResult({
        number,
        title: issue.title,
        body: issue.body,
        state: 'open',
        user: 'ruoyu',
      });
    }
    if (method === 'add_comment') {
      const num = Number(args.issue_number);
      const issue = issues.get(num);
      if (!issue) {
        return {
          isError: true,
          content: [{ type: 'text', text: `issue ${num} not found` }],
        };
      }
      let commentBody = body || '';
      if (
        commentBody.trim() &&
        !commentBody.startsWith('@letta-code')
      ) {
        commentBody = `@letta-code ${commentBody}`;
      }
      const id = nextCommentId++;
      issue.comments.push({
        id,
        body: commentBody,
        user: 'ruoyu',
      });
      addBotReply(issue, commentBody);
      return jsonResult({ id, body: commentBody, user: 'ruoyu' });
    }
    return {
      isError: true,
      content: [{ type: 'text', text: `unsupported method ${method}` }],
    };
  },
);

server.registerTool(
  'issue_read',
  {
    description: 'Read issue (mock of gitea-mcp issue_read)',
    inputSchema: {
      method: z.enum(['get', 'get_comments', 'get_labels']),
      owner: z.string(),
      repo: z.string(),
      issue_number: z.union([z.number(), z.string()]),
    },
  },
  async ({ method, issue_number }) => {
    const num = Number(issue_number);
    const issue = issues.get(num);
    if (!issue) {
      return {
        isError: true,
        content: [{ type: 'text', text: `issue ${num} not found` }],
      };
    }
    if (method === 'get') {
      return jsonResult({
        number: issue.number,
        title: issue.title,
        body: issue.body,
        state: 'open',
        user: 'ruoyu',
      });
    }
    if (method === 'get_comments') {
      return jsonResult(issue.comments);
    }
    return jsonResult([]);
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
