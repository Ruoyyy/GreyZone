#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Step 1: get FriendsOfShopware/shopware-playground at the pinned commit into
 * browser-demo/playground/ (gitignored), apply patches/playground-*.patch and install its
 * npm dependencies (lite4mariadb >= 0.1.2 comes from the patched package.json).
 *
 * Re-runnable: an existing clone is reset to the pinned commit before patching.
 * PLAYGROUND_DIR may point at an existing clone (e.g. a scratch checkout).
 */
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { DEMO_ROOT, PLAYGROUND_COMMIT, PLAYGROUND_DIR, PLAYGROUND_REPO } from './config.mjs';
import { isDir, log, requireTool, run, tryRun } from './lib.mjs';

requireTool('git');
requireTool('patch');

if (!isDir(join(PLAYGROUND_DIR, '.git'))) {
  if (existsSync(PLAYGROUND_DIR)) rmSync(PLAYGROUND_DIR, { recursive: true, force: true });
  log(`cloning ${PLAYGROUND_REPO} → ${PLAYGROUND_DIR}`);
  run('git', ['clone', '--quiet', '--no-checkout', PLAYGROUND_REPO, PLAYGROUND_DIR]);
}

const head = tryRun('git', ['rev-parse', 'HEAD'], { cwd: PLAYGROUND_DIR }).stdout.trim();
if (head !== PLAYGROUND_COMMIT) {
  log(`checking out playground ${PLAYGROUND_COMMIT.slice(0, 7)} (was ${head.slice(0, 7) || 'none'})`);
  if (tryRun('git', ['cat-file', '-e', `${PLAYGROUND_COMMIT}^{commit}`], { cwd: PLAYGROUND_DIR }).status !== 0) {
    run('git', ['fetch', '--quiet', 'origin', PLAYGROUND_COMMIT], { cwd: PLAYGROUND_DIR });
  }
  run('git', ['checkout', '--quiet', '--detach', PLAYGROUND_COMMIT], { cwd: PLAYGROUND_DIR });
}

// Reset the tracked files our patch touches so apply-patches.sh sees a pristine tree;
// shopware/ (composer.json/lock get `composer require`d by install-shop.mjs) is left alone.
run('git', ['checkout', '--quiet', '--', 'package.json', 'src'], { cwd: PLAYGROUND_DIR });
run('bash', [join(DEMO_ROOT, 'patches/apply-patches.sh'), 'playground', PLAYGROUND_DIR]);


const shimSrc = join(DEMO_ROOT, 'playground-shims/fs-ext-stub');
const shimDst = join(PLAYGROUND_DIR, 'shims/fs-ext-stub');
if (!existsSync(shimSrc)) {
  throw new Error(`missing playground shim: ${shimSrc} (required for @php-wasm/node on CI)`);
}
mkdirSync(join(PLAYGROUND_DIR, 'shims'), { recursive: true });
cpSync(shimSrc, shimDst, { recursive: true });


function ensureFsExtStub() {
  const fsExtDir = join(PLAYGROUND_DIR, 'node_modules/fs-ext-extra-prebuilt');
  const fsExtPkg = join(fsExtDir, 'package.json');
  if (existsSync(fsExtPkg)) {
    try {
      const pkg = JSON.parse(readFileSync(fsExtPkg, 'utf8'));
      if (pkg.version === '2.2.7-stub') return;
    } catch {
      /* reinstall */
    }
  }
  // package-lock may point fs-ext at @php-wasm/node/shims (empty); npm install then leaves the dep missing.
  rmSync(fsExtDir, { recursive: true, force: true });
  mkdirSync(join(PLAYGROUND_DIR, 'node_modules'), { recursive: true });
  log('installing fs-ext-extra-prebuilt stub (@php-wasm/node on Node without native prebuild)');
  cpSync(shimDst, fsExtDir, { recursive: true });
}

// package.json changed (lite4mariadb pin) → `npm install` rather than `npm ci`.
run('npm', ['install', '--no-audit', '--no-fund'], { cwd: PLAYGROUND_DIR });
ensureFsExtStub();
const litePackage = join(PLAYGROUND_DIR, 'node_modules/lite4mariadb/package.json');
const lite = existsSync(litePackage) ? JSON.parse(readFileSync(litePackage, 'utf8')).version : '';
if (!lite || Number(lite.split('.')[2]) < 2) {
  throw new Error(`lite4mariadb >= 0.1.2 required (NUL-byte LONGTEXT truncation fix), got ${lite || 'none'}`);
}
log(`playground ready at ${PLAYGROUND_DIR} (lite4mariadb ${lite})`);
