// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import {
  FIXTURE_MEDIA_LOGO,
  FIXTURE_THEME_CSS,
  FIXTURE_THEME_JS,
} from './config.mjs';
import {
  REQUIRED_PUBLIC_ASSETS,
  assertPublishedStorefrontAssets,
  extractPublicAssets,
  fixtureArchivePath,
  handoffSecretFromShopConfig,
  listPublicAssetArchive,
  missingPublicAssets,
  writeShopEnvLocal,
} from './ci-public-assets.mjs';

const FIXTURE_DIR = new URL('../ci-fixtures/6.7.13.1/', import.meta.url);
const ARCHIVE = fixtureArchivePath(FIXTURE_DIR.pathname);

describe('CI public theme/media fixtures', () => {
  it('ships the compiled Storefront theme and seed media in the archive', () => {
    const entries = new Set(listPublicAssetArchive(ARCHIVE));
    for (const rel of REQUIRED_PUBLIC_ASSETS) {
      assert.equal(entries.has(rel), true, rel);
    }
  });

  it('extracts all.css, storefront.js and dump media into shopware/public', () => {
    const dest = mkdtempSync(join(tmpdir(), 'ca-public-assets-'));
    try {
      extractPublicAssets(ARCHIVE, dest);
      assertPublishedStorefrontAssets(dest);
      assert.ok(readFileSync(join(dest, FIXTURE_THEME_CSS)).byteLength > 10_000);
      assert.ok(readFileSync(join(dest, FIXTURE_THEME_JS)).byteLength > 1000);
      assert.ok(readFileSync(join(dest, FIXTURE_MEDIA_LOGO)).byteLength > 100);
    } finally {
      rmSync(dest, { recursive: true, force: true });
    }
  });

  it('refuses a version bundle that only has administration JS', () => {
    const dest = mkdtempSync(join(tmpdir(), 'ca-public-assets-empty-'));
    try {
      assert.deepEqual(missingPublicAssets(dest), [...REQUIRED_PUBLIC_ASSETS]);
      assert.throws(() => assertPublishedStorefrontAssets(dest), /all\.css|theme\/media missing/i);
    } finally {
      rmSync(dest, { recursive: true, force: true });
    }
  });

  it('writes MCP_SERVER=1 so Admin MCP is not a 404 when prepare-shop is skipped', () => {
    const dest = mkdtempSync(join(tmpdir(), 'ca-env-local-'));
    try {
      const secret = handoffSecretFromShopConfig(new URL('../ci-fixtures/6.7.13.1/shop-config.json', import.meta.url).pathname);
      writeShopEnvLocal(dest, { handoffSecret: secret });
      const env = readFileSync(join(dest, '.env.local'), 'utf8');
      assert.match(env, /^MCP_SERVER=1$/m);
      assert.match(env, new RegExp(`COMMERCE_AGENTS_HANDOFF_SECRET=${secret}`));
    } finally {
      rmSync(dest, { recursive: true, force: true });
    }
  });
});
