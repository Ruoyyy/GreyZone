import { expect, test } from "bun:test";
import {
  finalAssistantReply,
  hasPublishedReply,
} from "../src/runner/final-reply";

test("publishes the final assistant text and excludes tools and reasoning", () => {
  expect(
    finalAssistantReply([
      {
        type: "assistant",
        message: { content: [{ type: "text", text: "Reading files" }] },
      },
      {
        type: "user",
        message: { content: [{ type: "text", text: "private tool output" }] },
      },
      {
        type: "assistant",
        message: {
          content: [
            { type: "thinking", thinking: "private reasoning" },
            { type: "text", text: "Run python3 expenses.py." },
          ],
        },
      },
      { type: "result", duration_ms: 10 },
    ]),
  ).toBe("Run python3 expenses.py.");
});

test("supports result text and ignores absent replies", () => {
  expect(finalAssistantReply([{ type: "result", result: "Answer" }])).toBe(
    "Answer",
  );
  expect(
    finalAssistantReply([{ type: "tool_result", content: "secret" }]),
  ).toBe("");
  expect(finalAssistantReply(null)).toBe("");
});

test("ignores malformed events and content blocks", () => {
  expect(
    finalAssistantReply([
      null,
      1,
      { type: "assistant", message: null },
      {
        type: "assistant",
        message: { content: [null, 1, { type: "text", text: "Answer" }] },
      },
    ]),
  ).toBe("Answer");
});

test("agent metadata alone is not a reply; authored comments are preserved", () => {
  const footer =
    "\n\n---\n🤖 **Agent:** [agent](url)\n💻 Chat\n<!-- letta-metadata\nagent_id: 1\n-->";
  expect(hasPublishedReply("Letta Code is working…" + footer)).toBe(false);
  expect(hasPublishedReply("Here is the answer." + footer)).toBe(true);
});
