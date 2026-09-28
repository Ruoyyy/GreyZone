#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * `npm run build` — the whole pipeline, in order. Each step is its own script and can be
 * re-run alone (see package.json "build:*"); steps that are expensive skip work that is
 * already present unless FORCE_* is set:
 *
 *   1. fetch-playground   clone FriendsOfShopware/shopware-playground @ pinned commit, patch, npm install
 *   2. install-shop       composer install Shopware 6.7.13.x + our plugins, vendor patches   (FORCE_INSTALL=1)
 *   3. prepare-shop       install + seed in Node PHP WASM, dump, shop-config.json           (FORCE_INSTALL=1)
 *   4. bundle-shop        MEMFS zip, static bundles, workers, versions.json                  (FORCE_ZIP=1)
 *   5. sync-backends      copy Python backends + web UIs read-only, wheels, Pyodide
 *   6. build:app          vite build of the shell
 *   7. assemble-site      dist/site
 *
 * Requirements: node ≥ 22, npm, git, php ≥ 8.2 with composer, python3 ≥ 3.11 with pip, zip,
 * rsync, network (GitHub, Packagist, PyPI, Pyodide CDN).
 */
import { join } from 'node:path';
import { DEMO_ROOT } from './config.mjs';
import { log, run } from './lib.mjs';

const startedAt = Date.now();
const steps = [
  ['fetch playground', () => run('node', [join(DEMO_ROOT, 'build/fetch-playground.mjs')])],
  ['install shop', () => run('node', [join(DEMO_ROOT, 'build/install-shop.mjs')])],
  ['seed CI fixtures', () => run('node', [join(DEMO_ROOT, 'build/seed-ci-fixtures.mjs')])],
  ['prepare shop', () => run('node', [join(DEMO_ROOT, 'build/prepare-shop.mjs')])],
  ['bundle shop', () => run('node', [join(DEMO_ROOT, 'build/bundle-shop.mjs')])],
  ['sync backends', () => run('bash', [join(DEMO_ROOT, 'scripts/sync-backends.sh')])],
  ['build wheels', () => run('bash', [join(DEMO_ROOT, 'build/build-wheels.sh')])],
  ['fetch pyodide', () => run('node', [join(DEMO_ROOT, 'build/fetch-pyodide.mjs')])],
  ['build app', () => run('npx', ['vite', 'build', '--config', join(DEMO_ROOT, 'app/vite.config.ts')], { cwd: DEMO_ROOT })],
  ['assemble site', () => run('node', [join(DEMO_ROOT, 'build/assemble-site.mjs')])],
];

const only = process.argv.slice(2);
for (const [name, step] of steps) {
  if (only.length && !only.some((needle) => name.includes(needle))) continue;
  log(`==> ${name}`);
  const t0 = Date.now();
  step();
  log(`<== ${name} (${Math.round((Date.now() - t0) / 1000)} s)`);
}
log(`build finished in ${Math.round((Date.now() - startedAt) / 1000)} s — Pages: push to main (workflow pages.yml). Local: npm start`);
