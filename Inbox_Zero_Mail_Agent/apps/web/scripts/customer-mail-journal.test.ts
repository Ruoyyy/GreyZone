import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { deliver } from "./customer-mail-lib";

let directory: string;
let journal: string;

beforeEach(async () => {
  directory = await mkdtemp(path.join(tmpdir(), "mail-journal-"));
  journal = path.join(directory, "events.jsonl");
  vi.stubEnv("GREYZONE_MAIL_EVENTS", journal);
});

afterEach(async () => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  await rm(directory, { recursive: true, force: true });
});

it("journals intent before delivery and correlates the accepted mail IDs", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const pending = (await readFile(journal, "utf8"))
        .trim()
        .split("\n")
        .map((s) => JSON.parse(s));
      expect(pending.map((e) => e.event)).toEqual(["submit_intent"]);
      return new Response(
        JSON.stringify({ messageId: "message-1", deliveryId: "delivery-1" }),
      );
    }),
  );
  await deliver({
    eml: "Example mail",
    from: "alice@gmail.com",
    recipients: ["admin@gmail.com"],
    transportToken: "test-token",
  });
  const events = (await readFile(journal, "utf8"))
    .trim()
    .split("\n")
    .map((s) => JSON.parse(s));
  expect(events.map((e) => e.event)).toEqual(["submit_intent", "submitted"]);
  expect(events[1].attemptId).toBe(events[0].attemptId);
  expect(events[1].messageId).toBe("message-1");
  expect((await stat(journal)).mode & 0o777).toBe(0o600);
});

it("marks an uncertain write and never retries a failed request", async () => {
  const fetch = vi.fn().mockRejectedValue(new Error("connection lost"));
  vi.stubGlobal("fetch", fetch);
  await expect(
    deliver({
      eml: "Example mail",
      from: "alice@gmail.com",
      recipients: ["admin@gmail.com"],
      transportToken: "test-token",
    }),
  ).rejects.toThrow("connection lost");
  expect(fetch).toHaveBeenCalledTimes(1);
  const events = (await readFile(journal, "utf8"))
    .trim()
    .split("\n")
    .map((s) => JSON.parse(s));
  expect(events.map((e) => e.event)).toEqual([
    "submit_intent",
    "write_outcome_unknown",
  ]);
});
