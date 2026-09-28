// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { describe, it } from 'node:test';

const SQL_DUMP = new URL('../playground/src/sql-dump.mjs', import.meta.url);
const playgroundPresent = existsSync(SQL_DUMP);

describe('sales-channel URL rewrite under a project Pages prefix', { skip: playgroundPresent ? false : 'playground not fetched' }, async () => {
  if (!playgroundPresent) return;

  const { shopUrlVariants } = await import(SQL_DUMP.href);

  it('keeps a path prefix on GitHub project Pages origins', () => {
    const urls = shopUrlVariants('https://agentic-commerce-lab.github.io/shopware-claude-commerce');
    assert.ok(urls.includes('https://agentic-commerce-lab.github.io/shopware-claude-commerce'));
    assert.ok(urls.includes('http://agentic-commerce-lab.github.io/shopware-claude-commerce'));
    assert.ok(!urls.includes('https://agentic-commerce-lab.github.io'));
    assert.ok(!urls.includes('http://agentic-commerce-lab.github.io'));
  });

  it('stays pathless at the origin root (local npm start)', () => {
    const urls = shopUrlVariants('http://127.0.0.1:4188');
    assert.ok(urls.includes('http://127.0.0.1:4188'));
    assert.ok(urls.includes('http://localhost:4188'));
    assert.ok(urls.includes('https://127.0.0.1:4188'));
    assert.ok(!urls.some((url) => url.includes('/shopware')));
  });
});
