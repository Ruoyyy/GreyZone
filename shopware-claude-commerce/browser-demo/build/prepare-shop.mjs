#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Step 3: install Shopware inside Node PHP WASM + MariaDB WASM, run our bootstrap, and dump
 * the database into the version bundle the browsers seed from.
 *
 * Mirrors playground/src/prepare-install.mjs (installer, migrations, shop config, theme,
 * SwagPlatformDemoData) and then adds what docker/bootstrap.sh does for the Docker shop:
 *
 *   - plugin:install --activate SwagAgenticCommerce, CommerceAgentsHandoff,
 *     SwagCommerceAgentTools, DemoOverlay (all composer-managed → no `composer require`)
 *   - .env.local: MCP_SERVER=1, UCP profile dev mode, handoff secret
 *   - UCP exposure on the Storefront channel (same PUT as docker/enable_ucp.py), one ES256
 *     shop signing key (`ucp:signing-keys:generate`, needs OPENSSL_CONF — patched in)
 *   - the repo's Python seeders run unchanged against a loopback HTTP shim in front of
 *     PHP WASM: seed_catalog.py (variants, out-of-stock size, Grundpreis, shipping, CMS
 *     policy pages), seed_orders.py (~40 orders / 60 days), merchant_identity.py
 *     (ACL role + integration + MCP allowlist, verified over /api/_mcp)
 *   - agent signing key + agent-profile.json (docker/agent_key.py) and the profile
 *     pre-seeded into ucp_platform_profile_cache (no outbound HTTP from WASM)
 *   - dal:refresh:index, SQL dump, versions.json, app/public/demo/shop-config.json
 *
 * Env: FORCE_INSTALL=1 to rebuild an existing dump, PYTHON to pick the interpreter
 * (default: the repo's .venv), SEED_ORDER_COUNT (default 40).
 */
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';
import { randomBytes } from 'node:crypto';
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  AGENT_PROFILE_URL,
  APP_PUBLIC_DEMO,
  DEMO_ROOT,
  PLAYGROUND_DIR,
  PLAYGROUND_PUBLIC,
  REPO_ROOT,
  SEED_HTTP_PORT,
  SEED_ORIGIN,
  SHOP_DIR,
  UCP_SIGNATURE_POLICY,
} from './config.mjs';
import { isFile, log, run, runAsync } from './lib.mjs';

const pg_ = (rel) => import(pathToFileURL(join(PLAYGROUND_DIR, 'src', rel)).href);
const { createPlayground } = await pg_('runtime.mjs');
const { dumpDatabase, rewriteShopUrls } = await pg_('sql-dump.mjs');
const { copyBundlePublicAssets, runShopwareConsole } = await pg_('frontend-assets.mjs');
const { detectShopwareVersion, updateVersionsManifest, versionDumpPath } = await pg_('shopware-version.mjs');

const CACHE_DIR = join(DEMO_ROOT, 'build/.cache');
const PYTHON = process.env.PYTHON || join(REPO_ROOT, '.venv/bin/python');
const STOREFRONT_TYPE_ID = '8a243080f92e4c719546314b577cf82b';
const SHOP_NAME = 'Shopware Agentic Demo';
const SEED_HOST = `127.0.0.1:${SEED_HTTP_PORT}`;
const HANDOFF_SECRET_BYTES = 32;
const SIGNING_KID = 'default';
const SEED_ORDER_COUNT = Number(process.env.SEED_ORDER_COUNT || 40);
const UCP_VERSION = '2026-08-25';
const LOOPBACK_HOSTS = ['127.0.0.1', 'localhost'];
const PLUGINS_TO_INSTALL = [
  { name: 'SwagAgenticCommerce', required: true },
  { name: 'CommerceAgentsHandoff', required: true },
  { name: 'DemoOverlay', required: true },
  { name: 'SwagCommerceAgentTools', required: false },
];

const shopwareVersion = detectShopwareVersion(SHOP_DIR) || 'unknown';
const dumpPath = versionDumpPath(PLAYGROUND_PUBLIC, shopwareVersion);
const lockPath = join(SHOP_DIR, 'install.lock');
const shopConfigPath = join(APP_PUBLIC_DEMO, 'shop-config.json');

