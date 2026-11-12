function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Extract public assistant text, never tool results or reasoning events. */
export function finalAssistantReply(events: unknown): string {
  if (!Array.isArray(events)) return "";
  for (const event of [...events].reverse()) {
    if (!isRecord(event)) continue;
    if (
      event.type === "result" &&
      typeof event.result === "string" &&
      !event.is_error &&
      event.result.trim()
    )
      return event.result.trim();
    if (event?.type !== "assistant") continue;
    const content = isRecord(event.message) ? event.message.content : undefined;
    if (!Array.isArray(content)) continue;
    const text = content
      .flatMap((part: unknown) =>
        isRecord(part) && part.type === "text" && typeof part.text === "string"
          ? [part.text]
          : [],
      )
      .join("\n")
      .trim();
    if (text) return text;
  }
  return "";
}

/** Metadata and the initial working placeholder are not a published answer. */
export function hasPublishedReply(body: string): boolean {
  return Boolean(
    (
      body
        .replace(/<!-- letta-metadata[\s\S]*?-->/g, "")
        .split(/\n(?:---\s*\n)?🤖 \*\*Agent:\*\*/)[0] ?? ""
    )
      .replace(/Letta Code is working[…\.]{1,3}(?:\s*<img[^>]*>)?/i, "")
      .replace(/I'll analyze this and get back to you\.?/gi, "")
      .replace(/[\s-]/g, ""),
  );
}
