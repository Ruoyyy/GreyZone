/**
 * Contract tests for the mail backbone: the three faces of the
 * local "user mailbox" — ingress (import into the managed mailbox), notify
 * (Pub/Sub-shaped push with watch semantics), egress (SENT detection and
 * delivery to the customer mail server) — plus proxy passthrough fidelity.
 *
 * App-side calls (watch, send, drafts) go through the backbone proxy, the
 * way the real app would with GOOGLE_BASE_URL pointing at it. Emulator-direct
 * calls are test observations only.
 */
import { createServer, type Server } from "node:http";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it, beforeAll, beforeEach, afterAll } from "vitest";
import { createEmulator, type Emulator } from "emulate";
import {
  startMailBackbone,
  type MailBackbone,
} from "../../scripts/mail-backbone";

const RUN_INTEGRATION_TESTS = process.env.RUN_INTEGRATION_TESTS;

const MANAGED = "managed@example.com";
const CUSTOMER = "customer@example.external";
const TRANSPORT_TOKEN = "test-transport-token";
const WEBHOOK_TOKEN = "test-webhook-token";
const TOPIC = "projects/local/topics/gmail-test";

type Notification = { emailAddress: string; historyId: string; body: unknown };
type Delivery = {
  deliveryId: string;
  messageId: string;
  threadId?: string;
  envelope: { from: string; recipients: string[] };
  raw: Buffer;
};