if (!isFile(PYTHON)) {
  throw new Error(`Python interpreter missing at ${PYTHON} — create the repo venv (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt) or set PYTHON`);
}
if (existsSync(dumpPath) && existsSync(lockPath) && existsSync(shopConfigPath) && process.env.FORCE_INSTALL !== '1') {
  log('dump, install.lock and shop-config.json exist; set FORCE_INSTALL=1 to rebuild');
  process.exit(0);
}
mkdirSync(CACHE_DIR, { recursive: true });

// --------------------------------------------------------------------------- helpers

function header(res, name) {
  const key = Object.keys(res.headers || {}).find((k) => k.toLowerCase() === name.toLowerCase());
  if (!key) return '';
  const value = res.headers[key];
  return Array.isArray(value) ? value[0] || '' : String(value || '');
}

async function request(pg, req) {
  return pg.handleRequest({
    method: req.method || 'GET',
    url: req.url,
    headers: { Host: SEED_HOST, Accept: req.accept || 'text/html', ...(req.headers || {}) },
    body: req.body,
  });
}

async function follow(pg, req, maxHops = 6) {
  let current = { ...req };
  for (let i = 0; i < maxHops; i++) {
    const res = await request(pg, current);
    if (res.status >= 300 && res.status < 400) {
      const loc = header(res, 'location');
      if (!loc) return res;
      const url = loc.startsWith('http') ? new URL(loc).pathname + new URL(loc).search : loc;
      current = { method: 'GET', url, accept: current.accept };
      continue;
    }
    return res;
  }
  throw new Error('too many redirects from ' + req.url);
}

