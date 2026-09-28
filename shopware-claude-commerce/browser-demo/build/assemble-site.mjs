#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Assemble the self-contained static site in dist/site:
 *
 *   /index.html, /demo/assets/*           the Vite-built shell (app/dist)
 *   /demo/{pyodide,wheels,host,…}         agent host artifacts (app/public/demo, copied by Vite)
 *   /browser-worker.js, /service-worker.js, /assets/*   playground engine (PHP WASM, ICU, intl)
 *   /mariadb/*                            lite4mariadb (MariaDB WASM)
 *   /php/auto_prepend.php                 playground PHP prelude
 *   /versions.json, /versions/<v>/…       Shopware MEMFS zip, seed dump, static bundles/theme/media
 *   /demo/build-info.json                 sizes + versions of everything above
 *
 * Serve with `npm start` (server/index.mjs) or GitHub Pages. Pages cannot set COOP/COEP;
 * the playground service worker adds them (coi-serviceworker behaviour). DEMO_BASE_PATH
 * prefixes engine URLs for project Pages (`/shopware-claude-commerce/`). The service
 * worker keeps logical `/demo/` and `/php/` route constants; only `browser-worker.js`
 * has those quoted paths rewritten (see prepareEngineWorkerSource).
 */
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { APP_DIR, DEMO_ROOT, PLAYGROUND_COMMIT, PLAYGROUND_DIR, PLAYGROUND_PUBLIC, PYODIDE_VERSION, SITE_DIR } from './config.mjs';
import { formatBytes, log } from './lib.mjs';
import {
  pagesFileLimitBytes,
  prefixManifestUrls,
  prepareEngineWorkerSource,
  publicBasePrefix,
  viteBaseFromEnv,
} from './public-base.mjs';

const appDist = join(APP_DIR, 'dist');
const mariadbDist = join(PLAYGROUND_DIR, 'node_modules', 'lite4mariadb', 'dist');
const publicPrefix = publicBasePrefix(viteBaseFromEnv());

function required(path, hint) {
  if (!existsSync(path)) throw new Error(`${path} missing — ${hint}`);
}

required(join(appDist, 'index.html'), 'run npm run build:app');
required(join(PLAYGROUND_PUBLIC, 'browser-worker.js'), 'run npm run build:bundle');
required(join(PLAYGROUND_PUBLIC, 'versions.json'), 'run npm run build:bundle');
required(join(mariadbDist, 'lite4mariadb.wasm'), 'run npm run build:playground');
required(join(appDist, 'demo', 'pyodide', 'pyodide.asm.wasm'), 'run npm run build:host');
required(join(appDist, 'demo', 'shop-config.json'), 'run npm run build:prepare');

rmSync(SITE_DIR, { recursive: true, force: true });
mkdirSync(SITE_DIR, { recursive: true });

cpSync(appDist, SITE_DIR, { recursive: true });

// Engine: workers + only the hashed assets they reference (the playground's public/assets
// accumulates stale hashes across rebuilds).
const engineFiles = ['browser-worker.js', 'service-worker.js'];
const referenced = new Set();
for (const file of engineFiles) {
  const source = readFileSync(join(PLAYGROUND_PUBLIC, file), 'utf8');
  // php-wasm side modules are referenced as `assets/<name>.so?url` — the query is not part of the file.
  for (const match of source.matchAll(/["'](?:\/)?assets\/([A-Za-z0-9._-]+)(?:\?[A-Za-z0-9=&_-]*)?["']/g)) referenced.add(match[1]);
  writeFileSync(join(SITE_DIR, file), prepareEngineWorkerSource(file, source, publicPrefix));
}
mkdirSync(join(SITE_DIR, 'assets'), { recursive: true });
for (const name of referenced) {
  required(join(PLAYGROUND_PUBLIC, 'assets', name), 'run npm run build:bundle');
  cpSync(join(PLAYGROUND_PUBLIC, 'assets', name), join(SITE_DIR, 'assets', name));
}
cpSync(mariadbDist, join(SITE_DIR, 'mariadb'), { recursive: true });
cpSync(join(PLAYGROUND_DIR, 'php'), join(SITE_DIR, 'php'), { recursive: true });

// Shopware version bundles. Prefix zip/dump URLs when the site is not at `/`.
const manifest = prefixManifestUrls(JSON.parse(readFileSync(join(PLAYGROUND_PUBLIC, 'versions.json'), 'utf8')), publicPrefix);
writeFileSync(join(SITE_DIR, 'versions.json'), JSON.stringify(manifest, null, 2) + '\n');
for (const version of manifest.versions || []) {
  const from = join(PLAYGROUND_PUBLIC, 'versions', version.id);
  required(join(from, 'shopware.zip'), 'run npm run build:bundle');
  required(join(from, 'shopware.sql.gz'), 'run npm run build:prepare');
  cpSync(from, join(SITE_DIR, 'versions', version.id), { recursive: true });
}

// Sizes: what a static host has to hold, grouped the way the README documents it.
function dirSize(path) {
  if (!existsSync(path)) return 0;
  const stat = statSync(path);
  if (stat.isFile()) return stat.size;
  return readdirSync(path).reduce((sum, entry) => sum + dirSize(join(path, entry)), 0);
}
const groups = {
  shell: ['index.html', 'demo/assets'],
  engine: ['browser-worker.js', 'service-worker.js', 'assets', 'mariadb', 'php'],
  shopware: ['versions.json', 'versions'],
  pyodide: ['demo/pyodide'],
  agents: ['demo/wheels', 'demo/host', 'demo/shop-config.json'],
};
const sizes = Object.fromEntries(Object.entries(groups).map(([name, paths]) => [name, paths.reduce((sum, p) => sum + dirSize(join(SITE_DIR, p)), 0)]));
const total = dirSize(SITE_DIR);
const largest = [];
(function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) walk(full);
    else if (stat.size > 5 * 1024 * 1024) largest.push({ path: '/' + relative(SITE_DIR, full), bytes: stat.size });
  }
})(SITE_DIR);
largest.sort((a, b) => b.bytes - a.bytes);

const shopConfig = JSON.parse(readFileSync(join(SITE_DIR, 'demo', 'shop-config.json'), 'utf8'));
const buildInfo = {
  builtAt: new Date().toISOString(),
  shopwareVersion: shopConfig.shopwareVersion,
  playgroundCommit: PLAYGROUND_COMMIT,
  pyodide: PYODIDE_VERSION,
  sizes: { ...sizes, total },
  largestFiles: largest,
};
writeFileSync(join(SITE_DIR, 'demo', 'build-info.json'), JSON.stringify({ ...buildInfo, publicBase: publicPrefix || '/' }, null, 2) + '\n');
writeFileSync(join(SITE_DIR, '.nojekyll'), '');
cpSync(join(SITE_DIR, 'index.html'), join(SITE_DIR, '404.html'));

log(`site assembled in ${relative(DEMO_ROOT, SITE_DIR)} (${formatBytes(total)}) publicBase=${publicPrefix || '/'}`);
for (const [name, bytes] of Object.entries(sizes)) log(`  ${name.padEnd(9)} ${formatBytes(bytes)}`);
log('largest files:');
for (const file of largest.slice(0, 8)) log(`  ${formatBytes(file.bytes).padStart(9)}  ${file.path}`);
const overLimit = largest.filter((file) => file.bytes >= pagesFileLimitBytes());
if (overLimit.length) {
  for (const file of overLimit) log(`  GitHub Pages limit (100 MB): ${file.path} is ${formatBytes(file.bytes)}`);
}
