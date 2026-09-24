import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

const server = new Server({ name: 'mock-customer-mail', version: '0.1.0' }, { capabilities: { tools: {} } });
const messages = [];
let deliveries = 0;
let sends = 0;

function success(data) {
  return { structuredContent: data, content: [{ type: 'text', text: JSON.stringify(data) }] };
}

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: ['send_email', 'list_received', 'read_received', 'reply_to'].map((name) => ({
    name,
    inputSchema: { type: 'object', properties: {} },
  })),
}));

server.setRequestHandler(CallToolRequestSchema, async ({ params }) => {
  const args = params.arguments || {};
  if (params.name === 'list_received') return success({ messages: messages.map(({ full, ...summary }) => summary) });
  if (params.name === 'read_received') {
    const message = messages.find((item) => item.index === args.index);
    return message ? success({ ...message.full, index: message.index, deliveryId: message.deliveryId }) : {
      isError: true,
      content: [{ type: 'text', text: 'Message missing' }],
    };
  }
  if (params.name !== 'send_email' && params.name !== 'reply_to') {
    return { isError: true, content: [{ type: 'text', text: 'Unknown tool' }] };
  }
  sends += 1;
  const rfcMessageId = `<sent-${sends}@customer.local>`;
  const subject = params.name === 'send_email'
    ? args.subject
    : messages.find((item) => item.index === args.index)?.subject;
  if (params.name === 'reply_to' && !subject) {
    return { isError: true, content: [{ type: 'text', text: 'Cannot reply to missing mail' }] };
  }
  if (sends === 1) addMail('other@example.com', 'unrelated', subject, rfcMessageId);
  if (args.body !== 'silent') addMail('ruoyu@gmail.com', `reply ${sends}`, subject, rfcMessageId);
  return success({ deliveryId: `incoming-${sends}`, messageId: `internal-${sends}`, rfcMessageId });
});

function addMail(to, body, subject, inReplyTo) {
  deliveries += 1;
  const deliveryId = `out-${deliveries}`;
  messages.push({
    index: deliveries,
    deliveryId,
    threadId: 'thread-1',
    from: 'admin@gmail.com',
    recipients: [to],
    subject: `Re: ${subject}`,
    receivedAt: new Date().toISOString(),
    full: { from: 'admin@gmail.com', to, recipients: [to], subject: `Re: ${subject}`, body, inReplyTo },
  });
}

await server.connect(new StdioServerTransport());