async function json(pg, method, url, body, headers = {}) {
  const res = await request(pg, {
    method,
    url,
    accept: 'application/json',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let parsed = null;
  try {
    parsed = JSON.parse(res.text || 'null');
  } catch {
    parsed = null;
  }
  return { status: res.status, json: parsed, text: res.text, res };
}

async function migrateAll(pg) {
  let offset = 0;
  let last = -1;
  let stuck = 0;
  for (;;) {
    const r = await json(pg, 'POST', '/installer/database-migrate', { offset });
    if (r.status !== 200) throw new Error(`database-migrate HTTP ${r.status} ${(r.text || '').slice(0, 800)}`);
    if (!r.json) throw new Error(`database-migrate non-JSON: ${(r.text || '').slice(0, 400)}`);
    if (r.json.error) throw new Error(`database-migrate: ${r.json.error}`);
    offset = Number(r.json.offset || 0);
    const total = Number(r.json.total || 0);
    if (offset % 200 === 0 || r.json.isFinished) log(`migrate ${offset}/${total}${r.json.isFinished ? ' finished' : ''}`);
    if (r.json.isFinished) return;
    if (offset === last) {
      if (++stuck >= 3) throw new Error(`migrate stalled at offset ${offset}`);
    } else {
      stuck = 0;
      last = offset;
    }
  }
}

function cachedSecret(name, generate) {
  const path = join(CACHE_DIR, name);
  if (existsSync(path)) return readFileSync(path, 'utf8').trim();
  const value = generate();
  writeFileSync(path, value + '\n', { mode: 0o600 });
  return value;
}

function ensureAgentKey() {
  const pemPath = join(CACHE_DIR, 'agent-signing-key.pem');
  if (!existsSync(pemPath)) {
    const ec = spawnSync('openssl', ['ecparam', '-name', 'prime256v1', '-genkey', '-noout'], { encoding: 'utf8' });
    if (ec.status !== 0) throw new Error('openssl ecparam failed: ' + ec.stderr);
    const pkcs8 = spawnSync('openssl', ['pkcs8', '-topk8', '-nocrypt'], { input: ec.stdout, encoding: 'utf8' });
    if (pkcs8.status !== 0) throw new Error('openssl pkcs8 failed: ' + pkcs8.stderr);
    writeFileSync(pemPath, pkcs8.stdout, { mode: 0o600 });
    log('generated demo agent signing key (P-256, PKCS#8)');
  }
  const profilePath = join(CACHE_DIR, 'agent-profile.json');
  writeFileSync(profilePath, readFileSync(join(REPO_ROOT, 'agent-profile.json'), 'utf8'));
  run(PYTHON, [join(REPO_ROOT, 'docker/agent_key.py'), 'write-profile', '--pem', pemPath, '--profile', profilePath]);
  return { pem: readFileSync(pemPath, 'utf8'), profile: JSON.parse(readFileSync(profilePath, 'utf8')) };
}

function writeEnvLocal(handoffSecret) {
  const body = [
    'APP_SECRET=playground-app-secret-please-change-32ch',
    `APP_URL=${SEED_ORIGIN}`,
    'DATABASE_URL=mysql://root:root@localhost/shopware',
    'INSTANCE_ID=playgroundinstanceid32charsxx',
    'BLUE_GREEN_DEPLOYMENT=0',
    'SHOPWARE_HTTP_CACHE_ENABLED=0',
    'SHOPWARE_ES_ENABLED=0',
    'SHOPWARE_ES_INDEXING_ENABLED=0',
    'MAILER_DSN=null://null',
    'LOCK_DSN=flock',
    'COMPOSER_HOME=/shopware/var/cache/composer',
    // Admin MCP (/api/_mcp) + UCP MCP proxy: feature flag on 6.7.11–6.7.13 (docs/version-matrix.md).
    'MCP_SERVER=1',
    // The UCP SDK refuses plain-http agent-profile URLs unless this is on; the profile is
    // never fetched (cache pre-seeded), but the header is still validated.
    'SWAG_AGENTIC_COMMERCE_UCP_PROFILE_FETCHING_DEVELOPMENT_MODE=1',
    `COMMERCE_AGENTS_HANDOFF_SECRET=${handoffSecret}`,
    '',
  ].join('\n');
  writeFileSync(join(SHOP_DIR, '.env.local'), body);
}

function seedDemoDataPluginRow(db) {
  if (db.query("SELECT name FROM plugin WHERE name = 'SwagPlatformDemoData'").length) return;
  const id = randomBytes(16).toString('hex');
  const sqlStr = (s) => "'" + String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";
  const autoload = JSON.stringify({ 'psr-4': { 'Swag\\PlatformDemoData\\': 'src/' } });
  db.exec(
    'INSERT INTO plugin (id, name, base_class, composer_name, active, managed_by_composer, path, autoload, author, copyright, license, version, created_at) VALUES (' +
      `UNHEX('${id}'), 'SwagPlatformDemoData', ${sqlStr('Swag\\PlatformDemoData\\SwagPlatformDemoData')}, 'swag/demo-data', 0, 1, 'vendor/swag/demo-data', ${sqlStr(autoload)}, 'shopware AG', '(c) by shopware AG', 'MIT', '2.1.0', NOW(3))`
  );
  for (const lang of db.query('SELECT HEX(id) AS id FROM language')) {
    const langId = String(lang.id || lang.ID || '');
    if (!langId) continue;
    db.exec(
      `INSERT INTO plugin_translation (plugin_id, language_id, label, description, created_at) VALUES (UNHEX('${id}'), UNHEX('${langId}'), 'Shopware 6 Demo data', 'Demo data plugin', NOW(3))`
    );
  }
}

function markFrwComplete(db) {
  const id = randomBytes(16).toString('hex');
  try {
    db.exec(
      `INSERT INTO system_config (id, configuration_key, configuration_value, created_at) VALUES (UNHEX('${id}'), 'core.frw.completedAt', '{"_value":"2026-09-02 00:00:00"}', NOW(6))`
    );
  } catch (e) {
    log('frw config insert skipped: ' + (e && e.message ? e.message : e));
  }
}

async function consoleCmd(pg, input, { required = true } = {}) {
  const label = [input.command, ...(input.plugins || [])].join(' ');
  const started = Date.now();
  try {
    const out = await runShopwareConsole(pg, input);
    const tail = out.split('\n').map((l) => l.trim()).filter(Boolean).slice(-1)[0] || '';
    log(`console ${label} (${Date.now() - started} ms) ${tail.slice(0, 160)}`);
    return out;
  } catch (error) {
    if (required) throw error;
    log(`WARNING console ${label} failed: ${String(error.message || error).slice(0, 600)}`);
    return null;
  }
}

/** Loopback HTTP → PHP WASM, strictly serialized (one PHP request at a time). */
function startSeedServer(pg) {
  let queue = Promise.resolve();
  const server = createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = chunks.length ? new Uint8Array(Buffer.concat(chunks)) : undefined;
      const headers = {};
      for (const [k, v] of Object.entries(req.headers)) {
        if (['content-length', 'connection', 'accept-encoding', 'transfer-encoding'].includes(k)) continue;
        headers[k] = Array.isArray(v) ? v.join(', ') : String(v);
      }
      headers.Host = SEED_HOST;
      queue = queue
        .then(() => pg.handleRequest({ method: req.method, url: req.url, headers, body }))
        .then(
          (php) => {
            const out = {};
            for (const [k, v] of Object.entries(php.headers || {})) {
              if (['transfer-encoding', 'content-length', 'connection'].includes(k.toLowerCase())) continue;
              out[k] = v;
            }
            const bytes = php.bytes instanceof Uint8Array ? php.bytes : Buffer.from(php.text || '', 'utf8');
            out['content-length'] = String(bytes.byteLength);
            res.writeHead(php.status || 200, out);
            res.end(Buffer.from(bytes));
          },
          (error) => {
            res.writeHead(502, { 'content-type': 'text/plain' });
            res.end('php wasm error: ' + (error && error.message ? error.message : String(error)));
          }
        );
    });
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(SEED_HTTP_PORT, '127.0.0.1', () => resolve(server));
  });
}

