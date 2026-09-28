export const UCP_ORIGIN_ONLY_KEYS: readonly string[];
export function originOnly(url: string, fallback?: string): string;
export function rewriteUcpConfig(
  config: unknown,
  staleOrigins: Iterable<string>,
  livePublic: string,
  liveOrigin: string,
): unknown;
