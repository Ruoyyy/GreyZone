#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Checked-in Storefront theme + media for GitHub Actions.
 *
 * Pages skips the in-WASM theme compile (ci-fixtures SQL dump). Theme CSS/JS and
 * product media therefore have to be copied from this archive into
 * `shopware/public/` before `bundle-shop.mjs` publishes `/versions/<v>/assets/`.
 * Without that, the storefront HTML is 200 but `all.css` / `storefront.js` / logos
 * 404 on the static host.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  FIXTURE_MEDIA_LOGO,
  FIXTURE_MEDIA_PRODUCT,
  FIXTURE_PUBLIC_ASSETS_ARCHIVE,
  FIXTURE_THEME_CSS,
  FIXTURE_THEME_JS,
} from './config.mjs';
import { run } from './lib.mjs';

export const REQUIRED_PUBLIC_ASSETS = Object.freeze([
  FIXTURE_THEME_CSS,
  FIXTURE_THEME_JS,
  FIXTURE_MEDIA_LOGO,
  FIXTURE_MEDIA_PRODUCT,
]);

export function fixtureArchivePath(fixtureDir) {
  return join(fixtureDir, FIXTURE_PUBLIC_ASSETS_ARCHIVE);
}

export function listPublicAssetArchive(archivePath) {
  const result = run('tar', ['-tzf', archivePath], { capture: true, quiet: true });
  return String(result.stdout || '')
    .split('\n')
    .map((line) => line.replace(/^\.\//, '').replace(/\/$/, ''))
    .filter(Boolean);
}

export function extractPublicAssets(archivePath, publicDir) {
  if (!existsSync(archivePath)) {
    throw new Error(`CI public-asset archive missing: ${archivePath}`);
  }
  mkdirSync(publicDir, { recursive: true });
  run('tar', ['-xzf', archivePath, '-C', publicDir], { quiet: true });
}

export function missingPublicAssets(rootDir, required = REQUIRED_PUBLIC_ASSETS) {
  return required.filter((rel) => !existsSync(join(rootDir, rel)));
}

export function assertPublishedStorefrontAssets(rootDir, required = REQUIRED_PUBLIC_ASSETS) {
  const missing = missingPublicAssets(rootDir, required);
  if (missing.length) {
    throw new Error(
      `Storefront theme/media missing under ${rootDir}: ${missing.join(', ')}. ` +
        `On CI, seed-ci-fixtures.mjs must extract ${FIXTURE_PUBLIC_ASSETS_ARCHIVE} into shopware/public/.`,
    );
  }
}

/** Demo-only Shopware env so `/api/_mcp` is registered when prepare-shop is skipped. */
export function writeShopEnvLocal(shopDir, { handoffSecret, appUrl = 'http://127.0.0.1:4180' }) {
  if (!handoffSecret) throw new Error('handoffSecret is required for .env.local');
  const body = [
    'APP_SECRET=playground-app-secret-please-change-32ch',
    `APP_URL=${appUrl}`,
    'DATABASE_URL=mysql://root:root@localhost/shopware',
    'INSTANCE_ID=playgroundinstanceid32charsxx',
    'BLUE_GREEN_DEPLOYMENT=0',
    'SHOPWARE_HTTP_CACHE_ENABLED=0',
    'SHOPWARE_ES_ENABLED=0',
    'SHOPWARE_ES_INDEXING_ENABLED=0',
    'MAILER_DSN=null://null',
    'LOCK_DSN=flock',
    'COMPOSER_HOME=/shopware/var/cache/composer',
    'MCP_SERVER=1',
    'SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1',
    `COMMERCE_AGENTS_HANDOFF_SECRET=${handoffSecret}`,
    '',
  ].join('\n');
  writeFileSync(join(shopDir, '.env.local'), body);
}

export function handoffSecretFromShopConfig(configPath) {
  const config = JSON.parse(readFileSync(configPath, 'utf8'));
  if (!config.handoffSecret) throw new Error(`handoffSecret missing in ${configPath}`);
  return String(config.handoffSecret);
}
