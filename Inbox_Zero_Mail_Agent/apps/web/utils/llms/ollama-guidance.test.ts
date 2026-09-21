import { describe, it, expect, vi, beforeEach } from "vitest";
import { z } from "zod";
import { Provider } from "@/utils/llms/config";
import {
  appendOllamaOnlySystemGuidance,
  appendStructuredOutputSchema,
  OLLAMA_STRUCTURED_OUTPUT_GUIDANCE,
} from "./ollama-guidance";

vi.mock("@/env", () => ({
  env: {
    OPENAI_COMPATIBLE_STRUCTURED_OUTPUTS: true,
  },
}));

import { env } from "@/env";

describe("appendOllamaOnlySystemGuidance", () => {
  beforeEach(() => {
    vi.mocked(env).OPENAI_COMPATIBLE_STRUCTURED_OUTPUTS = true;
  });

  it("appends guidance for Ollama", () => {
    const result = appendOllamaOnlySystemGuidance(
      { system: "Decide whether this is cold outreach." },
      { provider: Provider.OLLAMA },
      OLLAMA_STRUCTURED_OUTPUT_GUIDANCE,
    );

    expect(result.system).toContain("Decide whether this is cold outreach.");
    expect(result.system).toContain("Return only valid JSON");
  });

  it("leaves other providers untouched when they support structured outputs", () => {
    const result = appendOllamaOnlySystemGuidance(
      { system: "Decide whether this is cold outreach." },
      { provider: Provider.OPENAI_COMPATIBLE },
      OLLAMA_STRUCTURED_OUTPUT_GUIDANCE,
    );

    expect(result.system).toBe("Decide whether this is cold outreach.");
  });

  // An OpenAI-compatible endpoint that rejects response_format: json_schema
  // falls back to json_object, which the API only accepts when the prompt
  // mentions JSON. Without the guidance those calls fail outright.
  it("appends guidance for OpenAI-compatible providers without structured outputs", () => {
    vi.mocked(env).OPENAI_COMPATIBLE_STRUCTURED_OUTPUTS = false;

    const result = appendOllamaOnlySystemGuidance(
      { system: "Decide whether this is cold outreach." },
      { provider: Provider.OPENAI_COMPATIBLE },
      OLLAMA_STRUCTURED_OUTPUT_GUIDANCE,
    );

    expect(result.system).toContain("Decide whether this is cold outreach.");
    expect(result.system).toContain("Return only valid JSON");
  });

  describe("appendStructuredOutputSchema", () => {
    const schema = z.object({ coldEmail: z.boolean(), reason: z.string() });

    // The SDK drops the schema when the provider cannot enforce it, so the
    // model never learns the field names and invents its own.
    it("describes the schema for OpenAI-compatible providers without structured outputs", () => {
      vi.mocked(env).OPENAI_COMPATIBLE_STRUCTURED_OUTPUTS = false;

      const result = appendStructuredOutputSchema(
        { system: "Decide whether this is cold outreach." },
        { provider: Provider.OPENAI_COMPATIBLE },
        schema,
      );

      expect(result.system).toContain("Decide whether this is cold outreach.");
      expect(result.system).toContain('"coldEmail"');
      expect(result.system).toContain('"reason"');
    });

    it("leaves providers that enforce the schema untouched", () => {
      const result = appendStructuredOutputSchema(
        { system: "Decide whether this is cold outreach." },
        { provider: Provider.OPENAI_COMPATIBLE },
        schema,
      );

      expect(result.system).toBe("Decide whether this is cold outreach.");
    });
  });
});
