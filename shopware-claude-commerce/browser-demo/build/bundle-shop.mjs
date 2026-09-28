#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Turn the prepared Shopware tree into the browser bundle the playground engine boots:
 *
 *   playground/public/browser-worker.js, service-worker.js   esbuild (playground's build-shell.mjs)
 *   playground/public/versions/<v>/shopware.zip              MEMFS image of the Shopware tree
 *   playground/public/versions/<v>/shopware.sql.gz           seed dump (written by prepare-shop.mjs)
 *   playground/public/versions/<v>/assets/…                  bundles/theme/media served statically
 *   playground/public/versions.json                          manifest the shell boots from
 *
 * The zip excludes what the browser never needs (node_modules, logs, caches, the admin bundle
 * that is served statically) — the same exclusions as the playground's copy-assets.mjs.
 * FORCE_ZIP=1 rebuilds an existing zip.
 */
import { existsSync, mkdirSync, rmSync, statSync, cpSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { PLAYGROUND_DIR, PLAYGROUND_PUBLIC, SHOP_DIR } from './config.mjs';
import { assertPublishedStorefrontAssets } from './ci-public-assets.mjs';
import { formatBytes, isDir, log, requireTool, run } from './lib.mjs';

requireTool('zip');
if (!isDir(join(SHOP_DIR, 'vendor'))) throw new Error(`Shopware vendor tree missing in ${SHOP_DIR} — run npm run build:shop`);

const { buildWorkers } = await import(pathToFileURL(join(PLAYGROUND_DIR, 'src/build-shell.mjs')).href);
const { copyBundlePublicAssets } = await import(pathToFileURL(join(PLAYGROUND_DIR, 'src/frontend-assets.mjs')).href);
const { detectShopwareVersion, updateVersionsManifest, versionBundleDir } = await import(pathToFileURL(join(PLAYGROUND_DIR, 'src/shopware-version.mjs')).href);

const version = detectShopwareVersion(SHOP_DIR);
if (!version) throw new Error('cannot detect the Shopware version from composer.lock');
const versionDir = versionBundleDir(PLAYGROUND_PUBLIC, version);
const assetsDir = join(versionDir, 'assets');
mkdirSync(assetsDir, { recursive: true });
if (!existsSync(join(versionDir, 'shopware.sql.gz'))) throw new Error(`seed dump missing in ${versionDir} — run npm run build:prepare`);

log(`bundling Shopware ${version}`);
await buildWorkers();

copyBundlePublicAssets(SHOP_DIR);
for (const dir of ['bundles', 'theme', 'media', 'thumbnail']) {
  const from = join(SHOP_DIR, 'public', dir);
  if (!existsSync(from)) continue;
  const to = join(assetsDir, dir);
  rmSync(to, { recursive: true, force: true });
  cpSync(from, to, { recursive: true });
}
assertPublishedStorefrontAssets(assetsDir);
log(`version assets → ${assetsDir}`);

const zipPath = join(versionDir, 'shopware.zip');
if (existsSync(zipPath) && process.env.FORCE_ZIP !== '1') {
  log(`keeping ${zipPath} (${formatBytes(statSync(zipPath).size)}); FORCE_ZIP=1 to rebuild`);
} else {
  rmSync(zipPath, { force: true });
  const excludes = [
    'node_modules/*',
    'node_modules/**',
    '*/node_modules/*',
    '*/node_modules/**',
    'var/log/*',
    'var/log/**',
    'var/cache/*',
    'var/cache/**',
    'public/bundles/administration/*',
    'public/bundles/administration/**',
    '.git/*',
    '.git/**',
  ];
  run('zip', ['-qr', zipPath, '.', '-x', ...excludes], { cwd: SHOP_DIR });
  log(`wrote ${zipPath} (${formatBytes(statSync(zipPath).size)})`);
}

const manifest = updateVersionsManifest(PLAYGROUND_PUBLIC, version);
log(`versions.json: ${manifest.versions.map((entry) => entry.id).join(', ')} (default ${manifest.default})`);
