import { z } from "zod";
import { env } from "@/env";
import { Provider } from "@/utils/llms/config";

type ProviderLike = {
  provider: string;
};

export function isOllamaProvider(provider: string) {
  return provider === Provider.OLLAMA;
}

// Without structured outputs the request falls back to `response_format:
// json_object`, which OpenAI-compatible APIs only accept when the prompt
// mentions JSON. Ollama has always needed this; an OpenAI-compatible endpoint
// with OPENAI_COMPATIBLE_STRUCTURED_OUTPUTS=false needs it for the same reason.
function needsStructuredOutputGuidance(modelOptions: ProviderLike) {
  if (isOllamaProvider(modelOptions.provider)) return true;

  return (
    modelOptions.provider === Provider.OPENAI_COMPATIBLE &&
    !env.OPENAI_COMPATIBLE_STRUCTURED_OUTPUTS
  );
}

export function appendOllamaOnlySystemGuidance<
  OPTIONS extends { system?: unknown },
>(
  options: OPTIONS,
  modelOptions: ProviderLike,
  guidance: readonly string[],
): OPTIONS {
  if (!needsStructuredOutputGuidance(modelOptions)) return options;

  return {
    ...options,
    system: appendSystemGuidance(options.system, guidance),
  };
}

// Providers that do not enforce response_format schemas drop them entirely, so
// the model never learns the field names and invents its own. The prompt is the
// only channel left once the API discards the schema.
export function appendStructuredOutputSchema<
  OPTIONS extends { system?: unknown },
>(options: OPTIONS, modelOptions: ProviderLike, schema: unknown): OPTIONS {
  if (!needsStructuredOutputGuidance(modelOptions)) return options;

  const jsonSchema = toJsonSchemaString(schema);
  if (!jsonSchema) return options;

  return {
    ...options,
    system: appendSystemGuidance(options.system, [
      "Return only valid JSON matching this JSON schema:",
      jsonSchema,
    ]),
  };
}

function toJsonSchemaString(schema: unknown): string | null {
  if (!(schema instanceof z.ZodType)) return null;

  try {
    return JSON.stringify(z.toJSONSchema(schema));
  } catch {
    return null;
  }
}

function appendSystemGuidance(
  system: unknown,
  guidance: readonly string[],
): string {
  const currentSystem = typeof system === "string" ? system.trimEnd() : "";
  const extraGuidance = guidance.join("\n");

  return currentSystem ? `${currentSystem}\n\n${extraGuidance}` : extraGuidance;
}

export const OLLAMA_STRUCTURED_OUTPUT_GUIDANCE = [
  "Return only valid JSON that matches the requested schema.",
  "The top-level JSON value must match the schema root exactly; do not return a nested item by itself.",
  "Do not include markdown, bullets, code fences, explanations, or any text outside the JSON.",
  "If a schema field asks for prose, put that prose inside the appropriate JSON string field.",
] as const;
