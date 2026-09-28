// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Vite config for the demo shell (React). Used in middleware mode by server/index.mjs --dev
 * (one origin with the Anthropic proxy) and by `vite build`. Dev and build both serve one
 * origin that combines: the playground engine (/browser-worker.js, /service-worker.js, /versions/*,
 * /assets/*, /mariadb/*, /php/*), our shell at "/", and our static tree under /demo/*
 * (bypassed by the playground service worker — see patches/playground-*.patch).
 *
 * The web UIs from storefront/web and merchant/web are Next.js apps; scripts/sync-backends.sh
 * copies them read-only into src/vendor/*. Three small compatibility layers make them run here:
 *   - `@/…` resolves per vendored app root (both apps use the same alias)
 *   - `next/navigation`, `next/link`, `next/font/google` resolve to src/shims
 *   - `process.env.NEXT_PUBLIC_API_URL` becomes the role's virtual origin (see agent-host.ts)
 */
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig, type Plugin } from 'vite';
import { ISOLATION_HEADERS, contentTypeFor } from '../server/headers.mjs';
import { builtAssetUrl, viteBaseFromEnv } from '../build/public-base.mjs';

const appRoot = dirname(fileURLToPath(import.meta.url));
const demoRoot = resolve(appRoot, '..');
const playgroundRoot = process.env.PLAYGROUND_DIR ? resolve(process.env.PLAYGROUND_DIR) : join(demoRoot, 'playground');
const playgroundPublic = join(playgroundRoot, 'public');
const mariadbDist = join(playgroundRoot, 'node_modules/lite4mariadb/dist');
const vendorRoot = join(appRoot, 'src/vendor');

const VIRTUAL_ORIGINS: Record<string, string> = {
  'storefront-web': 'http://shopping.agent-host.invalid',
  'merchant-web': 'http://merchant.agent-host.invalid',
};

/** Emulate a header-less static host (server/index.mjs --no-isolation-headers) to test the SW fallback. */
const isolationHeaders: Record<string, string> = process.env.DEMO_NO_ISOLATION_HEADERS ? {} : { ...ISOLATION_HEADERS };

function vendorAppOf(importer: string | undefined): string | null {
  if (!importer) return null;
  const normalized = importer.split('\\').join('/');
  const marker = '/src/vendor/';
  const at = normalized.indexOf(marker);
  if (at < 0) return null;
  const rest = normalized.slice(at + marker.length);
  return rest.split('/')[0] || null;
}

/** `@/lib/api` inside src/vendor/<app>/… → src/vendor/<app>/lib/api (per-app alias). */
function nextAppCompat(): Plugin {
  return {
    name: 'demo-next-app-compat',
    enforce: 'pre',
    resolveId(source, importer) {
      if (source.startsWith('@/')) {
        const app = vendorAppOf(importer);
        if (!app) return null;
        return this.resolve(join(vendorRoot, app, source.slice(2)), importer, { skipSelf: true });
      }
      return null;
    },
    transform(code, id) {
      const app = vendorAppOf(id);
      if (!app || !/\.(ts|tsx)$/.test(id)) return null;
      if (!code.includes('process.env.NEXT_PUBLIC_API_URL')) return null;
      const origin = VIRTUAL_ORIGINS[app];
      if (!origin) return null;
      return { code: code.split('process.env.NEXT_PUBLIC_API_URL').join(JSON.stringify(origin)), map: null };
    },
  };
}

function sendFile(res: import('node:http').ServerResponse, filePath: string): boolean {
  if (!existsSync(filePath) || statSync(filePath).isDirectory()) return false;
  res.writeHead(200, {
    'Content-Type': contentTypeFor(extname(filePath)),
    'Cache-Control': 'no-store',
    ...isolationHeaders,
  });
  createReadStream(filePath).pipe(res);
  return true;
}

/** Dev only: serve the playground engine + version bundles next to the Vite app. */
function playgroundEngine(): Plugin {
  return {
    name: 'demo-playground-engine',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        for (const [key, value] of Object.entries(isolationHeaders)) res.setHeader(key, value);
        const url = req.url || '/';
        const pathname = decodeURIComponent(url.split('?')[0]);
        if (pathname.startsWith('/@') || pathname.startsWith('/src/') || pathname.startsWith('/node_modules/') || pathname.startsWith('/demo/')) {
          next();
          return;
        }
        if (pathname.startsWith('/mariadb/')) {
          if (!sendFile(res, join(mariadbDist, pathname.slice('/mariadb/'.length)))) res.writeHead(404).end('not found');
          return;
        }
        if (pathname.startsWith('/php/')) {
          if (!sendFile(res, join(playgroundRoot, 'php', pathname.slice('/php/'.length)))) res.writeHead(404).end('not found');
          return;
        }
        if (
          pathname === '/browser-worker.js' ||
          pathname === '/service-worker.js' ||
          pathname === '/versions.json' ||
          pathname.startsWith('/versions/') ||
          pathname.startsWith('/assets/')
        ) {
          if (pathname === '/service-worker.js') res.setHeader('Service-Worker-Allowed', '/');
          if (!sendFile(res, join(playgroundPublic, pathname))) res.writeHead(404).end('not found: run npm run build:bundle');
          return;
        }
        // Static fallback of the bundled version assets when the SW is not (yet) in control.
        if (/^\/(bundles|theme|media|thumbnail)\//.test(pathname)) {
          try {
            const manifest = JSON.parse(readFileSync(join(playgroundPublic, 'versions.json'), 'utf8')) as { default?: string };
            if (manifest.default && sendFile(res, join(playgroundPublic, 'versions', manifest.default, 'assets', pathname))) return;
          } catch {
            /* no manifest yet */
          }
        }
        // Top-level navigations to Shopware routes (e.g. a reload on /checkout/cart) get the
        // shell, which opens that path inside the storefront frame.
        if ((req.method || 'GET') === 'GET' && (pathname === '/index.php' || pathname.startsWith('/index.php/'))) {
          req.url = '/' + (url.includes('?') ? url.slice(url.indexOf('?')) : '');
        }
        next();
      });
    },
  };
}

export default defineConfig(({ command }) => ({
  root: appRoot,
  publicDir: 'public',
  appType: 'spa',
  base: viteBaseFromEnv(),
  plugins: [nextAppCompat(), react(), tailwindcss(), playgroundEngine()],
  resolve: {
    alias: {
      'web-shared': join(vendorRoot, 'web-shared'),
      'next/navigation': join(appRoot, 'src/shims/next-navigation.tsx'),
      'next/link': join(appRoot, 'src/shims/next-link.tsx'),
      'next/font/google': join(appRoot, 'src/shims/next-font-google.ts'),
    },
    dedupe: ['react', 'react-dom'],
  },
  define: {
    __DEMO_BUILD_MODE__: JSON.stringify(command),
  },
  server: {
    host: '127.0.0.1',
    port: Number(process.env.PORT || 4188),
    strictPort: true,
    headers: { ...isolationHeaders, 'Service-Worker-Allowed': '/' },
    fs: { allow: [demoRoot, resolve(demoRoot, '..')] },
  },
  worker: { format: 'es' },
  // Absolute URLs under Vite `base` so lazy chunks (MerchantView, next-navigation)
  // never resolve against the origin root on GitHub project Pages.
  experimental: {
    renderBuiltUrl(filename) {
      return builtAssetUrl(filename);
    },
  },
  build: {
    outDir: join(appRoot, 'dist'),
    emptyOutDir: true,
    assetsDir: 'demo/assets',
    sourcemap: false,
    target: 'es2022',
  },
}));
