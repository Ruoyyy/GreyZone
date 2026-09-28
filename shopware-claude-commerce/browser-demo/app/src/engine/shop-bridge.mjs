// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * PHP-WASM request URL for a shop call. The service worker sends the full href.
 * Path-only URLs lose the GitHub Pages prefix when PHPRequestHandler joins them
 * against an absoluteUrl that already includes `/shopware-claude-commerce`, and
 * Shopware then maps no sales channel (HTML 400 on /store-api, 404 on /api/_mcp).
 */
export function phpRequestUrl(href, publicBase = '') {
  let url;
  try {
    url = new URL(href);
  } catch {
    if (!publicBase || href.startsWith(publicBase)) return href;
    return publicBase.replace(/\/$/, '') + (href.startsWith('/') ? href : `/${href}`);
  }
  const base = String(publicBase || '').replace(/\/$/, '');
  if (base && url.pathname !== base && !url.pathname.startsWith(`${base}/`)) {
    url.pathname = `${base}${url.pathname.startsWith('/') ? url.pathname : `/${url.pathname}`}`;
  }
  return url.href;
}

/**
 * Current PDP from a Shopware storefront document (overlay data attributes, buy widget,
 * or itemprop). Used when the iframe navigates so the shopping session can focus it.
 */
export function extractStorefrontProduct(html) {
  if (!html) return null;
  const attr = html.match(/data-product-id=["']([0-9a-f]{32})["']/i);
  const nameAttr = html.match(/data-product-name=["']([^"']+)["']/i);
  if (attr) {
    return { id: attr[1], name: nameAttr ? decodeHtml(nameAttr[1]) : '' };
  }
  const widget = html.match(/name=["']lineItems\[0\]\[(?:referencedId|id)\]["'][^>]*value=["']([0-9a-f]{32})["']/i)
    || html.match(/value=["']([0-9a-f]{32})["'][^>]*name=["']lineItems\[0\]\[(?:referencedId|id)\]["']/i);
  if (widget) {
    const title = html.match(/<(?:h1|meta)[^>]*(?:itemprop=["']name["']|class=["'][^"']*product-detail-name)[^>]*>([^<]+)/i)
      || html.match(/itemprop=["']name["'][^>]*content=["']([^"']+)["']/i);
    return { id: widget[1], name: title ? decodeHtml(title[1].trim()) : '' };
  }
  return null;
}

function decodeHtml(value) {
  return String(value)
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');
}
