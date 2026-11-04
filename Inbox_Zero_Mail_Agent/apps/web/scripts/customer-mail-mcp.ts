/**
 * MCP server for the external customer mail role.
 *
 * Exposes the customer's mailbox to an agent via stdio, so an agent can
 * drive the closed-loop mail tests: send mails as the external customer,
 * list received replies, read them, and reply in-thread.
 *
 * Shares one implementation with the CLI via customer-mail-lib.ts.
 *
 * Run with:
 *   TRANSPORT_TOKEN=$(cat .tmp/customer-mail/.transport-token) \
 *     npx tsx scripts/customer-mail-mcp.ts
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import {
  DEFAULT_CUSTOMER_EMAIL,
  DEFAULT_OUTBOX,
  DEFAULT_TRANSPORT_URL,
  composeEml,
  deliver,
  loadInbox,
  parseEml,
  readEmlForEntry,
} from "./customer-mail-lib";

const TRANSPORT_TOKEN = process.env.TRANSPORT_TOKEN || "";

const server = new McpServer({
  name: "customer-mail",
  version: "0.1.0",
});

server.registerTool(
  "send_email",
  {
    description:
      "Send an email as the external customer (alice@gmail.com). The mail " +
      "goes through the mail backbone to the managed mailbox (Inbox Zero). " +
      "Use this to start or continue a conversation with the managed user.",
    inputSchema: {
      to: z.string().describe("Recipient address, e.g. admin@gmail.com"),
      subject: z.string().describe("Email subject line"),
      body: z.string().describe("Plain-text email body"),
      inReplyTo: z
        .string()
        .optional()
        .describe("Message-ID of the email this is replying to, if any"),
      references: z
        .string()
        .optional()
        .describe("References header value for threading, if replying"),
    },
  },
  async ({ to, subject, body, inReplyTo, references }) => {
    if (!TRANSPORT_TOKEN) {
      return {
        content: [
          { type: "text" as const, text: "TRANSPORT_TOKEN is not set" },
        ],
        isError: true,
      };
    }
    const eml = composeEml({
      from: DEFAULT_CUSTOMER_EMAIL,
      to,
      subject,
      text: body,
      inReplyTo,
      references,
    });
    const rfcMessageId = parseEml(eml).messageId;
    try {
      const result = await deliver({
        eml,
        from: DEFAULT_CUSTOMER_EMAIL,
        recipients: [to],
        transportUrl: DEFAULT_TRANSPORT_URL,
        transportToken: TRANSPORT_TOKEN,
      });
      return {
        structuredContent: {
          deliveryId: result.deliveryId,
          messageId: result.messageId,
          rfcMessageId,
          to,
        },
        content: [
          {
            type: "text" as const,
            text: `Sent to ${to}. messageId=${result.messageId} deliveryId=${result.deliveryId}`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: error instanceof Error ? error.message : "delivery failed",
          },
        ],
        isError: true,
      };
    }
  },
);

server.registerTool(
  "list_received",
  {
    description:
      "List emails the external customer has actually received from Inbox " +
      "Zero (auto-replies etc.). Returns index numbers to use with " +
      "read_received and reply_to.",
    inputSchema: {},
  },
  async () => {
    const inbox = await loadInbox(DEFAULT_OUTBOX);
    if (inbox.length === 0) {
      return {
        structuredContent: { messages: [] },
        content: [{ type: "text" as const, text: "(inbox empty)" }],
      };
    }
    const lines = inbox.map(
      (entry) =>
        `[${entry.index}] ${entry.receivedAt.slice(0, 16).replace("T", " ")}  ` +
        `${entry.from}  ${entry.subject ?? "(no subject)"}`,
    );
    return {
      structuredContent: { messages: inbox },
      content: [{ type: "text" as const, text: lines.join("\n") }],
    };
  },
);

server.registerTool(
  "read_received",
  {
    description:
      "Read the full content of a received email by its index number (see list_received).",
    inputSchema: {
      index: z
        .number()
        .int()
        .positive()
        .describe("Index of the email from list_received"),
    },
  },
  async ({ index }) => {
    const inbox = await loadInbox(DEFAULT_OUTBOX);
    const entry = inbox.find((e) => e.index === index);
    if (!entry) {
      return {
        content: [{ type: "text" as const, text: `No message #${index}` }],
        isError: true,
      };
    }
    const parsed = await readEmlForEntry(entry, DEFAULT_OUTBOX);
    const headerLines = [
      `From:    ${parsed.from}`,
      `To:      ${parsed.to}`,
      `Subject: ${parsed.subject}`,
      `Date:    ${parsed.date}`,
      `Msg-ID:  ${parsed.messageId}`,
      ...(parsed.inReplyTo ? [`In-Reply-To: ${parsed.inReplyTo}`] : []),
      ...(parsed.references ? [`References:  ${parsed.references}`] : []),
    ];
    return {
      structuredContent: {
        index,
        deliveryId: entry.deliveryId,
        threadId: entry.threadId,
        envelopeFrom: entry.from,
        recipients: entry.recipients,
        ...parsed,
      },
      content: [
        {
          type: "text" as const,
          text: `${headerLines.join("\n")}\n\n${parsed.body}`,
        },
      ],
    };
  },
);

server.registerTool(
  "reply_to",
  {
    description:
      "Reply to a received email by index number. The reply is sent as the " +
      "external customer with proper In-Reply-To/References threading, so it " +
      "continues the conversation with the managed user.",
    inputSchema: {
      index: z
        .number()
        .int()
        .positive()
        .describe("Index of the email to reply to, from list_received"),
      body: z.string().describe("Reply body (plain text)"),
    },
  },
  async ({ index, body }) => {
    if (!TRANSPORT_TOKEN) {
      return {
        content: [
          { type: "text" as const, text: "TRANSPORT_TOKEN is not set" },
        ],
        isError: true,
      };
    }
    const inbox = await loadInbox(DEFAULT_OUTBOX);
    const entry = inbox.find((e) => e.index === index);
    if (!entry) {
      return {
        content: [{ type: "text" as const, text: `No message #${index}` }],
        isError: true,
      };
    }
    const original = await readEmlForEntry(entry, DEFAULT_OUTBOX);
    // Reply target uses the envelope sender: app-generated mail may have no From header.
    const replyTo = entry.from;
    const subject = original.subject.startsWith("Re:")
      ? original.subject
      : `Re: ${original.subject}`;
    const references = original.references
      ? `${original.references} ${original.messageId}`
      : original.messageId;

    const eml = composeEml({
      from: DEFAULT_CUSTOMER_EMAIL,
      to: replyTo,
      subject,
      text: body,
      inReplyTo: original.messageId,
      references,
    });
    const rfcMessageId = parseEml(eml).messageId;
    try {
      const result = await deliver({
        eml,
        from: DEFAULT_CUSTOMER_EMAIL,
        recipients: [replyTo],
        transportUrl: DEFAULT_TRANSPORT_URL,
        transportToken: TRANSPORT_TOKEN,
      });
      return {
        structuredContent: {
          deliveryId: result.deliveryId,
          messageId: result.messageId,
          rfcMessageId,
          inReplyTo: original.messageId,
          to: replyTo,
        },
        content: [
          {
            type: "text" as const,
            text: `Replied to ${replyTo} (${subject}). messageId=${result.messageId}`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: error instanceof Error ? error.message : "delivery failed",
          },
        ],
        isError: true,
      };
    }
  },
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // stderr keeps MCP protocol on stdout clean.
  console.error(
    `customer-mail MCP server ready (you are ${DEFAULT_CUSTOMER_EMAIL})`,
  );
}

main().catch((error) => {
  console.error("MCP server failed", error);
  process.exit(1);
});
