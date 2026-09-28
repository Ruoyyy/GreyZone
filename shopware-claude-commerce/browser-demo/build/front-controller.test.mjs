// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { describe, it } from 'node:test';

const FRONT = new URL('../playground/src/front-controller.mjs', import.meta.url);
const APP_ROUTE = new URL('../playground/src/app-route.mjs', import.meta.url);
const playgroundPresent = existsSync(FRONT);

describe('Shopware front-controller APP_URL under a project Pages prefix', { skip: playgroundPresent ? false : 'playground not fetched' }, async () => {
  if (!playgroundPresent) return;

  const { indexPhpKeepsPublicBase, patchShopwareIndexAppUrl } = await import(FRONT.href);
  const { phpRequestHeaders } = await import(APP_ROUTE.href);

  it('appends the playground public base to origin-only APP_URL', () => {
    const src = "    $appUrl = $scheme . '://' . $host;\n    $_SERVER['APP_URL'] = $appUrl;\n";
    const next = patchShopwareIndexAppUrl(src);
    assert.equal(next.changed, true);
    assert.equal(indexPhpKeepsPublicBase(next.source), true);
    assert.match(next.source, /\$appUrl \.= \$base/);
    assert.equal(patchShopwareIndexAppUrl(next.source).changed, false);
  });

  it('strips hop-by-hop and X-Forwarded headers before PHP WASM', () => {
    const out = phpRequestHeaders({
      Host: 'agentic-commerce-lab.github.io',
      Accept: 'text/html',
      'Accept-Encoding': 'gzip, deflate, br',
      'X-Forwarded-Host': 'agentic-commerce-lab.github.io',
      'X-Forwarded-Proto': 'https',
      'X-Forwarded-Prefix': '/shopware-claude-commerce',
      Cookie: 'sw_playground_version=6.7.13.1',
    });
    assert.equal(out.Host, 'agentic-commerce-lab.github.io');
    assert.equal(out.Accept, 'text/html');
    assert.equal(out.Cookie, 'sw_playground_version=6.7.13.1');
    assert.equal(out['Accept-Encoding'], undefined);
    assert.equal(out['X-Forwarded-Host'], undefined);
    assert.equal(out['X-Forwarded-Prefix'], undefined);
  });
});
