#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Assemble a self-hosted Pyodide distribution under app/public/demo/pyodide/:
 *   - runtime files from the `pyodide` npm package (same version, no CDN at run time)
 *   - the Pyodide-built package wheels the agent host needs (PYODIDE_PACKAGES + their
 *     transitive `depends`), downloaded once from the Pyodide CDN into build/.cache and
 *     verified against the sha256 in pyodide-lock.json
 *   - packages.json: the package names the worker passes to loadPackage()
 */
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { APP_PUBLIC_DEMO, DEMO_ROOT, PYODIDE_CDN, PYODIDE_PACKAGES, PYODIDE_VERSION } from './config.mjs';
import { formatBytes, log } from './lib.mjs';

const require = createRequire(import.meta.url);
const pyodideDir = dirname(require.resolve('pyodide/package.json'));
const installedVersion = JSON.parse(readFileSync(join(pyodideDir, 'package.json'), 'utf8')).version;
if (installedVersion !== PYODIDE_VERSION) {
  throw new Error(`pyodide npm package is ${installedVersion}, build/config.mjs pins ${PYODIDE_VERSION} — run npm install`);
}

const RUNTIME_FILES = ['pyodide.mjs', 'pyodide.asm.mjs', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json'];
const outDir = join(APP_PUBLIC_DEMO, 'pyodide');
const cacheDir = join(DEMO_ROOT, 'build', '.cache', 'pyodide', PYODIDE_VERSION);
mkdirSync(outDir, { recursive: true });
mkdirSync(cacheDir, { recursive: true });

for (const file of RUNTIME_FILES) copyFileSync(join(pyodideDir, file), join(outDir, file));
log(`pyodide ${PYODIDE_VERSION} runtime copied from node_modules/pyodide`);

const lock = JSON.parse(readFileSync(join(pyodideDir, 'pyodide-lock.json'), 'utf8'));
const byImport = new Map();
for (const entry of Object.values(lock.packages)) for (const name of entry.imports || []) byImport.set(name, entry.name);

function resolvePackage(name) {
  const normalized = name.toLowerCase().replace(/_/g, '-');
  return lock.packages[normalized] || lock.packages[name] || (byImport.has(name) ? lock.packages[byImport.get(name)] : undefined);
}

const closure = new Map();
const queue = [...PYODIDE_PACKAGES];
while (queue.length) {
  const name = queue.shift();
  const entry = resolvePackage(name);
  if (!entry) throw new Error(`package ${name} is not in pyodide-lock.json ${PYODIDE_VERSION}`);
  if (closure.has(entry.name)) continue;
  closure.set(entry.name, entry);
  queue.push(...(entry.depends || []));
}

function sha256(file) {
  return createHash('sha256').update(readFileSync(file)).digest('hex');
}

async function fetchPackage(entry) {
  const cached = join(cacheDir, entry.file_name);
  if (existsSync(cached) && sha256(cached) === entry.sha256) return cached;
  const url = `${PYODIDE_CDN}${entry.file_name}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`download failed: ${url} (${res.status})`);
  const bytes = Buffer.from(await res.arrayBuffer());
  const digest = createHash('sha256').update(bytes).digest('hex');
  if (digest !== entry.sha256) throw new Error(`sha256 mismatch for ${entry.file_name}: ${digest} != ${entry.sha256}`);
  writeFileSync(cached, bytes);
  return cached;
}

let total = 0;
let downloaded = 0;
const entries = [...closure.values()].sort((a, b) => a.name.localeCompare(b.name));
const CONCURRENCY = 6;
for (let i = 0; i < entries.length; i += CONCURRENCY) {
  await Promise.all(
    entries.slice(i, i + CONCURRENCY).map(async (entry) => {
      const before = existsSync(join(cacheDir, entry.file_name));
      const cached = await fetchPackage(entry);
      if (!before) downloaded += 1;
      copyFileSync(cached, join(outDir, entry.file_name));
      total += statSync(cached).size;
    })
  );
}

writeFileSync(join(outDir, 'packages.json'), JSON.stringify({ pyodide: PYODIDE_VERSION, packages: entries.map((entry) => entry.name) }, null, 2) + '\n');
const runtimeBytes = RUNTIME_FILES.reduce((sum, file) => sum + statSync(join(outDir, file)).size, 0);
log(`pyodide packages: ${entries.length} (${downloaded} downloaded, ${formatBytes(total)}) + runtime ${formatBytes(runtimeBytes)} → ${outDir}`);