describe.skipIf(!RUN_INTEGRATION_TESTS)(
  "mail backbone",
  { timeout: 30_000 },
  () => {
    let emulator: Emulator;
    let webhookServer: Server;
    let webhookUrl: string;
    let customerServer: Server;
    let customerUrl: string;
    const notifications: Notification[] = [];
    const deliveries: Delivery[] = [];
    let customerFailuresRemaining = 0;

    beforeEach(() => {
      notifications.length = 0;
      deliveries.length = 0;
      customerFailuresRemaining = 0;
    });

    beforeAll(async () => {
      emulator = await createEmulator({
        service: "google",
        port: await getAvailablePort(),
        seed: {
          google: {
            users: [{ email: MANAGED, name: "Managed" }],
            oauth_clients: [
              {
                client_id: "backbone-test.apps.googleusercontent.com",
                client_secret: "test-secret",
                redirect_uris: ["http://localhost:3000/callback"],
              },
            ],
          },
        },
      });

      webhookServer = createServer(async (req, res) => {
        const chunks: Buffer[] = [];
        for await (const chunk of req) chunks.push(chunk as Buffer);
        const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        const decoded = JSON.parse(
          Buffer.from(body.message.data, "base64url").toString("utf8"),
        );
        notifications.push({
          emailAddress: decoded.emailAddress,
          historyId: decoded.historyId,
          body,
        });
        res.writeHead(200).end();
      });
      webhookUrl = await listen(webhookServer);

      customerServer = createServer(async (req, res) => {
        const chunks: Buffer[] = [];
        for await (const chunk of req) chunks.push(chunk as Buffer);
        if (customerFailuresRemaining > 0) {
          customerFailuresRemaining -= 1;
          res.writeHead(500).end();
          return;
        }
        const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        deliveries.push({
          deliveryId: payload.deliveryId,
          messageId: payload.messageId,
          threadId: payload.threadId,
          envelope: payload.envelope,
          raw: Buffer.from(payload.raw, "base64"),
        });
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ accepted: payload.deliveryId }));
      });
      customerUrl = await listen(customerServer);
    });

    afterAll(async () => {
      await Promise.all([
        emulator?.close(),
        closeServer(webhookServer),
        closeServer(customerServer),
      ]);
    });

    it("ingress imports customer mail into the managed mailbox", async () => {
      const backbone = await startBackbone();
      try {
        const eml = buildEml({
          from: CUSTOMER,
          to: MANAGED,
          subject: "Ingress contract",
          messageId: "<ingress-1@example.external>",
          text: "Hello managed user.",
        });
        const response = await fetch(`${backbone.url}/transport/inbound`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${TRANSPORT_TOKEN}`,
          },
          body: JSON.stringify({
            envelope: { from: CUSTOMER, recipients: [MANAGED] },
            raw: Buffer.from(eml).toString("base64"),
          }),
        });
        expect(response.status).toBe(202);
        const receipt = (await response.json()) as {
          messageId: string;
          threadId: string;
          historyId: string;
        };
        expect(receipt.messageId).toBeTruthy();
        expect(receipt.threadId).toBeTruthy();
        expect(receipt.historyId).toBeTruthy();

        const message = await emulatorGet(
          emulator.url,
          `/gmail/v1/users/me/messages/${receipt.messageId}`,
        );
        expect(message.labelIds).toContain("INBOX");
        expect(message.labelIds).toContain("UNREAD");

        const history = await historyContaining(emulator, receipt.historyId);
        const added = history.history
          ?.flatMap((record) => record.messagesAdded ?? [])
          .find((entry) => entry.message?.id === receipt.messageId);
        expect(added).toBeTruthy();
      } finally {
        await backbone.close();
      }
    });

    it("rejects ingress without a valid token or managed recipient", async () => {
      const backbone = await startBackbone();
      try {
        const before = await emulatorGet(
          emulator.url,
          "/gmail/v1/users/me/profile",
        );
        const eml = buildEml({
          from: CUSTOMER,
          to: MANAGED,
          subject: "Rejected",
          messageId: "<reject-1@example.external>",
          text: "Nope.",
        });

        const unauthorized = await fetch(`${backbone.url}/transport/inbound`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: "Bearer wrong-token",
          },
          body: JSON.stringify({
            envelope: { from: CUSTOMER, recipients: [MANAGED] },
            raw: Buffer.from(eml).toString("base64"),
          }),
        });
        expect(unauthorized.status).toBe(401);

        const unroutable = await fetch(`${backbone.url}/transport/inbound`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${TRANSPORT_TOKEN}`,
          },
          body: JSON.stringify({
            envelope: { from: CUSTOMER, recipients: ["other@example.net"] },
            raw: Buffer.from(eml).toString("base64"),
          }),
        });
        expect(unroutable.status).toBe(422);
        const rejection = (await unroutable.json()) as { recipients: string[] };
        expect(rejection.recipients).toEqual(["other@example.net"]);

        const after = await emulatorGet(
          emulator.url,
          "/gmail/v1/users/me/profile",
        );
        expect(after.messagesTotal).toBe(before.messagesTotal);
      } finally {
        await backbone.close();
      }
    });

    it("watches through the proxy and pushes queryable notifications", async () => {
      const backbone = await startBackbone();
      try {
        await watchAsApp(backbone.url);
        await until(() => notifications.length > 0);
        const initial = notifications[0];
        expect(initial.emailAddress).toBe(MANAGED);
        expect(initial.body.subscription).toBe(TOPIC);
        expect(BigInt(initial.historyId)).toBeGreaterThan(0n);
        // The app decodes the notified history ID with Number.parseInt and rolls
        // the start point back by MAX_GMAIL_HISTORY_ID_GAP (3000). IDs above
        // 2^53 cannot survive that round trip: the rounding pushes the start past
        // the event being notified, and the app then skips the mail that caused
        // it. Keep emulator history IDs inside the exactly-representable range.
        expect(Number.parseInt(initial.historyId).toString()).toBe(
          initial.historyId,
        );

        const eml = buildEml({
          from: CUSTOMER,
          to: MANAGED,
          subject: "Notify me",
          messageId: "<notify-1@example.external>",
          text: "Trigger a notification.",
        });
        const ingressed = await ingress(backbone.url, eml, [MANAGED]);
        await until(() =>
          notifications.some((n) => n.historyId === ingressed.historyId),
        );

        // The notified historyId must be queryable in history.list.
        const history = await historyContaining(
          emulator,
          ingressed.historyId as string,
        );
        expect(history.history?.some((r) => r.id === ingressed.historyId)).toBe(
          true,
        );
      } finally {
        await backbone.close();
      }
    });

    it("restores watches after restart and preserves users.stop", async () => {
      const stateDir = await mkdtemp(path.join(tmpdir(), "mail-watch-restart-"));
      let backbone = await startBackbone(stateDir);
      try {
        await watchAsApp(backbone.url);
        await until(() => notifications.length > 0);
        await backbone.close();
        backbone = await startBackbone(stateDir);

        const restored = await ingress(
          backbone.url,
          buildEml({
            from: CUSTOMER,
            to: MANAGED,
            subject: "Watch after restart",
            messageId: "<restored-watch@example.external>",
            text: "The existing watch should notify the app.",
          }),
          [MANAGED],
        );
        await until(() =>
          notifications.some((n) => n.historyId === restored.historyId),
        );

        const stopped = await fetch(`${backbone.url}/gmail/v1/users/me/stop`, {
          method: "POST",
          headers: appHeaders(),
          body: "{}",
        });
        expect(stopped.ok).toBe(true);
        await backbone.close();
        backbone = await startBackbone(stateDir);
        notifications.length = 0;
        await ingress(
          backbone.url,
          buildEml({
            from: CUSTOMER,
            to: MANAGED,
            subject: "Stopped watch after restart",
            messageId: "<stopped-watch@example.external>",
            text: "The stopped watch must remain disabled.",
          }),
          [MANAGED],
        );
        await sleep(400);
        expect(notifications).toHaveLength(0);
      } finally {
        await backbone.close();
      }
    });

    it("delivers outbound SENT mail to the customer without a watch", async () => {
      const backbone = await startBackbone();
      try {
        const inEml = buildEml({
          from: CUSTOMER,
          to: MANAGED,
          subject: "Outbound thread",
          messageId: "<outbound-1@example.external>",
          text: "Original.",
        });
        const inbound = await ingress(backbone.url, inEml, [MANAGED]);

        const outEml = buildEml({
          from: MANAGED,
          to: CUSTOMER,
          bcc: "hidden@example.external",
          subject: "Re: Outbound thread",
          messageId: "<outbound-2@example.com>",
          inReplyTo: "<outbound-1@example.external>",
          references: "<outbound-1@example.external>",
          text: "Reply with a bcc.",
        });
        const sent = await sendAsApp(backbone.url, outEml);

        await until(() => deliveries.length > 0);
        const delivery = deliveries[0];
        expect(delivery.messageId).toBe(sent.id);
        // The reply joined the inbound thread.
        expect(sent.threadId).toBe(inbound.threadId);
        expect(delivery.threadId).toBe(inbound.threadId);
        // Envelope covers To and Bcc (Bcc header retention end to end).
        expect(delivery.envelope.from).toBe(MANAGED);
        expect(delivery.envelope.recipients).toEqual([
          CUSTOMER,
          "hidden@example.external",
        ]);
        // Raw round-trips the full MIME bytes.
        expect(delivery.raw.toString("utf8")).toContain("Reply with a bcc.");
        expect(delivery.raw.toString("utf8")).toContain(
          "Bcc: hidden@example.external",
        );

        // No watch was established: mailbox changed, but nothing was notified.
        expect(notifications).toHaveLength(0);

        // Polling continues without duplicating the delivery.
        await sleep(400);
        expect(deliveries).toHaveLength(1);
      } finally {
        await backbone.close();
      }
    });

    it("retries egress when the customer receiver fails", async () => {
      const backbone = await startBackbone();
      try {
        customerFailuresRemaining = 1;
        const outEml = buildEml({
          from: MANAGED,
          to: CUSTOMER,
          subject: "Retry me",
          messageId: "<retry-1@example.com>",
          text: "First attempt fails.",
        });
        const sent = await sendAsApp(backbone.url, outEml);

        await until(() => deliveries.length > 0, 15_000);
        expect(deliveries[0].messageId).toBe(sent.id);
        expect(deliveries).toHaveLength(1);
      } finally {
        customerFailuresRemaining = 0;
        await backbone.close();
      }
    });

    it("stops notifying after users.stop while still accepting mail", async () => {
      const backbone = await startBackbone();
      try {
        await watchAsApp(backbone.url);
        await until(() => notifications.length > 0);
        const afterInitial = notifications.length;

        const stopResponse = await fetch(
          `${backbone.url}/gmail/v1/users/me/stop`,
          {
            method: "POST",
            headers: appHeaders(),
          },
        );
        expect(stopResponse.ok).toBe(true);

        const eml = buildEml({
          from: CUSTOMER,
          to: MANAGED,
          subject: "After stop",
          messageId: "<after-stop-1@example.external>",
          text: "Stored but not notified.",
        });
        const stored = await ingress(backbone.url, eml, [MANAGED]);

        // Bounded quiet window: the mail must exist, the webhook must stay silent.
        await sleep(1200);
        expect(notifications.length).toBe(afterInitial);
        const message = await emulatorGet(
          emulator.url,
          `/gmail/v1/users/me/messages/${stored.messageId}`,
        );
        expect(message.labelIds).toContain("INBOX");
      } finally {
        await backbone.close();
      }
    });

    it("proxies non-intercepted Google endpoints unchanged", async () => {
      const backbone = await startBackbone();
      try {
        const viaBackbone = await fetch(
          `${backbone.url}/.well-known/openid-configuration`,
        );
        const direct = await fetch(
          `${emulator.url}/.well-known/openid-configuration`,
        );
        expect(viaBackbone.status).toBe(direct.status);
        expect(await viaBackbone.json()).toEqual(await direct.json());
      } finally {
        await backbone.close();
      }
    });

    it("sends drafts through the app path, consuming the draft", async () => {
      const backbone = await startBackbone();
      try {
        const draftEml = buildEml({
          from: MANAGED,
          to: CUSTOMER,
          subject: "Draft contract",
          messageId: "<draft-1@example.com>",
          text: "Sent from a draft.",
        });
        const draftResponse = await fetch(
          `${backbone.url}/gmail/v1/users/me/drafts`,
          {
            method: "POST",
            headers: appHeaders(),
            body: JSON.stringify({
              message: { raw: Buffer.from(draftEml).toString("base64url") },
            }),
          },
        );
        expect(draftResponse.ok).toBe(true);
        const draft = (await draftResponse.json()) as { id: string };

        // Draft creation and sending are separate events in real use, so their
        // history records land in different polls. Sending in the same tick
        // would let a single poll batch consume both and hide the egress path.
        await sleep(100);

        const sendResponse = await fetch(
          `${backbone.url}/gmail/v1/users/me/drafts/send`,
          {
            method: "POST",
            headers: appHeaders(),
            body: JSON.stringify({ id: draft.id }),
          },
        );
        expect(sendResponse.ok).toBe(true);

        await until(() =>
          deliveries.some((d) =>
            d.raw.toString("utf8").includes("Sent from a draft."),
          ),
        );

        const deleted = await fetch(
          `${emulator.url}/gmail/v1/users/me/drafts/${draft.id}`,
          { headers: { authorization: "Bearer observer" } },
        );
        expect(deleted.status).toBe(404);
      } finally {
        await backbone.close();
      }
    });

    it("applies emulator filters on ingress", async () => {
      const backbone = await startBackbone();
      try {
        const labelResponse = await fetch(
          `${backbone.url}/gmail/v1/users/me/labels`,
          {
            method: "POST",
            headers: appHeaders(),
            body: JSON.stringify({ name: "FromFilterLabel" }),
          },
        );
        expect(labelResponse.ok).toBe(true);
        const label = (await labelResponse.json()) as { id: string };

        const filterResponse = await fetch(
          `${backbone.url}/gmail/v1/users/me/settings/filters`,
          {
            method: "POST",
            headers: appHeaders(),
            body: JSON.stringify({
              criteria: { from: `filtered@${CUSTOMER.split("@")[1]}` },
              action: { addLabelIds: [label.id] },
            }),
          },
        );
        expect(filterResponse.ok).toBe(true);

        const eml = buildEml({
          from: `filtered@${CUSTOMER.split("@")[1]}`,
          to: MANAGED,
          subject: "Filtered ingress",
          messageId: "<filtered-1@example.external>",
          text: "Should get the filter label.",
        });
        const stored = await ingress(backbone.url, eml, [MANAGED]);
        const message = await emulatorGet(
          emulator.url,
          `/gmail/v1/users/me/messages/${stored.messageId}`,
        );
        expect(message.labelIds).toContain(label.id);
        expect(message.labelIds).toContain("INBOX");
      } finally {
        await backbone.close();
      }
    });

    async function startBackbone(stateDir?: string): Promise<MailBackbone> {
      return startMailBackbone({
        port: 0,
        upstreamBaseUrl: emulator.url,
        managedEmail: MANAGED,
        transportToken: TRANSPORT_TOKEN,
        webhookUrl,
        webhookVerificationToken: WEBHOOK_TOKEN,
        customerReceiveUrl: `${customerUrl}/receive`,
        stateDir: stateDir ?? (await mkdtemp(path.join(tmpdir(), "mail-backbone-"))),
        pollIntervalMs: 50,
      });
    }
  },
);

