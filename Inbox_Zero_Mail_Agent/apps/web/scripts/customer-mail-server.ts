/**
 * Customer mail server: plays the external customer's mail system in local
 * tests (plan.md §2–§3). Knows nothing about Inbox Zero, the Gmail API, or
 * webhooks — it only speaks the {envelope, raw} transport contract with the
 * mail backbone and keeps its own send/receive records.
 *
 *   pnpm customer:mail serve  --port 4100 --outbox .tmp/customer-mail
 *   pnpm customer:mail send   --url http://127.0.0.1:4005 --token <token> \
 *     --from customer@example.external --to managed@example.com file.eml
 *   pnpm customer:mail compose --from ... --to ... --subject ... --text ... \
 *     --out file.eml
 */
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

type Args = Record<string, string | boolean> & { _?: string };

// send 的默认发件人,可用 --from 覆盖。
const DEFAULT_CUSTOMER_FROM = process.env.CUSTOMER_EMAIL || "ruoyu@gmail.com";

const USAGE = `Commands:
  serve   --port <n> --outbox <dir> --token <transport-token>
  send    --url <backbone> --token <transport-token> [--from <addr>] --to <addr>[,--cc,--bcc] <eml-file>
  compose --from <addr> --to <addr> [--subject <s>] [--text <s>] [--in-reply-to <id>] [--references <ids>] --out <file>`;

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  const args = parseArgs(rest);

  if (command === "serve") {
    await serve(args);
    return;
  }
  if (command === "send") {
    await send(args, args._);
    return;
  }
  if (command === "compose") {
    await compose(args);
    return;
  }
  console.error(USAGE);
  process.exit(1);
}

async function serve(args: Args) {
  const port = Number(args.port ?? 4100);
  const outbox = String(args.outbox ?? ".tmp/customer-mail");
  const token = String(args.token ?? "");
  if (!token) {
    console.error("serve requires --token");
    process.exit(1);
  }
  await mkdir(outbox, { recursive: true });
  const logFile = path.join(outbox, "log.jsonl");

  const server = createServer(async (req, res) => {
    if (req.method !== "POST" || req.url !== "/receive") {
      res.writeHead(404).end();
      return;
    }
    if (req.headers.authorization !== `Bearer ${token}`) {
      res.writeHead(401).end();
      return;
    }
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    Promise.resolve()
      .then(async () => {
        const payload = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
          deliveryId: string;
          messageId: string;
          threadId?: string;
          envelope: { from: string; recipients: string[] };
          raw: string;
        };
        const emlPath = path.join(outbox, `${payload.deliveryId}.eml`);

        // 骨干重试会复用同一个 deliveryId。.eml 写文件天然幂等(同名覆盖),
        // 但 log.jsonl 是 append,重复投递会让同一封邮件出现多行 —— 记录
        // 前先按 deliveryId 查重,已有就只补 .eml 不追加日志。
        let alreadyRecorded = false;
        try {
          const log = await readFile(logFile, "utf8");
          alreadyRecorded = log
            .trim()
            .split("\n")
            .filter(Boolean)
            .some((line) => {
              try {
                return JSON.parse(line).deliveryId === payload.deliveryId;
              } catch {
                return false;
              }
            });
        } catch {
          // log 文件还不存在,必然没记录过。
        }

        await writeFile(emlPath, Buffer.from(payload.raw, "base64"));
        if (!alreadyRecorded) {
          await appendFile(
            logFile,
            `${JSON.stringify({
              deliveryId: payload.deliveryId,
              messageId: payload.messageId,
              threadId: payload.threadId,
              envelope: payload.envelope,
              receivedAt: new Date().toISOString(),
            })}\n`,
          );
        }
        console.log("received", {
          deliveryId: payload.deliveryId,
          messageId: payload.messageId,
          from: payload.envelope.from,
          recipients: payload.envelope.recipients,
          duplicate: alreadyRecorded,
        });
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ accepted: payload.deliveryId }));
      })
      .catch((error) => {
        console.error("failed to record delivery", error);
        res.writeHead(400).end();
      });
  });

  // 默认绑定回环(宿主机场景安全);容器内用 LISTEN_HOST=0.0.0.0 覆盖,
  // 否则同网络的其它容器连不上。
  const listenHost = process.env.LISTEN_HOST ?? "127.0.0.1";
  await new Promise<void>((resolve) =>
    server.listen(port, listenHost, resolve),
  );
  console.log(`Customer mail server listening on http://${listenHost}:${port}`);
  console.log(`  outbox: ${outbox}`);

  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.on(signal, () => {
      server.close(() => process.exit(0));
    });
  }
}

async function send(args: Args, file?: string) {
  const url = String(args.url ?? "");
  const token = String(args.token ?? "");
  // 没给 --from 时回落到一个合理的默认发件人,免得每次都敲。
  const from = String(args.from ?? DEFAULT_CUSTOMER_FROM);
  if (!url || !token || !from || !file) {
    console.error("send requires --url, --token and an .eml file");
    console.error(USAGE);
    process.exit(1);
  }
  const recipients = [args.to, args.cc, args.bcc]
    .filter((value): value is string => typeof value === "string")
    .flatMap((value) => value.split(","))
    .map((value) => value.trim())
    .filter(Boolean);
  if (recipients.length === 0) {
    console.error("send requires --to");
    process.exit(1);
  }

  const raw = await readFile(file);
  const response = await fetch(`${url.replace(/\/+$/, "")}/transport/inbound`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      envelope: { from, recipients },
      raw: raw.toString("base64"),
    }),
  });
  const body = await response.text();
  if (!response.ok) {
    console.error(`delivery rejected (${response.status}): ${body}`);
    process.exit(1);
  }
  console.log(body);
}

async function compose(args: Args) {
  const from = String(args.from ?? DEFAULT_CUSTOMER_FROM);
  const to = String(args.to ?? "");
  const out = String(args.out ?? "");
  if (!from || !to || !out) {
    console.error("compose requires --to and --out");
    process.exit(1);
  }
  const messageId = `<${randomUUID()}@customer.local>`;
  const lines = [
    `From: ${from}`,
    `To: ${to}`,
    `Subject: ${String(args.subject ?? "(no subject)")}`,
    `Message-ID: ${messageId}`,
    `Date: ${new Date().toUTCString()}`,
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
  ];
  if (args["in-reply-to"]) lines.push(`In-Reply-To: ${args["in-reply-to"]}`);
  if (args.references) lines.push(`References: ${args.references}`);
  lines.push("", String(args.text ?? ""), "");
  await writeFile(out, lines.join("\r\n"), "utf8");
  console.log(`wrote ${out} (${messageId})`);
}

function parseArgs(argv: string[]): Args {
  const args: Args = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      // First positional arg is the command's file operand (e.g. the .eml to send).
      args._ ??= arg;
      continue;
    }
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
