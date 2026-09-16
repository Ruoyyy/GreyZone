/**
 * In-memory MCP server for smoke-testing the Promptfoo issue/comment provider.
 * Not the real Gitea scenario MCP — only mirrors create_issue / comment / wait tools.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import * as z from 'zod/v4';

const issues = new Map();
let nextNumber = 1;

function jsonResult(obj) {
  return {
    content: [{ type: 'text', text: JSON.stringify(obj) }],
  };
}

const server = new McpServer({
  name: 'mock-gitea-issue-mcp',
  version: '1.0.0',
});

server.registerTool(
  'create_issue',
  {
    description: 'Create a new issue (mock)',
    inputSchema: {
      title: z.string(),
      body: z.string(),
    },
  },
  async ({ title, body }) => {
    const issue_number = nextNumber++;
    issues.set(issue_number, {
      title,
      body,
      comments: [],
      lastPrompt: body,
    });
    return jsonResult({ issue_number, title });
  },
);

server.registerTool(
  'comment_on_issue',
  {
    description: 'Comment on an issue (mock)',
    inputSchema: {
      issue_number: z.union([z.number(), z.string()]),
      body: z.string(),
    },
  },
  async ({ issue_number, body }) => {
    const num = Number(issue_number);
    const issue = issues.get(num);
    if (!issue) {
      return {
        isError: true,
        content: [{ type: 'text', text: `issue ${num} not found` }],
      };
    }
    issue.comments.push(body);
    issue.lastPrompt = body;
    return jsonResult({ issue_number: num, ok: true });
  },
);

server.registerTool(
  'wait_for_agent_reply',
  {
    description: 'Wait for mock agent reply',
    inputSchema: {
      issue_number: z.union([z.number(), z.string()]),
    },
  },
  async ({ issue_number }) => {
    const num = Number(issue_number);
    const issue = issues.get(num);
    if (!issue) {
      return {
        isError: true,
        content: [{ type: 'text', text: `issue ${num} not found` }],
      };
    }
    const reply = `mock-agent-reply to: ${issue.lastPrompt}`;
    return jsonResult({
      issue_number: num,
      reply,
      failed: false,
    });
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
