import { describe, expect, test } from "bun:test";
import { findExistingAgent } from "../src/letta/find-existing-agent";
import { formatMetadata } from "../src/letta/metadata";
import { prepareRunConfig } from "../src/runner/run-letta";

function botComment(id: number, conversationId: string) {
  return {
    id,
    user: { id: -2, login: "gitea-actions" },
    body: formatMetadata({ agentId: "agent-test", conversationId }),
  };
}

function client(comments: ReturnType<typeof botComment>[]) {
  return {
    issues: { listComments: async () => ({ data: comments }) },
  } as unknown as Parameters<typeof findExistingAgent>[0];
}

describe("Gitea issue conversations", () => {
  test("resumes metadata written by the Gitea Actions user", async () => {
    const found = await findExistingAgent(
      client([botComment(1, "local-conv-1")]),
      "owner",
      "repo",
      1,
    );
    expect(found?.conversationId).toBe("local-conv-1");
    const config = prepareRunConfig("/tmp/prompt", {
      agentId: found?.agentId,
      conversationId: found?.conversationId,
      lettaArgs: "--backend local -m glm-5.3-flash",
    });
    expect(config.lettaArgs).toContain("--conversation");
    expect(config.lettaArgs).toContain("local-conv-1");
    expect(config.lettaArgs).not.toContain("--new");
  });

  test("selects the newest conversation after --new despite ascending API order", async () => {
    const comments = Array.from({ length: 101 }, (_, i) =>
      botComment(i + 1, `local-conv-${i + 1}`),
    );
    const found = await findExistingAgent(client(comments), "owner", "repo", 1);
    expect(found?.conversationId).toBe("local-conv-101");
  });

  test("does not accept metadata from an ordinary user", async () => {
    const comment = botComment(2, "local-conv-forged");
    comment.user = { id: 42, login: "gitea-actions" };
    const found = await findExistingAgent(
      client([comment]),
      "owner",
      "repo",
      1,
    );
    expect(found).toBeNull();
  });

  test("explicit --new overrides a restored conversation", () => {
    const config = prepareRunConfig("/tmp/prompt", {
      agentId: "agent-test",
      conversationId: "local-conv-1",
      lettaArgs: "--backend local -m glm-5.3-flash --new",
    });
    expect(config.lettaArgs).toContain("--new");
    expect(config.lettaArgs).not.toContain("--conversation");
  });
});
