// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Response headers and MIME types shared by the local server (server/index.mjs) and the Vite
 * dev middleware (app/vite.config.ts). The demo needs cross-origin isolation because MariaDB
 * WASM (lite4mariadb) runs on SharedArrayBuffer; every response of the origin carries these.
 */

export const ISOLATION_HEADERS = Object.freeze({
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
  'Cross-Origin-Resource-Policy': 'same-origin',
});

export const SERVICE_WORKER_PATH = '/service-worker.js';
export const SERVICE_WORKER_ALLOWED_HEADER = ['Service-Worker-Allowed', '/'];

/** Content types by extension. `application/wasm` matters: browsers refuse to compile-stream otherwise. */
export const MIME_TYPES = Object.freeze({
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.zip': 'application/zip',
  '.gz': 'application/gzip',
  '.tar': 'application/x-tar',
  '.whl': 'application/zip',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.webp': 'image/webp',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.avif': 'image/avif',
  '.webmanifest': 'application/manifest+json',
  '.xml': 'application/xml; charset=utf-8',
  '.php': 'text/plain; charset=utf-8',
  '.py': 'text/x-python; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.data': 'application/octet-stream',
  '.so': 'application/octet-stream',
  '.dat': 'application/octet-stream',
});

export function contentTypeFor(extension) {
  return MIME_TYPES[extension.toLowerCase()] || 'application/octet-stream';
}

/** Immutable caching for content-hashed files only (Vite assets, esbuild engine assets). */
export function cacheControlFor(pathname) {
  if (pathname.startsWith('/demo/assets/') || /^\/assets\/[^/]+-[A-Z0-9]{8}\.[a-z]+$/.test(pathname)) return 'public, max-age=31536000, immutable';
  return 'no-cache';
}