function python(script, args, env = {}) {
  // Asynchronous: the loopback shim in this process must keep answering PHP requests.
  return runAsync(PYTHON, [join(REPO_ROOT, 'docker', script), ...args], { cwd: REPO_ROOT, env });
}

function readEnvFile(path) {
  const out = {};
  if (!existsSync(path)) return out;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const eq = line.indexOf('=');
    if (eq <= 0 || line.startsWith('#')) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

// --------------------------------------------------------------------------- UCP exposure

async function adminToken(pg) {
  const r = await json(pg, 'POST', '/api/oauth/token', {
    grant_type: 'password',
    client_id: 'administration',
    scope: 'write user-verified',
    username: ADMIN_USERNAME,
    password: ADMIN_PASSWORD,
  });
  if (r.status !== 200 || !r.json?.access_token) throw new Error(`admin login failed: ${r.status} ${r.text?.slice(0, 300)}`);
  return r.json.access_token;
}

function storefrontChannel(db) {
  const row = db.query(
    `SELECT LOWER(HEX(id)) AS id, access_key FROM sales_channel WHERE LOWER(HEX(type_id)) = '${STOREFRONT_TYPE_ID}' AND active = 1 ORDER BY created_at LIMIT 1`
  )[0];
  if (!row) throw new Error('no Storefront sales channel found');
  return { id: String(row.id), accessKey: String(row.access_key) };
}

async function enableUcp(pg, channelId) {
  const token = await adminToken(pg);
  const auth = { Authorization: `Bearer ${token}` };
  const path = `/api/_admin/ucp/sales-channels/${channelId}/config`;
  const current = await json(pg, 'GET', path, undefined, auth);
  if (current.status === 404) throw new Error('UCP admin API missing — SwagAgenticCommerce not active');
  // docker/enable_ucp.py desired_config(), with the seed origin standing in for the shop URL
  // (rewritten to the live origin by the shell at boot).
  const desired = {
    active: true,
    ucpVersion: UCP_VERSION,
    profileDomain: SEED_ORIGIN,
    enabledCapabilities: ['catalog', 'cart', 'discount', 'checkout', 'order', 'identity_linking'],
    enabledTransports: ['rest', 'mcp', 'embedded'],
    continueUrlTemplate: `${SEED_ORIGIN}/checkout/confirm?checkoutId={checkoutId}`,
    platformAllowlist: LOOPBACK_HOSTS,
    remoteProfileAllowlist: LOOPBACK_HOSTS,
    agentAllowlist: LOOPBACK_HOSTS,
    embeddedAllowedOrigins: [SEED_ORIGIN],
    embeddedFrameAncestors: [SEED_ORIGIN],
    discoveryBudget: 10,
    catalogResultLimit: 50,
    webhookUrlOverride: null,
    signaturePolicy: UCP_SIGNATURE_POLICY,
    idempotencyRequired: true,
  };
  const put = await json(pg, 'PUT', path, { ...(current.json?.data || {}), ...desired }, auth);
  if (put.status >= 300) throw new Error(`PUT ${path} → ${put.status} ${put.text?.slice(0, 400)}`);
  log(`ucp config written (signaturePolicy=${UCP_SIGNATURE_POLICY})`);

  const listing = (await consoleCmd(pg, { command: 'ucp:signing-keys:list', '--sales-channel': channelId }, { required: false })) || '';
  const start = listing.indexOf('[');
  let keys = [];
  try {
    keys = start >= 0 ? JSON.parse(listing.slice(start, listing.lastIndexOf(']') + 1)) : [];
  } catch {
    keys = [];
  }
  if (!keys.some((k) => k && k.status === 'active')) {
    await consoleCmd(pg, { command: 'ucp:signing-keys:generate', '--sales-channel': channelId, '--kid': SIGNING_KID });
  }
  await consoleCmd(pg, { command: 'cache:clear', '--no-warmup': true });
  await consoleCmd(pg, { command: 'ucp:config:validate', '--sales-channel': channelId }, { required: false });
}

// --------------------------------------------------------------------------- main

async function main() {
  const started = Date.now();
  run('php', ['overrides/patch-installer.php'], { cwd: SHOP_DIR });
  if (existsSync(lockPath)) rmSync(lockPath);
  rmSync(join(SHOP_DIR, 'var/cache'), { recursive: true, force: true });

  const handoffSecret = cachedSecret('handoff-secret', () => randomBytes(HANDOFF_SECRET_BYTES).toString('hex'));
  const agentKey = ensureAgentKey();

  log(`booting PHP WASM + lite4mariadb (Shopware ${shopwareVersion}, in-memory)`);
  const pg = await createPlayground({ skipDump: true, absoluteUrl: SEED_ORIGIN, dataDir: 'memory://' });
  let server;
  try {
    pg.db.exec('DROP DATABASE IF EXISTS shopware');
    pg.db.exec('CREATE DATABASE shopware');
    pg.db.exec('USE shopware');

    log('installer: database-configuration');
    const cfg = await follow(pg, {
      method: 'POST',
      url: '/installer/database-configuration',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ hostname: 'localhost', username: 'root', password: '', port: '3306', databaseName: 'shopware' }).toString(),
    });
    if (cfg.status !== 200 || /name="hostname"/.test(cfg.text || '')) {
      const err = (cfg.text || '').match(/<div class="alert alert-error[\s\S]*?<pre>([\s\S]*?)<\/pre>/);
      throw new Error('database-configuration failed: ' + (err ? err[1] : `HTTP ${cfg.status} ` + (cfg.text || '').slice(0, 400)));
    }

    log('installer: migrations');
    await migrateAll(pg);

    log('installer: shop configuration');
    const setup = await pg.php.run({
      code: `<?php
        require '/internal/playground_prepend.php';
        require '/shopware/vendor/autoload.php';
        try {
            $info = (new Shopware\\Core\\Maintenance\\System\\Struct\\DatabaseConnectionInformation())->assign([
                'hostname' => 'localhost', 'username' => 'root', 'password' => '', 'port' => 3306, 'databaseName' => 'shopware',
            ]);
            $connection = (new Shopware\\Core\\Maintenance\\System\\Service\\DatabaseConnectionFactory())->getConnection($info);
            $clock = new Symfony\\Component\\Clock\\NativeClock();
            $dispatcher = new Symfony\\Component\\EventDispatcher\\EventDispatcher();
            $shop = new Shopware\\Core\\Installer\\Configuration\\ShopConfigurationService($dispatcher, $clock);
            $shop->updateShop([
                'name' => ${JSON.stringify(SHOP_NAME)},
                'locale' => 'en-GB',
                'currency' => 'EUR',
                'additionalCurrencies' => null,
                'country' => 'DEU',
                'email' => 'shop@example.com',
                'host' => ${JSON.stringify(SEED_HOST)},
                'schema' => 'http',
                'basePath' => '',
                'blueGreenDeployment' => false,
            ], $connection);
            $users = new Shopware\\Core\\Maintenance\\User\\Service\\UserProvisioner($connection, $clock);
            $users->provision(${JSON.stringify(ADMIN_USERNAME)}, ${JSON.stringify(ADMIN_PASSWORD)}, [
                'firstName' => 'Demo', 'lastName' => 'Merchant', 'email' => 'admin@example.com',
            ]);
            echo 'ok';
        } catch (Throwable $e) {
            fwrite(STDERR, $e->getMessage() . ' in ' . $e->getFile() . ':' . $e->getLine());
            echo 'fail:' . $e->getMessage();
            exit(1);
        }
      `,
    });
    if (String(setup.text || '').trim() !== 'ok') {
      throw new Error('shop configuration failed: ' + String(setup.text || '') + ' ' + String(setup.errors || '').slice(0, 1500));
    }
    markFrwComplete(pg.db);
    writeEnvLocal(handoffSecret);
    writeFileSync(lockPath, new Date().toISOString().replace(/[-:T]/g, '').slice(0, 12));
    rewriteShopUrls(pg.db, SEED_ORIGIN);

    log('copying bundle public assets (assets:install equivalent)');
    copyBundlePublicAssets(SHOP_DIR);
    await consoleCmd(pg, { command: 'theme:refresh' });
    await consoleCmd(pg, { command: 'theme:change', 'theme-name': 'Storefront', '--all': true, '--sync': true });

    if (existsSync(join(SHOP_DIR, 'vendor/swag/demo-data/composer.json'))) {
      seedDemoDataPluginRow(pg.db);
      await consoleCmd(pg, { command: 'plugin:install', plugins: ['SwagPlatformDemoData'], '--activate': true, '--skip-asset-build': true });
    } else {
      log('WARNING swag/demo-data missing; no demo catalog');
    }

    // --- our plugins ---------------------------------------------------------------------
    await consoleCmd(pg, { command: 'plugin:refresh' });
    const pluginRows = pg.db.query('SELECT name FROM plugin').map((r) => String(r.name));
    log('plugins known: ' + pluginRows.join(', '));
    for (const plugin of PLUGINS_TO_INSTALL) {
      if (!pluginRows.includes(plugin.name)) {
        if (plugin.required) throw new Error(`${plugin.name} not listed after plugin:refresh`);
        log(`WARNING ${plugin.name} not listed after plugin:refresh; skipping`);
        continue;
      }
      await consoleCmd(
        pg,
        { command: 'plugin:install', plugins: [plugin.name], '--activate': true, '--skip-asset-build': true },
        { required: plugin.required }
      );
    }
    await consoleCmd(pg, { command: 'cache:clear', '--no-warmup': true });
    const active = pg.db.query('SELECT name FROM plugin WHERE active = 1').map((r) => String(r.name));
    log('active plugins: ' + active.join(', '));

    // --- UCP exposure + shop signing key -----------------------------------------------------
    const channel = storefrontChannel(pg.db);
    log(`storefront sales channel ${channel.id}`);
    await enableUcp(pg, channel.id);

    // --- seeds through the loopback HTTP shim (repo scripts, unchanged) ----------------------
    server = await startSeedServer(pg);
    log(`seed HTTP shim listening on ${SEED_ORIGIN}`);
    const health = await fetch(`${SEED_ORIGIN}/api/_info/version`);
    log(`shim check: GET /api/_info/version → ${health.status}`);
    const seedEnv = { PYTHONUNBUFFERED: '1' };
    await python('seed_catalog.py', ['--shop-url', SEED_ORIGIN, '--user', ADMIN_USERNAME, '--password', ADMIN_PASSWORD], seedEnv);
    await python('seed_orders.py', ['--shop-url', SEED_ORIGIN, '--user', ADMIN_USERNAME, '--password', ADMIN_PASSWORD, '--count', String(SEED_ORDER_COUNT)], seedEnv);
    const generatedEnv = join(CACHE_DIR, 'generated.env');
    rmSync(generatedEnv, { force: true });
    await python('merchant_identity.py', ['--shop-url', SEED_ORIGIN, '--user', ADMIN_USERNAME, '--password', ADMIN_PASSWORD, '--generated-env', generatedEnv], seedEnv);
    const generated = readEnvFile(generatedEnv);
    if (!generated.SHOPWARE_INTEGRATION_ACCESS_KEY || !generated.SHOPWARE_INTEGRATION_SECRET_KEY) {
      throw new Error('merchant_identity.py did not write integration credentials');
    }
    server.close();
    server = undefined;

    // --- agent profile into the SDK's cache (never fetched over HTTP) -----------------------
    const profileJson = JSON.stringify(agentKey.profile);
    pg.db.exec(`DELETE FROM ucp_platform_profile_cache WHERE uri = '${AGENT_PROFILE_URL}'`);
    pg.db.exec(
      `INSERT INTO ucp_platform_profile_cache (uri, payload, expires_at) VALUES ('${AGENT_PROFILE_URL}', '${profileJson.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}', NULL)`
    );
    log('seeded ucp_platform_profile_cache (expires_at NULL)');

    await consoleCmd(pg, { command: 'dal:refresh:index' }, { required: false });
    await consoleCmd(pg, { command: 'cache:clear', '--no-warmup': true });

    // --- dump + manifest + shop-config ----------------------------------------------------
    const counts = Object.fromEntries(
      ['product', 'order', 'customer', 'category', 'cms_page'].map((t) => [t, Number(pg.db.query(`SELECT COUNT(*) AS c FROM \`${t}\``)[0].c)])
    );
    log('table counts: ' + JSON.stringify(counts));
    mkdirSync(dirname(dumpPath), { recursive: true });
    log('dumping MariaDB WASM');
    const sql = dumpDatabase(pg.db, { database: 'shopware', onProgress: () => {} });
    const gz = gzipSync(Buffer.from(sql, 'utf8'));
    writeFileSync(dumpPath, gz);
    log(`wrote ${dumpPath} (${gz.length} bytes gzip, ${sql.length} bytes sql)`);
    const manifest = updateVersionsManifest(PLAYGROUND_PUBLIC, shopwareVersion);
    log('versions.json: ' + manifest.versions.map((v) => v.id).join(', '));

    mkdirSync(APP_PUBLIC_DEMO, { recursive: true });
    const shopConfig = {
      builtAt: new Date().toISOString(),
      shopwareVersion,
      seedOrigin: SEED_ORIGIN,
      shopName: SHOP_NAME,
      salesChannelId: channel.id,
      salesChannelAccessKey: channel.accessKey,
      integrationAccessKey: generated.SHOPWARE_INTEGRATION_ACCESS_KEY,
      integrationSecretKey: generated.SHOPWARE_INTEGRATION_SECRET_KEY,
      handoffSecret,
      agentProfileUrl: AGENT_PROFILE_URL,
      agentProfile: agentKey.profile,
      agentSigningKeyPem: agentKey.pem,
      ucpSignaturePolicy: UCP_SIGNATURE_POLICY,
      admin: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
      activePlugins: active,
      counts,
    };
    writeFileSync(shopConfigPath, JSON.stringify(shopConfig, null, 2) + '\n');
    log(`wrote ${shopConfigPath}`);

    // Placeholder-origin caches must not end up in the zip.
    rmSync(join(SHOP_DIR, 'var/cache'), { recursive: true, force: true });
    log(`prepare-shop finished in ${Math.round((Date.now() - started) / 1000)} s`);
  } finally {
    if (server) server.close();
    await pg.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
