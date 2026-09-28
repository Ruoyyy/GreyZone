#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * GitHub Actions: PHP WASM migrations trap on hosted runners; local prepare-shop
 * produces the seed dump. Copy the tracked ci-fixtures into the playground bundle
 * so prepare-shop.mjs skips the in-WASM installer (dump + install.lock + shop-config)
 * and bundle-shop.mjs can still publish compiled theme/media (no theme:compile on CI).
 *
 * Regenerate after bootstrap changes:
 *   FORCE_INSTALL=1 node build/prepare-shop.mjs
 *   cp playground/public/versions/<v>/shopware.sql.gz ci-fixtures/<v>/
 *   cp app/public/demo/shop-config.json ci-fixtures/<v>/
 *   tar -C playground/shopware/public -czf ci-fixtures/<v>/public-assets.tar.gz \
 *     theme/<compiled-hash> theme/<storefront-theme-id> media
 */
import { cpSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { pathToFileURL } from 'node:url';
import { APP_PUBLIC_DEMO, DEMO_ROOT, PLAYGROUND_DIR, PLAYGROUND_PUBLIC, SHOP_DIR } from './config.mjs';
import {
  extractPublicAssets,
  fixtureArchivePath,
  handoffSecretFromShopConfig,
  writeShopEnvLocal,
} from './ci-public-assets.mjs';
import { log } from './lib.mjs';

const { detectShopwareVersion } = await import(
  pathToFileURL(join(PLAYGROUND_DIR, 'src/shopware-version.mjs')).href,
);

if (process.env.GITHUB_ACTIONS !== 'true') {
  process.exit(0);
}

const version = detectShopwareVersion(SHOP_DIR);
if (!version) throw new Error('cannot detect Shopware version for CI fixtures');

const fixtureDir = join(DEMO_ROOT, 'ci-fixtures', version);
const dumpSrc = join(fixtureDir, 'shopware.sql.gz');
const configSrc = join(fixtureDir, 'shop-config.json');
const archiveSrc = fixtureArchivePath(fixtureDir);
if (!existsSync(dumpSrc) || !existsSync(configSrc)) {
  throw new Error(`CI fixtures missing for ${version} in ${fixtureDir}`);
}
if (!existsSync(archiveSrc)) {
  throw new Error(`CI public-asset archive missing: ${archiveSrc}`);
}

const versionDir = join(PLAYGROUND_PUBLIC, 'versions', version);
mkdirSync(versionDir, { recursive: true });
cpSync(dumpSrc, join(versionDir, 'shopware.sql.gz'));
mkdirSync(APP_PUBLIC_DEMO, { recursive: true });
cpSync(configSrc, join(APP_PUBLIC_DEMO, 'shop-config.json'));
writeFileSync(join(SHOP_DIR, 'install.lock'), `ci-fixture ${new Date().toISOString()}\n`);
extractPublicAssets(archiveSrc, join(SHOP_DIR, 'public'));
writeShopEnvLocal(SHOP_DIR, { handoffSecret: handoffSecretFromShopConfig(configSrc) });
log(`CI fixtures seeded for Shopware ${version} (prepare-shop will skip WASM install; theme/media extracted)`);
