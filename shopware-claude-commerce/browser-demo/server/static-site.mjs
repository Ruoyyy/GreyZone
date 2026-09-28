// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Static file handler for the assembled site (dist/site). Mirrors what a static host must do:
 * correct MIME types (application/wasm), COOP/COEP on every response, `Service-Worker-Allowed`
 * on the playground service worker, and an SPA fallback so that reloading on a Shopware route
 * (e.g. /checkout/cart, /admin, /index.php/…) serves the shell, which then opens the route
 * inside the storefront frame.
 */
import { createReadStream, existsSync, statSync } from 'node:fs';
import { extname, join, normalize, sep } from 'node:path';
import { ISOLATION_HEADERS, SERVICE_WORKER_ALLOWED_HEADER, SERVICE_WORKER_PATH, cacheControlFor, contentTypeFor } from './headers.mjs';

const STATIC_FILE_EXT = /\.[a-z0-9]{1,12}$/i;

function safeJoin(root, pathname) {
  const target = normalize(join(root, pathname));
  if (target !== root && !target.startsWith(root + sep)) return null;
  return target;
}

/**
 * @param {string} root      absolute directory to serve
 * @param {object} [options]
 * @param {boolean} [options.isolationHeaders=true]  set false to emulate a host without COOP/COEP
 */
export function createStaticHandler(root, options = {}) {
  const isolation = options.isolationHeaders !== false;

  function send(req, res, filePath, pathname) {
    const stat = statSync(filePath);
    const headers = {
      'Content-Type': contentTypeFor(extname(filePath)),
      'Content-Length': String(stat.size),
      'Cache-Control': cacheControlFor(pathname),
      'Last-Modified': stat.mtime.toUTCString(),
    };
    if (isolation) Object.assign(headers, ISOLATION_HEADERS);
    if (pathname === SERVICE_WORKER_PATH) headers[SERVICE_WORKER_ALLOWED_HEADER[0]] = SERVICE_WORKER_ALLOWED_HEADER[1];
    const since = req.headers['if-modified-since'];
    if (since && new Date(since).getTime() >= Math.floor(stat.mtime.getTime() / 1000) * 1000) {
      res.writeHead(304, headers);
      res.end();
      return;
    }
    res.writeHead(200, headers);
    if (req.method === 'HEAD') {
      res.end();
      return;
    }
    createReadStream(filePath).pipe(res);
  }

  return {
    root,
    /** Serves the request; returns false when nothing matched (caller answers 404). */
    handle(req, res, pathname) {
      if (req.method !== 'GET' && req.method !== 'HEAD') return false;
      let decoded;
      try {
        decoded = decodeURIComponent(pathname);
      } catch {
        return false;
      }
      const direct = safeJoin(root, decoded);
      if (direct && existsSync(direct)) {
        const stat = statSync(direct);
        if (stat.isFile()) {
          send(req, res, direct, decoded);
          return true;
        }
        const index = join(direct, 'index.html');
        if (stat.isDirectory() && existsSync(index)) {
          send(req, res, index, decoded.endsWith('/') ? `${decoded}index.html` : `${decoded}/index.html`);
          return true;
        }
      }
      // SPA fallback: anything that is not a file request gets the shell.
      const wantsHtml = (req.headers.accept || '').includes('text/html');
      const looksLikeFile = STATIC_FILE_EXT.test(decoded) && !decoded.startsWith('/index.php');
      if (wantsHtml && !looksLikeFile) {
        const shell = join(root, 'index.html');
        if (existsSync(shell)) {
          send(req, res, shell, '/index.html');
          return true;
        }
      }
      return false;
    },
  };
}
