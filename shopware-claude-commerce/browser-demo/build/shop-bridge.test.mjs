// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { extractStorefrontProduct, phpRequestUrl } from '../app/src/engine/shop-bridge.mjs';

describe('phpRequestUrl', () => {
  it('keeps a Pages shop URL intact', () => {
    const href = 'https://agentic-commerce-lab.github.io/shopware-claude-commerce/store-api/search?search=a';
    assert.equal(phpRequestUrl(href, '/shopware-claude-commerce'), href);
  });

  it('restores the Pages prefix when httpx dropped it', () => {
    assert.equal(
      phpRequestUrl('https://agentic-commerce-lab.github.io/store-api/search?search=a', '/shopware-claude-commerce'),
      'https://agentic-commerce-lab.github.io/shopware-claude-commerce/store-api/search?search=a',
    );
    assert.equal(
      phpRequestUrl('https://agentic-commerce-lab.github.io/api/_mcp', '/shopware-claude-commerce'),
      'https://agentic-commerce-lab.github.io/shopware-claude-commerce/api/_mcp',
    );
  });

  it('leaves a pathless local origin alone', () => {
    const href = 'http://127.0.0.1:4188/store-api/search';
    assert.equal(phpRequestUrl(href, ''), href);
    assert.equal(phpRequestUrl(href, '/'), href);
  });
});

describe('extractStorefrontProduct', () => {
  it('reads overlay data attributes', () => {
    const html = '<div class="ca-demo" data-product-id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" data-product-name="Variant product"></div>';
    assert.deepEqual(extractStorefrontProduct(html), {
      id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      name: 'Variant product',
    });
  });

  it('reads the Shopware buy-widget referencedId', () => {
    const html = `
      <h1 class="product-detail-name">Variant product</h1>
      <input type="hidden" name="lineItems[0][referencedId]" value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">
    `;
    assert.deepEqual(extractStorefrontProduct(html), {
      id: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      name: 'Variant product',
    });
  });

  it('returns null off a listing page', () => {
    assert.equal(extractStorefrontProduct('<div class="cms-listing">Clothing</div>'), null);
  });
});
