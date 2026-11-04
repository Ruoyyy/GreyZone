/**
 * Mail backbone for the local Gmail emulator deployment.
 *
 * Extends the emulator into a near-real mail server with three faces:
 *  - ingress: POST /transport/inbound accepts {envelope, raw} from the
 *    customer mail server and imports it into the managed mailbox.
 *  - notify:  polls the emulator's history and pushes Pub/Sub-shaped
 *    notifications to the app webhook. Watch semantics come from proxying
 *    users.watch/users.stop — the emulator stores subscriptions but has no
 *    dispatch and exposes no way to read them back.
 *  - egress:  SENT messages found in history are fetched (format=raw) and
 *    delivered to the customer mail server with an idempotent deliveryId.
 *    Triggering on history/SENT (not on intercepting send calls) keeps the
 *    mailbox state as the single source of truth.
 *
 * Everything else is reverse-proxied to the upstream emulator unchanged, so
 * the app points GOOGLE_BASE_URL at this process. The emulator authenticates
 * any unknown bearer token as its first seeded user, which must be the
 * managed mailbox; that fallback is also why the API face must stay
 * unreachable from the customer side.
 */
import { randomUUID } from "node:crypto";
import {
  createServer,
  request as httpRequest,
  type IncomingHttpHeaders,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

export type MailBackboneOptions = {
  port: number;
  upstreamBaseUrl: string;
  managedEmail: string;
  transportToken: string;
  webhookUrl: string;
  webhookVerificationToken: string;
  customerReceiveUrl: string;
  stateDir: string;
  pollIntervalMs?: number;
  emulatorAuthToken?: string;
};

export type MailBackbone = {
  url: string;
  port: number;
  close(): Promise<void>;
};

type Subscription = {
  topicName: string;
  labelIds: string[];
  labelFilterBehavior: string | null;
  expiration: string;
};

type QueueItem = {
  kind: "notification" | "egress";
  historyId: string;
  messageId?: string;
  deliveryId?: string;
  attempts: number;
  nextAttemptAt: number;
};

type BackboneState = {
  historyCursor: string;
  deliveredMessageIds: string[];
  pending: QueueItem[];
  subscription?: Subscription | null;
};

type HistoryRecord = {
  id: string;
  messagesAdded?: Array<{ message?: MessageRef }>;
  labelsAdded?: Array<{ message?: MessageRef; labelIds?: string[] }>;
  labelsRemoved?: Array<{ message?: MessageRef; labelIds?: string[] }>;
};

type MessageRef = { id?: string; labelIds?: string[] };

const SENT_LABEL = "SENT";
const DEFAULT_TOPIC = "projects/local/topics/gmail";
const QUEUE_INTERVAL_MS = 200;
const MAX_BACKOFF_MS = 60_000;

export async function startMailBackbone(
  options: MailBackboneOptions,
): Promise<MailBackbone> {
  // The emulator allows 5000 requests per token per hour. Polling faster than
  // once a second spends that budget on history alone and locks the backbone
  // (and the app, which shares the quota only when it reuses the token) out.
  const pollIntervalMs = options.pollIntervalMs ?? 5000;
  const emulatorAuthToken = options.emulatorAuthToken ?? "mail-backbone";
  const upstream = new URL(options.upstreamBaseUrl);
  const stateFile = path.join(options.stateDir, "state.json");
  await mkdir(options.stateDir, { recursive: true });

  const state: BackboneState = await loadState(stateFile);
  // Restore the subscription on restart: watch is in-memory, but state lives in the volume.
  // Without restoring it, the cursor keeps advancing but notifications silently stop.
  let subscription: Subscription | null = state.subscription ?? null;
  if (subscription) {
    console.log("[mail-backbone] watch restored from state", {
      topicName: subscription.topicName,
      expiration: subscription.expiration,
    });
  }
  let closed = false;
  // Concurrency lock for processQueue. Must be declared here: the function body is
  // later in the file, but the timer can fire during the await below, and let's TDZ
  // would make the first call throw a ReferenceError.
  let queueRunning = false;

  const server: Server = createServer((req, res) => {
    handleRequest(req, res).catch((error) => {
      console.error("[mail-backbone] request failed", error);
      if (!res.headersSent) res.writeHead(500);
      res.end();
    });
  });

  const listenHost = process.env.LISTEN_HOST ?? "127.0.0.1";
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port, listenHost, resolve);
  });

  const address = server.address();
  if (!address || typeof address !== "object") {
    await close();
    throw new Error("mail backbone failed to bind");
  }

  if (!state.historyCursor || state.historyCursor === "0") {
    state.historyCursor = await resolveInitialCursor();
    await persistState();
  }

  const pollTimer = setInterval(() => {
    pollHistory().catch((error) =>
      console.error("[mail-backbone] history poll failed", error),
    );
  }, pollIntervalMs);
  pollTimer.unref();

  const queueTimer = setInterval(() => {
    processQueue().catch((error) =>
      console.error("[mail-backbone] queue processing failed", error),
    );
  }, QUEUE_INTERVAL_MS);
  queueTimer.unref();

  const backboneUrl = `http://127.0.0.1:${address.port}`;
  console.log(
    `[mail-backbone] listening on ${backboneUrl} -> ${options.upstreamBaseUrl} (managed: ${options.managedEmail})`,
  );

  return {
    url: backboneUrl,
    port: address.port,
    close,
  };

  async function close() {
    if (closed) return;
    closed = true;
    clearInterval(pollTimer);
    clearInterval(queueTimer);
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  async function handleRequest(req: IncomingMessage, res: ServerResponse) {
    const urlPath = (req.url ?? "/").split("?")[0];

    if (req.method === "POST" && urlPath === "/transport/inbound") {
      await handleIngress(req, res);
      return;
    }

    if (
      req.method === "POST" &&
      /^\/gmail\/v1\/users\/[^/]+\/watch$/.test(urlPath)
    ) {
      await handleCapturedProxy(req, res, (body, responseJson) => {
        if (responseJson?.historyId === undefined) return;
        subscription = {
          topicName: getString(body, "topicName") ?? DEFAULT_TOPIC,
          labelIds: getStringArray(body, "labelIds"),
          labelFilterBehavior:
            getString(body, "labelFilterBehavior", "labelFilterAction") ?? null,
          expiration: String(responseJson.expiration ?? ""),
        };
        // Persist the subscription so it survives a backbone restart (watch is in-memory);
        // otherwise the cursor keeps advancing but notifications silently stop — the classic
        // graceful stop/start pitfall.
        persistState();
        // Gmail delivers a sync notification when a watch is established.
        enqueue({
          kind: "notification",
          historyId: String(responseJson.historyId),
          attempts: 0,
          nextAttemptAt: Date.now(),
        });
        console.log("[mail-backbone] watch captured", {
          topicName: subscription.topicName,
          historyId: responseJson.historyId,
        });
      });
      return;
    }

    if (
      req.method === "POST" &&
      /^\/gmail\/v1\/users\/[^/]+\/stop$/.test(urlPath)
    ) {
      await handleCapturedProxy(req, res, (_body, _responseJson) => {
        subscription = null;
        persistState();
        console.log("[mail-backbone] watch stopped");
      });
      return;
    }

    proxyRequest(req, res);
  }

  function proxyRequest(req: IncomingMessage, res: ServerResponse) {
    const proxyReq = httpRequest(
      {
        protocol: upstream.protocol,
        hostname: upstream.hostname,
        port: upstream.port || 80,
        method: req.method,
        path: req.url,
        headers: { ...req.headers, host: upstream.host },
      },
      (proxyRes) => {
        res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
        proxyRes.pipe(res);
      },
    );
    proxyReq.on("error", (error) => {
      console.error("[mail-backbone] upstream proxy failed", error);
      if (!res.headersSent) res.writeHead(502);
      res.end();
    });
    req.pipe(proxyReq);
  }

  // For watch/stop: the bodies are tiny JSON, so buffer, forward, capture.
  async function handleCapturedProxy(
    req: IncomingMessage,
    res: ServerResponse,
    capture: (
      body: Record<string, unknown>,
      responseJson: Record<string, unknown> | undefined,
    ) => void,
  ) {
    const bodyText = await readBody(req);
    const upstreamRes = await fetch(`${options.upstreamBaseUrl}${req.url}`, {
      method: req.method,
      headers: filterRequestHeaders(req.headers),
      body: bodyText || undefined,
    });
    const responseText = await upstreamRes.text();
    let responseJson: Record<string, unknown> | undefined;
    try {
      responseJson = JSON.parse(responseText);
    } catch {
      responseJson = undefined;
    }
    if (upstreamRes.ok) {
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(bodyText || "{}");
      } catch {
        body = {};
      }
      capture(body, responseJson);
    }
    res.writeHead(upstreamRes.status, {
      "content-type":
        upstreamRes.headers.get("content-type") ?? "application/json",
    });
    res.end(responseText);
  }

  async function handleIngress(req: IncomingMessage, res: ServerResponse) {
    const auth = req.headers.authorization ?? "";
    if (auth !== `Bearer ${options.transportToken}`) {
      res.writeHead(401, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "invalid transport token" }));
      return;
    }

    let payload: {
      envelope?: { from?: string; recipients?: string[] };
      raw?: string;
    };
    const bodyText = await readBody(req);
    try {
      payload = JSON.parse(bodyText);
    } catch {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "invalid JSON body" }));
      return;
    }

    const recipients = (payload.envelope?.recipients ?? []).map((r) =>
      r.trim().toLowerCase(),
    );
    const managed = options.managedEmail.toLowerCase();
    if (!payload.envelope?.from || !payload.raw || recipients.length === 0) {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(
        JSON.stringify({
          error: "envelope.from, envelope.recipients and raw are required",
        }),
      );
      return;
    }
    if (!recipients.includes(managed)) {
      // Unknown recipients are rejected explicitly, never dropped silently.
      res.writeHead(422, { "content-type": "application/json" });
      res.end(
        JSON.stringify({
          error: "no managed recipient in envelope",
          managedEmail: options.managedEmail,
          recipients,
        }),
      );
      console.warn("[mail-backbone] inbound rejected: no managed recipient", {
        recipients,
      });
      return;
    }

    const rawBytes = Buffer.from(payload.raw, "base64");
    const upstreamRes = await fetch(
      `${options.upstreamBaseUrl}/gmail/v1/users/me/messages/import`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${emulatorAuthToken}`,
        },
        body: JSON.stringify({ raw: rawBytes.toString("base64url") }),
      },
    );
    if (!upstreamRes.ok) {
      const errorText = await upstreamRes.text();
      console.error("[mail-backbone] upstream import failed", {
        status: upstreamRes.status,
        errorText,
      });
      res.writeHead(502, { "content-type": "application/json" });
      res.end(
        JSON.stringify({ error: "managed mailbox rejected the message" }),
      );
      return;
    }

    const message = (await upstreamRes.json()) as {
      id: string;
      threadId: string;
      historyId?: string;
    };
    res.writeHead(202, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        deliveryId: randomUUID(),
        messageId: message.id,
        threadId: message.threadId,
        historyId: message.historyId,
      }),
    );
  }

  // A freshly restarted emulator reports historyId "0" while its history is
  // empty, so profile.historyId alone would leave the cursor at "0" — and the
  // first message after that would return the whole history as new, notifying
  // the app once per record.
  async function resolveInitialCursor(): Promise<string> {
    const profile = await emulatorGet<{ historyId?: string }>(
      "/gmail/v1/users/me/profile",
    );
    if (profile.historyId && profile.historyId !== "0")
      return profile.historyId;

    const messages = await emulatorGet<{ messages?: Array<{ id: string }> }>(
      "/gmail/v1/users/me/messages",
    );
    if (!messages.messages?.length) {
      // Nothing has happened yet; start below the emulator's seed value so the
      // cursor still means "I have seen everything".
      return "1";
    }

    // Reuse the newest message's historyId rather than re-deriving it.
    const ids = await Promise.all(
      messages.messages.map((message) =>
        emulatorGet<{ historyId?: string }>(
          `/gmail/v1/users/me/messages/${message.id}`,
        ).then((detail) => detail.historyId ?? "1"),
      ),
    );
    return ids.sort(compareHistoryIds).at(-1) ?? "1";
  }

  async function pollHistory() {
    const recordFilter = (record: HistoryRecord) =>
      compareHistoryIds(record.id, state.historyCursor) > 0;

    const records: HistoryRecord[] = [];
    let pageToken: string | undefined;
    do {
      const params = new URLSearchParams({
        startHistoryId: state.historyCursor,
        historyTypes: "messageAdded,labelAdded,labelRemoved",
        maxResults: "500",
      });
      if (pageToken) params.set("pageToken", pageToken);
      const data = await emulatorGet<{
        history?: HistoryRecord[];
        nextPageToken?: string;
      }>(`/gmail/v1/users/me/history?${params}`);
      records.push(...(data.history ?? []));
      pageToken = data.nextPageToken || undefined;
    } while (pageToken);

    const fresh = records.filter(recordFilter).sort(compareRecordIds);
    if (fresh.length === 0) return;

    for (const record of fresh) {
      if (shouldNotify(record)) {
        enqueue({
          kind: "notification",
          historyId: record.id,
          attempts: 0,
          nextAttemptAt: Date.now(),
        });
      }
      for (const messageId of outboundMessageIds(record)) {
        if (state.deliveredMessageIds.includes(messageId)) continue;
        enqueue({
          kind: "egress",
          historyId: record.id,
          messageId,
          deliveryId: randomUUID(),
          attempts: 0,
          nextAttemptAt: Date.now(),
        });
      }
      state.historyCursor = record.id;
    }
    await persistState();
  }

  function shouldNotify(record: HistoryRecord) {
    if (!subscription) return false;
    if (
      subscription.expiration &&
      Number(subscription.expiration) < Date.now()
    ) {
      return false;
    }
    if (subscription.labelIds.length === 0) return true;

    const touched = new Set<string>();
    for (const added of record.messagesAdded ?? []) {
      for (const label of added.message?.labelIds ?? []) touched.add(label);
    }
    for (const labels of [record.labelsAdded, record.labelsRemoved]) {
      for (const label of labels ?? []) {
        for (const id of label.labelIds ?? []) touched.add(id);
      }
    }
    const matches = subscription.labelIds.some((label) => touched.has(label));
    return subscription.labelFilterBehavior === "exclude" ? !matches : matches;
  }

  function enqueue(item: QueueItem) {
    state.pending.push(item);
  }

  async function processQueue() {
    if (closed || queueRunning) return;
    queueRunning = true;
    try {
      const now = Date.now();
      for (const item of [...state.pending]) {
        if (item.nextAttemptAt > now) continue;
        const ok =
          item.kind === "notification"
            ? await deliverNotification(item)
            : await deliverEgress(item);
        if (ok) {
          if (item.kind === "egress" && item.messageId) {
            state.deliveredMessageIds.push(item.messageId);
          }
          state.pending = state.pending.filter((pending) => pending !== item);
          await persistState();
        } else {
          item.attempts += 1;
          item.nextAttemptAt =
            Date.now() + Math.min(1000 * 2 ** item.attempts, MAX_BACKOFF_MS);
          await persistState();
        }
      }
    } finally {
      queueRunning = false;
    }
  }

  async function deliverNotification(item: QueueItem) {
    const topicName = subscription?.topicName ?? DEFAULT_TOPIC;
    const data = Buffer.from(
      JSON.stringify({
        emailAddress: options.managedEmail,
        historyId: item.historyId,
      }),
    ).toString("base64url");
    try {
      const response = await fetch(
        `${options.webhookUrl}?token=${encodeURIComponent(options.webhookVerificationToken)}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            subscription: topicName,
            message: {
              data,
              messageId: randomUUID(),
              publishTime: new Date().toISOString(),
            },
          }),
        },
      );
      if (!response.ok) {
        console.warn("[mail-backbone] webhook rejected notification", {
          status: response.status,
          historyId: item.historyId,
        });
        return false;
      }
      return true;
    } catch (error) {
      console.warn("[mail-backbone] webhook unreachable", { error });
      return false;
    }
  }

  async function deliverEgress(item: QueueItem) {
    const messageId = item.messageId;
    if (!messageId) return true;

    let rawMessage: { raw?: string; threadId?: string };
    let headers: Array<{ name: string; value: string }>;
    try {
      rawMessage = await emulatorGet<{ raw?: string; threadId?: string }>(
        `/gmail/v1/users/me/messages/${messageId}?format=raw`,
      );
      headers =
        (
          await emulatorGet<{
            payload?: { headers?: Array<{ name: string; value: string }> };
          }>(`/gmail/v1/users/me/messages/${messageId}?format=metadata`)
        ).payload?.headers ?? [];
    } catch (error) {
      console.error("[mail-backbone] egress fetch failed", {
        messageId,
        error,
      });
      return false;
    }
    if (!rawMessage.raw) {
      // Message vanished before delivery; dropping is the only option.
      console.warn("[mail-backbone] egress message has no raw, dropping", {
        messageId,
      });
      return true;
    }

    const header = (name: string) =>
      headers.find((h) => h.name.toLowerCase() === name)?.value;
    const recipients = [
      ...parseAddressList(header("to")),
      ...parseAddressList(header("cc")),
      ...parseAddressList(header("bcc")),
    ];

    try {
      const response = await fetch(options.customerReceiveUrl, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${options.transportToken}`,
        },
        body: JSON.stringify({
          deliveryId: item.deliveryId,
          messageId,
          threadId: rawMessage.threadId,
          envelope: {
            from: parseAddressList(header("from"))[0] ?? options.managedEmail,
            recipients,
          },
          raw: Buffer.from(rawMessage.raw, "base64url").toString("base64"),
        }),
      });
      if (!response.ok) {
        console.warn("[mail-backbone] customer rejected delivery", {
          status: response.status,
          messageId,
        });
        return false;
      }
      console.log("[mail-backbone] delivered outbound mail", {
        messageId,
        deliveryId: item.deliveryId,
        recipients,
      });
      return true;
    } catch (error) {
      console.warn("[mail-backbone] customer unreachable", {
        messageId,
        error,
      });
      return false;
    }
  }

  async function emulatorGet<T>(path: string): Promise<T> {
    const response = await fetch(`${options.upstreamBaseUrl}${path}`, {
      headers: { authorization: `Bearer ${emulatorAuthToken}` },
    });
    if (!response.ok) {
      throw new Error(`emulator GET ${path} failed: ${response.status}`);
    }
    return (await response.json()) as T;
  }

  async function persistState() {
    const next = `${stateFile}.tmp`;
    await writeFile(next, JSON.stringify(state, null, 2));
    await rename(next, stateFile);
  }
}

async function loadState(stateFile: string): Promise<BackboneState> {
  try {
    const parsed = JSON.parse(
      await readFile(stateFile, "utf8"),
    ) as BackboneState;
    return {
      historyCursor: parsed.historyCursor ?? "",
      deliveredMessageIds: parsed.deliveredMessageIds ?? [],
      pending: parsed.pending ?? [],
      subscription: parsed.subscription ?? null,
    };
  } catch {
    return {
      historyCursor: "",
      deliveredMessageIds: [],
      pending: [],
      subscription: null,
    };
  }
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

function filterRequestHeaders(headers: IncomingHttpHeaders) {
  const rest: Record<string, string> = {};
  for (const [key, value] of Object.entries(headers)) {
    if (key === "host" || key === "connection" || key === "content-length")
      continue;
    if (typeof value === "string") rest[key] = value;
  }
  return rest;
}

function getString(
  body: Record<string, unknown>,
  key: string,
  fallbackKey?: string,
): string | undefined {
  const value = body[key] ?? (fallbackKey ? body[fallbackKey] : undefined);
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function getStringArray(body: Record<string, unknown>, key: string): string[] {
  const value = body[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

// messages.send records a messageAdded carrying SENT, but drafts.send only
// records a labelAdded(SENT) on the existing draft message. Both are the same
// mailbox fact — a message now carries SENT — so egress watches for either.
function outboundMessageIds(record: HistoryRecord): string[] {
  const ids = (record.messagesAdded ?? [])
    .filter(({ message }) => message?.labelIds?.includes(SENT_LABEL))
    .map(({ message }) => message?.id);

  const labelledSent = (record.labelsAdded ?? []).some(({ labelIds }) =>
    labelIds?.includes(SENT_LABEL),
  );
  if (labelledSent) {
    ids.push(...(record.labelsAdded ?? []).map(({ message }) => message?.id));
  }

  return [...new Set(ids.filter((id): id is string => Boolean(id)))];
}

function parseAddressList(value: string | undefined): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((entry) => {
      const match = entry.trim().match(/<([^>]+)>/);
      return (match ? match[1] : entry.trim()).toLowerCase();
    })
    .filter(Boolean);
}

function compareHistoryIds(a: string, b: string): number {
  const bigA = BigInt(a);
  const bigB = BigInt(b);
  return bigA < bigB ? -1 : bigA > bigB ? 1 : 0;
}

function compareRecordIds(a: HistoryRecord, b: HistoryRecord): number {
  return compareHistoryIds(a.id, b.id);
}
