#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * The local server for the browser demo — one process, one origin:
 *
 *   node server/index.mjs            serve dist/site (npm start)      → http://127.0.0.1:4188
 *   node server/index.mjs --dev      Vite dev middleware (npm run dev), same routes + HMR
 *
 * Both modes add COOP/COEP on every response, the right MIME types (application/wasm) and
 * expose the Anthropic proxy under /api/anthropic/* (server/anthropic-proxy.mjs) with the key
 * from the repo .env. Only Node built-ins are used (Vite is a devDependency already).
 *
 * Flags / environment:
 *   --port <n> | PORT                 listen port (default 4188)
 *   --host <addr> | HOST              bind address (default 127.0.0.1; use 0.0.0.0 for LAN demos)
 *   --root <dir>                      static root (default dist/site)
 *   --no-isolation-headers            emulate a host without COOP/COEP (tests the SW fallback)
 *   --trust-proxy                     honour X-Forwarded-For for the per-IP budget
 *   ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, DEMO_PROXY_* (see anthropic-proxy.mjs)
 */
import { createServer } from 'node:http';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createAnthropicProxy, limitsFromEnv } from './anthropic-proxy.mjs';
import { envInt, loadEnv } from './env.mjs';
import { ISOLATION_HEADERS, SERVICE_WORKER_ALLOWED_HEADER, SERVICE_WORKER_PATH } from './headers.mjs';
import { createStaticHandler } from './static-site.mjs';

const DEMO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = resolve(DEMO_ROOT, '..');
const DEFAULT_PORT = 4188;
const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_SITE_DIR = join(DEMO_ROOT, 'dist', 'site');

function parseArgs(argv) {
  const args = { dev: false, isolationHeaders: true, trustProxy: false, port: null, host: null, root: null };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case '--dev':
        args.dev = true;
        break;
      case '--no-isolation-headers':
        args.isolationHeaders = false;
        break;
      case '--trust-proxy':
        args.trustProxy = true;
        break;
      case '--port':
        args.port = Number(argv[++i]);
        break;
      case '--host':
        args.host = argv[++i];
        break;
      case '--root':
        args.root = resolve(argv[++i]);
        break;
      case '--help':
      case '-h':
        console.log('usage: node server/index.mjs [--dev] [--port n] [--host addr] [--root dir] [--no-isolation-headers] [--trust-proxy]');
        process.exit(0);
        break;
      default:
        throw new Error(`unknown argument: ${arg}`);
    }
  }
  return args;
}

function timestamp() {
  return new Date().toISOString().slice(11, 19);
}

function log(line) {
  console.log(`[${timestamp()}] ${line}`);
}

function setBaseHeaders(res, pathname, isolationHeaders) {
  if (isolationHeaders) for (const [key, value] of Object.entries(ISOLATION_HEADERS)) res.setHeader(key, value);
  if (pathname === SERVICE_WORKER_PATH) res.setHeader(SERVICE_WORKER_ALLOWED_HEADER[0], SERVICE_WORKER_ALLOWED_HEADER[1]);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { env, loaded } = loadEnv([join(REPO_ROOT, '.env'), join(DEMO_ROOT, '.env')]);
  const port = args.port || Number(env.PORT) || DEFAULT_PORT;
  const host = args.host || env.HOST || DEFAULT_HOST;

  const proxy = createAnthropicProxy({
    apiKey: env.ANTHROPIC_API_KEY || '',
    workspaceId: env.ANTHROPIC_WORKSPACE_ID || '',
    limits: limitsFromEnv(env, envInt),
    trustProxyHeader: args.trustProxy,
    log,
  });

  let vite = null;
  let staticSite = null;
  const httpServer = createServer();

  if (args.dev) {
    const { createServer: createViteServer } = await import('vite');
    vite = await createViteServer({
      configFile: join(DEMO_ROOT, 'app', 'vite.config.ts'),
      appType: 'spa',
      server: {
        middlewareMode: true,
        hmr: { server: httpServer },
        // The Vite plugin adds the isolation headers itself; when emulating a header-less host
        // it must not.
        headers: args.isolationHeaders ? undefined : {},
      },
    });
    if (!args.isolationHeaders) process.env.DEMO_NO_ISOLATION_HEADERS = '1';
  } else {
    const root = args.root || DEFAULT_SITE_DIR;
    if (!existsSync(join(root, 'index.html'))) {
      console.error(`no built site at ${root} — run \`npm run build\` first (or \`npm run dev\` for the Vite dev server).`);
      process.exit(1);
    }
    staticSite = createStaticHandler(root, { isolationHeaders: args.isolationHeaders });
  }

  httpServer.on('request', (req, res) => {
    const url = new URL(req.url || '/', 'http://localhost');
    const pathname = url.pathname;
    Promise.resolve()
      .then(async () => {
        if (await proxy.handle(req, res, pathname)) return;
        if (vite) {
          setBaseHeaders(res, pathname, args.isolationHeaders);
          vite.middlewares(req, res, () => {
            res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
            res.end('not found');
          });
          return;
        }
        if (staticSite.handle(req, res, pathname)) return;
        setBaseHeaders(res, pathname, args.isolationHeaders);
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('not found');
      })
      .catch((error) => {
        log(`error ${req.method} ${pathname}: ${error.stack || error}`);
        if (!res.headersSent) {
          res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
          res.end('internal error');
        } else {
          res.destroy();
        }
      });
  });

  await new Promise((resolveListen, reject) => {
    httpServer.once('error', reject);
    httpServer.listen(port, host, resolveListen);
  });

  const origin = `http://${host === '0.0.0.0' ? 'localhost' : host}:${port}`;
  log(`browser demo ${args.dev ? '(Vite dev)' : `(static: ${staticSite.root})`} → ${origin}`);
  log(`env files: ${loaded.length ? loaded.join(', ') : 'none'}`);
  log(
    proxy.configured
      ? `anthropic proxy: ${origin}${proxy.prefix}  (key from environment, ${proxy.limits.requestsPerSession} req / ${proxy.limits.outputTokensPerSession} output tokens per session)`
      : `anthropic proxy: not configured (no ANTHROPIC_API_KEY) — chat works with "bring your own key" only`
  );
  if (!args.isolationHeaders) log('COOP/COEP headers disabled: the service worker adds them after the first reload');

  const shutdown = () => {
    log('shutting down');
    proxy.close();
    httpServer.close();
    vite?.close();
    setTimeout(() => process.exit(0), 200).unref();
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