async function ingress(
  backboneUrl: string,
  eml: string,
  recipients: string[],
  from = CUSTOMER,
) {
  const response = await fetch(`${backboneUrl}/transport/inbound`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${TRANSPORT_TOKEN}`,
    },
    body: JSON.stringify({
      envelope: { from, recipients },
      raw: Buffer.from(eml).toString("base64"),
    }),
  });
  expect(response.status).toBe(202);
  return (await response.json()) as {
    deliveryId: string;
    messageId: string;
    threadId: string;
    historyId: string;
  };
}

async function sendAsApp(backboneUrl: string, eml: string) {
  const response = await fetch(
    `${backboneUrl}/gmail/v1/users/me/messages/send`,
    {
      method: "POST",
      headers: appHeaders(),
      body: JSON.stringify({ raw: Buffer.from(eml).toString("base64url") }),
    },
  );
  expect(response.ok).toBe(true);
  return (await response.json()) as { id: string; threadId: string };
}

async function watchAsApp(backboneUrl: string) {
  const response = await fetch(`${backboneUrl}/gmail/v1/users/me/watch`, {
    method: "POST",
    headers: appHeaders(),
    body: JSON.stringify({ topicName: TOPIC, labelFilterBehavior: "include" }),
  });
  expect(response.ok).toBe(true);
}

function appHeaders() {
  return {
    "content-type": "application/json",
    authorization: "Bearer app-side-token",
  };
}

async function emulatorGet<T = Record<string, any>>(
  emulatorUrl: string,
  emulatorPath: string,
): Promise<T> {
  const response = await fetch(`${emulatorUrl}${emulatorPath}`, {
    headers: { authorization: "Bearer observer" },
  });
  expect(response.ok).toBe(true);
  return (await response.json()) as T;
}

async function historyContaining(
  emulatorInstance: Emulator,
  historyId: string,
) {
  const start = (BigInt(historyId) - 1n).toString();
  return emulatorGet<{ history?: Array<{ id: string }> }>(
    emulatorInstance.url,
    `/gmail/v1/users/me/history?startHistoryId=${start}`,
  );
}

function buildEml({
  from,
  to,
  cc,
  bcc,
  subject,
  messageId,
  inReplyTo,
  references,
  text,
}: {
  from: string;
  to: string;
  cc?: string;
  bcc?: string;
  subject: string;
  messageId: string;
  inReplyTo?: string;
  references?: string;
  text: string;
}) {
  const lines = [
    `From: ${from}`,
    `To: ${to}`,
    ...(cc ? [`Cc: ${cc}`] : []),
    ...(bcc ? [`Bcc: ${bcc}`] : []),
    `Subject: ${subject}`,
    `Message-ID: ${messageId}`,
    `Date: ${new Date().toUTCString()}`,
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
    ...(inReplyTo ? [`In-Reply-To: ${inReplyTo}`] : []),
    ...(references ? [`References: ${references}`] : []),
    "",
    text,
    "",
  ];
  return lines.join("\r\n");
}

async function listen(server: Server): Promise<string> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address !== "object") {
    throw new Error("failed to bind mock server");
  }
  return `http://127.0.0.1:${address.port}`;
}

async function closeServer(server: Server) {
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

async function until(
  condition: () => boolean,
  timeoutMs = 8000,
  intervalMs = 50,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (condition()) return;
    await sleep(intervalMs);
  }
  throw new Error("condition not met before deadline");
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function getAvailablePort() {
  const { createServer: createNetServer } = await import("node:net");
  return new Promise<number>((resolve, reject) => {
    const server = createNetServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (address && typeof address === "object") resolve(address.port);
        else reject(new Error("no port"));
      });
    });
  });
}
