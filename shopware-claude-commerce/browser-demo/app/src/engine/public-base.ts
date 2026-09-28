// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Public path of this deployment. Vite `base` is `/` locally and
 * `/shopware-claude-commerce/` on GitHub project Pages.
 */
export const PUBLIC_BASE = String(import.meta.env.BASE_URL || '/').replace(/\/$/, '');

/** Origin-absolute path (`/index.php` → `/shopware-claude-commerce/index.php` on Pages). */
export function demoUrl(path: string): string {
  const normalised = path.startsWith('/') ? path : `/${path}`;
  return PUBLIC_BASE ? `${PUBLIC_BASE}${normalised}` : normalised;
}

/** Shopware's public origin including the Pages path prefix when present. */
export function shopPublicOrigin(origin: string = location.origin): string {
  return origin + PUBLIC_BASE;
}
