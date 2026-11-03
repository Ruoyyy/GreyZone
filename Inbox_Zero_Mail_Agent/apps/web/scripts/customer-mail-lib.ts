/**
 * Shared transport helpers for the customer mail role: compose, deliver,
 * parse, and read the outbox. Used by the CLI and the MCP server so they
 * share one implementation.
 */
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

export const DEFAULT_CUSTOMER_EMAIL =
  process.env.CUSTOMER_EMAIL || "alice@gmail.com";
export const DEFAULT_OUTBOX =
  process.env.CUSTOMER_OUTBOX || ".tmp/customer-mail";
export const DEFAULT_TRANSPORT_URL =
  process.env.TRANSPORT_URL || "http://127.0.0.1:4005";

export type ComposeInput = {
  from: string;
  to: string;
  subject: string;
  text: string;
  inReplyTo?: string;
  references?: string;
};

export function composeEml(opts: ComposeInput): string {
  const messageId = `<${randomUUID()}@customer.local>`;
  const lines = [
    `From: ${opts.from}`,
    `To: ${opts.to}`,
    `Subject: ${opts.subject}`,
    `Message-ID: ${messageId}`,
    `Date: ${new Date().toUTCString()}`,
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
  ];
  if (opts.inReplyTo) lines.push(`In-Reply-To: ${opts.inReplyTo}`);
  if (opts.references) lines.push(`References: ${opts.references}`);
  lines.push("", opts.text, "");
  return lines.join("\r\n");
}

export async function deliver({
  eml,
  from,
  recipients,
  transportUrl = DEFAULT_TRANSPORT_URL,
  transportToken,
}: {
  eml: string;
  from: string;
  recipients: string[];
  transportUrl?: string;
  transportToken: string;
}): Promise<{ deliveryId: string; messageId: string }> {
  const response = await fetch(
    `${transportUrl.replace(/\/+$/, "")}/transport/inbound`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${transportToken}`,
      },
      body: JSON.stringify({
        envelope: { from, recipients },
        raw: Buffer.from(eml).toString("base64"),
      }),
    },
  );
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`delivery rejected (${response.status}): ${body}`);
  }
  return JSON.parse(body) as { deliveryId: string; messageId: string };
}

export type ParsedEml = {
  from: string;
  to: string;
  subject: string;
  date: string;
  messageId: string;
  inReplyTo?: string;
  references?: string;
  body: string;
};

export function parseEml(raw: string): ParsedEml {
  const [headerBlock, ...bodyParts] = raw.split(/\r?\n\r?\n/);
  const headers: Record<string, string> = {};
  for (const line of headerBlock.split(/\r?\n/)) {
    const m = line.match(/^([A-Za-z-]+):\s*(.*)$/);
    if (m) headers[m[1].toLowerCase()] = m[2].trim();
  }
  return {
    from: headers.from ?? "(unknown)",
    to: headers.to ?? "(unknown)",
    subject: headers.subject ?? "(no subject)",
    date: headers.date ?? "(unknown)",
    messageId: headers["message-id"] ?? "(unknown)",
    inReplyTo: headers["in-reply-to"],
    references: headers.references,
    body: bodyParts.join("\n\n").trim(),
  };
}

export type InboxEntry = {
  index: number;
  deliveryId: string;
  messageId: string;
  threadId?: string;
  from: string;
  recipients: string[];
  receivedAt: string;
  subject?: string;
};

export async function loadInbox(
  outbox = DEFAULT_OUTBOX,
): Promise<InboxEntry[]> {
  const logFile = path.join(outbox, "log.jsonl");
  let lines: string[] = [];
  try {
    lines = (await readFile(logFile, "utf8"))
      .trim()
      .split("\n")
      .filter(Boolean);
  } catch {
    return [];
  }

  const entries: InboxEntry[] = [];
  for (const line of lines) {
    const rec = JSON.parse(line);
    try {
      const raw = await readFile(
        path.join(outbox, `${rec.deliveryId}.eml`),
        "utf8",
      );
      entries.push({
        index: entries.length + 1,
        deliveryId: rec.deliveryId,
        messageId: rec.messageId,
        threadId: rec.threadId,
        from: rec.envelope.from,
        recipients: rec.envelope.recipients,
        receivedAt: rec.receivedAt,
        subject: parseEml(raw).subject,
      });
    } catch {
      entries.push({
        index: entries.length + 1,
        deliveryId: rec.deliveryId,
        messageId: rec.messageId,
        threadId: rec.threadId,
        from: rec.envelope.from,
        recipients: rec.envelope.recipients,
        receivedAt: rec.receivedAt,
      });
    }
  }
  return entries;
}

export async function readEmlForEntry(
  entry: InboxEntry,
  outbox = DEFAULT_OUTBOX,
): Promise<ParsedEml> {
  const raw = await readFile(
    path.join(outbox, `${entry.deliveryId}.eml`),
    "utf8",
  );
  return parseEml(raw);
}
