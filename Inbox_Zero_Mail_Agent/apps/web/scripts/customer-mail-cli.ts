/**
 * Customer mail CLI: interactive inbox for the external customer role.
 *
 * Reads the customer mailbox (apps/web/.tmp/customer-mail): log.jsonl is the
 * index, <deliveryId>.eml files are the full messages. Replies and new mails
 * go through the same {envelope, raw} transport as customer-mail-server.ts.
 */
import { createInterface } from "node:readline";
import {
  DEFAULT_CUSTOMER_EMAIL,
  DEFAULT_OUTBOX,
  DEFAULT_TRANSPORT_URL,
  composeEml,
  deliver,
  loadInbox,
  readEmlForEntry,
  type InboxEntry,
} from "./customer-mail-lib";

const CUSTOMER_EMAIL = DEFAULT_CUSTOMER_EMAIL;
const OUTBOX = DEFAULT_OUTBOX;
const TRANSPORT_URL = DEFAULT_TRANSPORT_URL;
const TRANSPORT_TOKEN = process.env.TRANSPORT_TOKEN || "";

const rl = createInterface({ input: process.stdin, output: process.stdout });
// 管道输入下,loadInbox 的异步阶段会让 readline 丢掉已到达的行;
// 先暂停,就绪后再恢复。
rl.pause();

async function main() {
  if (!TRANSPORT_TOKEN) {
    console.error(
      "TRANSPORT_TOKEN is not set (look in .tmp/customer-mail/.transport-token)",
    );
    process.exit(1);
  }

  const inbox = await loadInbox();
  console.log(`Customer mail CLI (you are ${CUSTOMER_EMAIL})`);
  console.log(
    `Inbox: ${inbox.length} message(s). Commands: list, read <n>, reply <n>, send, quit`,
  );
  rl.resume();

  for (;;) {
    let line: string;
    try {
      line = (await ask("> ")).trim();
    } catch {
      // stdin closed (e.g. piped input or terminal EOF); exit quietly.
      process.exit(0);
    }
    if (!line) continue;
    const [cmd, ...rest] = line.split(/\s+/);
    const arg = rest.join(" ");

    switch (cmd.toLowerCase()) {
      case "list":
      case "ls":
        listInbox(inbox);
        break;
      case "read": {
        const entry = findEntry(inbox, arg);
        if (entry) await readMessage(entry);
        break;
      }
      case "reply": {
        const entry = findEntry(inbox, arg);
        if (entry) await reply(entry);
        break;
      }
      case "send":
        await sendNew();
        break;
      case "quit":
      case "exit":
        // 不调 process.exit:stdout 可能未 flush,小的输出(如 list)会被吞掉。
        console.log("bye");
        rl.close();
        return;
      case "help":
        console.log("Commands: list, read <n>, reply <n>, send, quit");
        break;
      default:
        console.log(`unknown command: ${cmd} (try 'help')`);
    }
  }
}

function listInbox(inbox: InboxEntry[]) {
  if (inbox.length === 0) {
    console.log("(inbox empty)");
    return;
  }
  for (const e of inbox) {
    const subject = e.subject ?? "(no subject)";
    console.log(
      `  [${e.index}] ${e.receivedAt.slice(0, 16).replace("T", " ")}  ${e.from}  ${subject}`,
    );
  }
}

async function readMessage(entry: InboxEntry) {
  const parsed = await readEmlForEntry(entry, OUTBOX);
  console.log("─".repeat(60));
  console.log(`From:    ${parsed.from}`);
  console.log(`To:      ${parsed.to}`);
  console.log(`Subject: ${parsed.subject}`);
  console.log(`Date:    ${parsed.date}`);
  console.log(`Msg-ID:  ${parsed.messageId}`);
  if (parsed.inReplyTo) console.log(`In-Reply-To: ${parsed.inReplyTo}`);
  if (parsed.references) console.log(`References:  ${parsed.references}`);
  console.log("─".repeat(60));
  console.log(parsed.body);
  console.log("─".repeat(60));
}

async function reply(entry: InboxEntry) {
  const original = await readEmlForEntry(entry, OUTBOX);
  // 回复目标用 envelope 的发件人:应用生成的邮件可能没有 From 头
  // (身份在传输 envelope 里),直接读头会得到 "(unknown)"。
  const replyTo = entry.from;
  const subject = original.subject.startsWith("Re:")
    ? original.subject
    : `Re: ${original.subject}`;

  const body = await askMultiline(
    `Reply to ${replyTo} (empty line to finish):`,
  );
  const references = original.references
    ? `${original.references} ${original.messageId}`
    : original.messageId;

  const eml = composeEml({
    from: CUSTOMER_EMAIL,
    to: replyTo,
    subject,
    text: body,
    inReplyTo: original.messageId,
    references,
  });

  try {
    const result = await deliver({
      eml,
      from: CUSTOMER_EMAIL,
      recipients: [replyTo],
      transportUrl: TRANSPORT_URL,
      transportToken: TRANSPORT_TOKEN,
    });
    console.log(`sent ✓ (${result.messageId})`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : "delivery failed");
  }
}

async function sendNew() {
  const to = await ask("To: ");
  if (!to) return;
  const subject = await ask("Subject: ");
  const body = await askMultiline("Body (empty line to finish):");
  const eml = composeEml({ from: CUSTOMER_EMAIL, to, subject, text: body });
  try {
    const result = await deliver({
      eml,
      from: CUSTOMER_EMAIL,
      recipients: [to],
      transportUrl: TRANSPORT_URL,
      transportToken: TRANSPORT_TOKEN,
    });
    console.log(`sent ✓ (${result.messageId})`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : "delivery failed");
  }
}

function findEntry(inbox: InboxEntry[], arg: string): InboxEntry | undefined {
  const n = Number.parseInt(arg, 10);
  if (Number.isNaN(n)) {
    console.error("usage: read <n> | reply <n> (see 'list' for numbers)");
    return;
  }
  const entry = inbox.find((e) => e.index === n);
  if (!entry) console.error(`no message #${n}`);
  return entry;
}

// ── tiny interactive prompts (readline-based; no stdin event soup) ─────────

function ask(question: string): Promise<string> {
  return new Promise((resolve) => {
    rl.question(question, (answer) => resolve(answer));
  });
}

async function askMultiline(hint: string): Promise<string> {
  console.log(hint);
  const lines: string[] = [];
  for (;;) {
    const line = await ask("");
    if (line === "") break;
    lines.push(line);
  }
  return lines.join("\n");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
