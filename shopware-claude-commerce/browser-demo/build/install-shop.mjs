#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Step 2: the Shopware 6.7.13.1 vendor tree with our plugins, on the host (Composer).
 *
 *  - composer install (playground lockfile: symfony/mcp-bundle + mcp/sdk already in)
 *  - SwagAgenticCommerce cloned into custom/plugins at the pinned ref
 *  - CommerceAgentsHandoff, SwagCommerceAgentTools (repo) and DemoOverlay (browser-demo)
 *    copied into custom/plugins
 *  - `composer require` for all of them (path repositories, symlinked) so plugin:install
 *    inside PHP WASM never shells out to Composer ("Could not execute composer require")
 *  - the playground's installer patch, then patches/vendor-*.patch (Fibers-free mcp/sdk,
 *    ucp-php-sdk connection through the bridged MariaDB WASM driver)
 */
import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import { basename, join } from 'node:path';
import {
  DEMO_ROOT,
  REPO_PLUGINS,
  SHOP_DIR,
  SWAG_AGENTIC_COMMERCE_REF,
  SWAG_AGENTIC_COMMERCE_REPO,
  SWAG_AGENTIC_COMMERCE_VERSION,
  UCP_SDK_CONSTRAINT,
} from './config.mjs';
import { isDir, isFile, log, requireTool, run, tryRun } from './lib.mjs';

requireTool('php');
requireTool('composer');

if (!isDir(SHOP_DIR)) throw new Error(`playground shopware tree missing: ${SHOP_DIR} (run fetch-playground first)`);

const composerEnv = { COMPOSER_MEMORY_LIMIT: '-1', COMPOSER_NO_INTERACTION: '1' };

if (!isFile(join(SHOP_DIR, 'vendor/autoload.php'))) {
  run('composer', ['install', '--no-scripts', '--no-progress'], { cwd: SHOP_DIR, env: composerEnv });
}

// --- SwagAgenticCommerce (git, pinned) -------------------------------------------------------
const pluginsDir = join(SHOP_DIR, 'custom/plugins');
mkdirSync(pluginsDir, { recursive: true });
const swagDir = join(pluginsDir, 'SwagAgenticCommerce');
if (!isDir(join(swagDir, '.git'))) {
  if (existsSync(swagDir)) rmSync(swagDir, { recursive: true, force: true });
  run('git', ['clone', '--quiet', SWAG_AGENTIC_COMMERCE_REPO, swagDir]);
}
const swagHead = tryRun('git', ['rev-parse', 'HEAD'], { cwd: swagDir }).stdout.trim();
if (swagHead !== SWAG_AGENTIC_COMMERCE_REF) {
  if (tryRun('git', ['cat-file', '-e', `${SWAG_AGENTIC_COMMERCE_REF}^{commit}`], { cwd: swagDir }).status !== 0) {
    run('git', ['fetch', '--quiet', 'origin', SWAG_AGENTIC_COMMERCE_REF], { cwd: swagDir });
  }
  run('git', ['checkout', '--quiet', '--detach', SWAG_AGENTIC_COMMERCE_REF], { cwd: swagDir });
}
log(`SwagAgenticCommerce at ${SWAG_AGENTIC_COMMERCE_REF.slice(0, 7)}`);

// --- plugins from this repository (read-only copies) -------------------------------------------
const SKIP_DIRS = new Set(['tests', 'node_modules', '.git', '.idea', 'var']);
for (const plugin of REPO_PLUGINS) {
  if (!isDir(plugin.source)) throw new Error(`plugin source missing: ${plugin.source}`);
  const target = join(pluginsDir, plugin.name);
  rmSync(target, { recursive: true, force: true });
  cpSync(plugin.source, target, {
    recursive: true,
    filter: (src) => !SKIP_DIRS.has(basename(src)),
  });
  log(`copied ${plugin.name} → custom/plugins/${plugin.name}`);
}

// --- composer require (path repos custom/plugins/* are already configured) ---------------------
const requirements = [
  `shopware/agentic-commerce:${SWAG_AGENTIC_COMMERCE_VERSION}`,
  `ucp-php-sdk/symfony-bundle:${UCP_SDK_CONSTRAINT}`,
];
for (const plugin of REPO_PLUGINS) {
  const manifest = JSON.parse(
    tryRun('cat', [join(plugin.source, 'composer.json')]).stdout || '{}'
  );
  if (manifest.name !== plugin.composer) {
    throw new Error(`${plugin.name}: composer name ${manifest.name} ≠ ${plugin.composer}`);
  }
  requirements.push(`${plugin.composer}:${manifest.version}`);
}
const installed = tryRun('composer', ['show', '--format=json', '--locked'], { cwd: SHOP_DIR, env: composerEnv });
const lockedNames = new Set(
  (JSON.parse(installed.stdout || '{"locked":[]}').locked || []).map((p) => p.name)
);
const missing = requirements.filter((req) => !lockedNames.has(req.split(':')[0]));
if (missing.length) {
  run('composer', ['require', '--no-scripts', '--no-progress', '--update-no-dev', ...missing], {
    cwd: SHOP_DIR,
    env: composerEnv,
  });
} else {
  // Sources of path packages may have changed: refresh the symlinked installs.
  run('composer', ['install', '--no-scripts', '--no-progress'], { cwd: SHOP_DIR, env: composerEnv });
}

// The playground's post-install script (SQL bridge into public/index.php, proc_open stubs).
run('php', ['overrides/patch-installer.php'], { cwd: SHOP_DIR });

// Our vendor patches (idempotent).
run('bash', [join(DEMO_ROOT, 'patches/apply-patches.sh'), 'vendor', SHOP_DIR]);

log('shop vendor tree ready');
