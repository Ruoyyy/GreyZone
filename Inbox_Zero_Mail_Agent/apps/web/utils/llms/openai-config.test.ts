import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("DEFAULT_LLMS", "");
  vi.stubEnv("DEFAULT_LLM_PROVIDER", undefined);
  vi.stubEnv("DEFAULT_LLM_MODEL", undefined);
  vi.stubEnv("OPENAI_API_KEY", "sk-config-fixture");
  vi.stubEnv("OPENAI_MODEL", "fixture-model");
  vi.stubEnv("OPENAI_BASE_URL", undefined);
});

afterEach(() => vi.unstubAllEnvs());

describe("benchmark OpenAI configuration", () => {
  it("derives the default model chain from the standard variables", async () => {
    const { env } = await import("@/env");
    expect(env.DEFAULT_LLMS).toBe("openai-compatible:fixture-model");
    expect(env.OPENAI_API_KEY).toBe("sk-config-fixture");
    expect(env.OPENAI_BASE_URL).toBe("https://api.openai.com/v1");
  });

  it("preserves a custom endpoint and explicit role model selection", async () => {
    vi.stubEnv("OPENAI_BASE_URL", "http://127.0.0.1:1234/v1");
    vi.stubEnv("DEFAULT_LLMS", "openai-compatible:explicit-model");
    const { env } = await import("@/env");
    expect(env.DEFAULT_LLMS).toBe("openai-compatible:explicit-model");
    expect(env.OPENAI_BASE_URL).toBe("http://127.0.0.1:1234/v1");
  });

  it("requires a model instead of silently selecting another provider", async () => {
    vi.stubEnv("OPENAI_MODEL", undefined);
    const logging = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      await expect(import("@/env")).rejects.toThrow();
    } finally {
      logging.mockRestore();
    }
  });
});
