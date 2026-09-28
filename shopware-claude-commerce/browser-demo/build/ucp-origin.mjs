// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Rewrite the seeded UCP sales-channel config from the build-time loopback
 * origin to the live host. GitHub project Pages serves the shop under a path
 * prefix (`https://host/repo`); `embeddedAllowedOrigins` and
 * `embeddedFrameAncestors` must stay pathless origins or SwagAgenticCommerce
 * throws UcpConfigException and the storefront answers 500/400.
 */

export const UCP_ORIGIN_ONLY_KEYS = Object.freeze(['embeddedAllowedOrigins', 'embeddedFrameAncestors']);

/** Scheme + host + port, never a path. */
export function originOnly(url, fallback = '') {
  if (typeof url !== 'string' || !url) return fallback || url;
  try {
    return new URL(url).origin;
  } catch {
    return fallback || url;
  }
}

/**
 * @param {unknown} config
 * @param {Iterable<string>} staleOrigins  previous APP_URL / seed origin prefixes
 * @param {string} livePublic              shop public URL including a Pages prefix
 * @param {string} liveOrigin              pathless browser origin
 */
export function rewriteUcpConfig(config, staleOrigins, livePublic, liveOrigin) {
  const stale = [...new Set([...staleOrigins].filter((from) => typeof from === 'string' && from && from !== livePublic))];
  const swap = (value, forceOriginOnly) => {
    if (typeof value === 'string') {
      let next = value;
      for (const from of stale) {
        if (next.startsWith(from)) {
          next = livePublic + next.slice(from.length);
          break;
        }
      }
      return forceOriginOnly ? originOnly(next, liveOrigin) : next;
    }
    if (Array.isArray(value)) return value.map((item) => swap(item, forceOriginOnly));
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value).map(([key, child]) => [key, swap(child, forceOriginOnly || UCP_ORIGIN_ONLY_KEYS.includes(key))]),
      );
    }
    return value;
  };
  return swap(config, false);
}
