// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  builtAssetUrl,
  injectPublicBaseBanner,
  prefixManifestUrls,
  prepareEngineWorkerSource,
  publicBasePrefix,
  rewriteEngineSource,
  viteBaseFromEnv,
} from './public-base.mjs';

describe('public base (GitHub Pages project path)', () => {
  it('normalises DEMO_BASE_PATH for Vite', () => {
    assert.equal(viteBaseFromEnv({}), '/');
    assert.equal(viteBaseFromEnv({ DEMO_BASE_PATH: '/' }), '/');
    assert.equal(viteBaseFromEnv({ DEMO_BASE_PATH: '' }), '/');
    assert.equal(viteBaseFromEnv({ DEMO_BASE_PATH: '/shopware-claude-commerce' }), '/shopware-claude-commerce/');
    assert.equal(viteBaseFromEnv({ DEMO_BASE_PATH: '/shopware-claude-commerce/' }), '/shopware-claude-commerce/');
    assert.equal(viteBaseFromEnv({ DEMO_BASE_PATH: 'shopware-claude-commerce' }), '/shopware-claude-commerce/');
  });

  it('uses an empty worker prefix at the origin root', () => {
    assert.equal(publicBasePrefix('/'), '');
    assert.equal(publicBasePrefix('/shopware-claude-commerce/'), '/shopware-claude-commerce');
  });

  it('rewrites quoted engine paths once and leaves already-prefixed paths alone', () => {
    const source = `import { Lite4MariaDB } from '/mariadb/index.mjs'; fetch('/php/auto_prepend.php'); fetch("/versions/6.7.13.1/shopware.zip");`;
    const prefix = '/shopware-claude-commerce';
    const once = rewriteEngineSource(source, prefix);
    assert.match(once, /from '\/shopware-claude-commerce\/mariadb\/index\.mjs'/);
    assert.match(once, /fetch\('\/shopware-claude-commerce\/php\/auto_prepend\.php'\)/);
    assert.match(once, /fetch\("\/shopware-claude-commerce\/versions\/6\.7\.13\.1\/shopware\.zip"\)/);
    assert.equal(rewriteEngineSource(once, prefix), once);
    assert.equal(rewriteEngineSource(source, ''), source);
  });

  it('does not prefix service-worker route constants that withoutPublicBase already strips', () => {
    const source = [
      'var DEMO_STATIC_PREFIX = "/demo/";',
      'var DEMO_PROXY_PREFIX = "/api/anthropic/";',
      'pathname.startsWith("/php/");',
      'pathname === "/browser-worker.js";',
      '',
    ].join('\n');
    const prefix = '/shopware-claude-commerce';
    const prepared = prepareEngineWorkerSource('service-worker.js', source, prefix);
    assert.match(prepared, /self\.__DEMO_PUBLIC_BASE__ = "\/shopware-claude-commerce"/);
    assert.match(prepared, /DEMO_STATIC_PREFIX = "\/demo\/"/);
    assert.match(prepared, /DEMO_PROXY_PREFIX = "\/api\/anthropic\/"/);
    assert.match(prepared, /startsWith\("\/php\/"\)/);
    assert.match(prepared, /=== "\/browser-worker.js"/);
    assert.doesNotMatch(prepared, /DEMO_STATIC_PREFIX = "\/shopware-claude-commerce\/demo\/"/);
    assert.equal(prepareEngineWorkerSource('playground/public/service-worker.js', source, prefix), prepared);
  });

  it('still prefixes browser-worker resource URLs for project Pages', () => {
    const source = `import { Lite4MariaDB } from '/mariadb/index.mjs'; fetch('/php/auto_prepend.php');\n`;
    const prepared = prepareEngineWorkerSource('browser-worker.js', source, '/shopware-claude-commerce');
    assert.match(prepared, /from '\/shopware-claude-commerce\/mariadb\/index\.mjs'/);
    assert.match(prepared, /fetch\('\/shopware-claude-commerce\/php\/auto_prepend\.php'\)/);
  });

  it('injects a rewritable __DEMO_PUBLIC_BASE__ banner', () => {
    const first = injectPublicBaseBanner('console.log(1);\n', '/shopware-claude-commerce');
    assert.equal(first, 'self.__DEMO_PUBLIC_BASE__ = "/shopware-claude-commerce";\nconsole.log(1);\n');
    const second = injectPublicBaseBanner(first, '');
    assert.equal(second, 'self.__DEMO_PUBLIC_BASE__ = "";\nconsole.log(1);\n');
  });

  it('emits origin-absolute Vite chunk URLs under the Pages prefix', () => {
    assert.equal(
      builtAssetUrl('demo/assets/MerchantView-DgL7vdsf.js', '/shopware-claude-commerce/'),
      '/shopware-claude-commerce/demo/assets/MerchantView-DgL7vdsf.js',
    );
    assert.equal(builtAssetUrl('demo/assets/next-navigation-BpM7TX4o.js', '/'), '/demo/assets/next-navigation-BpM7TX4o.js');
    assert.equal(
      builtAssetUrl('/demo/assets/ShoppingView-Vb3o-FWp.js', '/shopware-claude-commerce'),
      '/shopware-claude-commerce/demo/assets/ShoppingView-Vb3o-FWp.js',
    );
  });

  it('prefixes versions.json zip and dump URLs', () => {
    const manifest = {
      default: '6.7.13.1',
      versions: [{ id: '6.7.13.1', zip: '/versions/6.7.13.1/shopware.zip', dump: '/versions/6.7.13.1/shopware.sql.gz' }],
    };
    const prefixed = prefixManifestUrls(manifest, '/shopware-claude-commerce');
    assert.equal(prefixed.versions[0].zip, '/shopware-claude-commerce/versions/6.7.13.1/shopware.zip');
    assert.equal(prefixManifestUrls(prefixed, '/shopware-claude-commerce').versions[0].zip, prefixed.versions[0].zip);
    assert.equal(prefixManifestUrls(manifest, '').versions[0].zip, '/versions/6.7.13.1/shopware.zip');
  });
});
