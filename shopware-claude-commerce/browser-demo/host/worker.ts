// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Agent-host Web Worker: Pyodide (CPython 3.14) running the repo's FastAPI apps.
 *
 * Messages from the page (see app/src/engine/agent-host.ts):
 *   boot          load Pyodide + wheels + repo tree, install the browser transport
 *   start-role    import storefront.api.main / merchant.api.main, run lifespan startup
 *   http          one request to a role's app; answered with http-head / http-chunk / http-end
 *   shop-result   the page's answer to a shop-request this worker sent
 *   set-anthropic switch between proxy mode and BYOK (key stays in this worker's memory)
 *
 * Messages to the page: status, booted, role-ready, http-*, shop-request, log, error.
 */

import type { HostBootConfig, HostWorkerInbound, HostWorkerOutbound } from './protocol';

declare const self: DedicatedWorkerGlobalScope;

type PyodideModule = {
  loadPackage: (names: string[], options?: Record<string, unknown>) => Promise<void>;
  runPythonAsync: (code: string, options?: { globals?: unknown }) => Promise<unknown>;
  runPython: (code: string) => unknown;
  registerJsModule: (name: string, module: unknown) => void;
  unpackArchive: (buffer: ArrayBuffer, format: string, options?: { extractDir?: string }) => void;
  FS: { mkdirTree: (path: string) => void; writeFile: (path: string, data: Uint8Array | string) => void };
  globals: { get: (name: string) => unknown };
  pyimport: (name: string) => unknown;
  setStderr: (options: { batched: (line: string) => void }) => void;
  setStdout: (options: { batched: (line: string) => void }) => void;
  version: string;
};

const REPO_MOUNT = '/repo';
const WHEELS_DIR = '/wheels';
const SECRETS_DIR = `${REPO_MOUNT}/secrets`;
const AGENT_KEY_PATH = `${SECRETS_DIR}/ucp-agent-signing-key.pem`;
const SHOP_REQUEST_TIMEOUT_MS = 180_000;

let pyodide: PyodideModule | null = null;
let bootstrap: Record<string, (...args: unknown[]) => unknown> | null = null;
let bootConfig: HostBootConfig | null = null;
const shopWaiters = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: number }>();

function post(message: HostWorkerOutbound, transfer: Transferable[] = []): void {
  self.postMessage(message, transfer);
}

function status(text: string, detail?: Record<string, unknown>): void {
  post({ type: 'status', text, ...(detail || {}) });
}

/** What Python sees as `import demo_bridge`. */
const bridge = {
  shopOrigin: '',
  /** Live model-access settings; bootstrap.py reads them per request (mode switches apply immediately). */
  anthropic: { mode: 'proxy', proxyUrl: '', apiKey: '', workspaceId: '', sessionToken: '' } as HostBootConfig['anthropic'],
  shopRequest(method: string, url: string, headersJson: string, body: Uint8Array | null): Promise<unknown> {
    const id = crypto.randomUUID();
    const payload = body ? new Uint8Array(body).buffer : null;
    return new Promise((resolve, reject) => {
      const timer = self.setTimeout(() => {
        shopWaiters.delete(id);
        reject(new Error(`shop request timed out: ${method} ${url}`));
      }, SHOP_REQUEST_TIMEOUT_MS);
      shopWaiters.set(id, { resolve, reject, timer });
      post({ type: 'shop-request', id, method, url, headers: JSON.parse(headersJson), body: payload }, payload ? [payload] : []);
    });
  },
};

async function loadPyodideModule(indexURL: string): Promise<PyodideModule> {
  const loader = (await import(/* @vite-ignore */ `${indexURL}pyodide.mjs`)) as {
    loadPyodide: (options: Record<string, unknown>) => Promise<PyodideModule>;
  };
  return loader.loadPyodide({ indexURL, lockFileURL: `${indexURL}pyodide-lock.json` });
}

async function boot(config: HostBootConfig): Promise<void> {
  bootConfig = config;
  bridge.shopOrigin = config.shopOrigin;
  bridge.anthropic = { ...bridge.anthropic, ...config.anthropic };
  const t0 = performance.now();
  const stamp = () => Math.round(performance.now() - t0);

  status('Loading Python (Pyodide)…', { phase: 'pyodide' });
  pyodide = await loadPyodideModule(config.pyodideIndexUrl);
  pyodide.setStdout({ batched: (line) => post({ type: 'log', level: 'info', text: line }) });
  pyodide.setStderr({ batched: (line) => post({ type: 'log', level: 'warn', text: line }) });
  status(`Python ${pyodide.version} ready (${stamp()} ms) — loading packages…`, { phase: 'packages' });

  await pyodide.loadPackage(config.pyodidePackages, { messageCallback: () => {} });
  status(`Packages loaded (${stamp()} ms) — installing agent wheels…`, { phase: 'wheels' });

  pyodide.FS.mkdirTree(WHEELS_DIR);
  const wheelFiles = await Promise.all(
    config.wheelUrls.map(async (url) => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`wheel download failed: ${url} (${res.status})`);
      const name = url.split('/').pop() as string;
      pyodide!.FS.writeFile(`${WHEELS_DIR}/${name}`, new Uint8Array(await res.arrayBuffer()));
      return `emfs:${WHEELS_DIR}/${name}`;
    })
  );
  pyodide.registerJsModule('demo_bridge', bridge);
  await pyodide.runPythonAsync(
    `import micropip\nawait micropip.install(${JSON.stringify(wheelFiles)}, deps=False)\n`
  );
  status(`Wheels installed (${stamp()} ms) — mounting backends…`, { phase: 'repo' });

  const tree = await fetch(config.repoTreeUrl);
  if (!tree.ok) throw new Error(`repo tree download failed: ${config.repoTreeUrl} (${tree.status})`);
  pyodide.FS.mkdirTree(REPO_MOUNT);
  pyodide.unpackArchive(await tree.arrayBuffer(), 'tar', { extractDir: REPO_MOUNT });
  pyodide.FS.mkdirTree(SECRETS_DIR);
  pyodide.FS.writeFile(AGENT_KEY_PATH, config.agentSigningKeyPem);
  pyodide.FS.mkdirTree(`${REPO_MOUNT}/merchant/data`);
  pyodide.FS.mkdirTree(`${REPO_MOUNT}/storefront/data`);

  const bootstrapSource = await (await fetch(config.bootstrapUrl)).text();
  pyodide.FS.writeFile(`${REPO_MOUNT}/browser_demo_bootstrap.py`, bootstrapSource);
  pyodide.runPython(
    [
      'import os, sys',
      `os.chdir(${JSON.stringify(REPO_MOUNT)})`,
      `for p in (${JSON.stringify(REPO_MOUNT)}, ${JSON.stringify(REPO_MOUNT + '/vendor')}):`,
      '    sys.path.insert(0, p) if p not in sys.path else None',
    ].join('\n')
  );
  bootstrap = pyodide.pyimport('browser_demo_bootstrap') as typeof bootstrap;
  bootstrap!.configure_logging(config.logLevel || 'INFO');
  bootstrap!.configure_environment(pyodide.runPython(`import json; json.loads(${JSON.stringify(JSON.stringify(environmentFor(config)))})`));
  bootstrap!.install_transport();
  const versions = JSON.parse(String(bootstrap!.versions()));
  status(`Agent host ready (${stamp()} ms)`, { phase: 'ready' });
  post({ type: 'booted', ms: stamp(), versions });
}

function environmentFor(config: HostBootConfig): Record<string, string> {
  const shop = config.shopOrigin;
  const cfg = config.shop;
  const env: Record<string, string> = {
    DEMO_LOG_LEVEL: config.logLevel || 'INFO',
    // storefront host (storefront/api)
    SHOPWARE_URL: shop,
    SHOPWARE_ADMIN_URL: shop,
    SHOPWARE_SALES_CHANNEL_ACCESS_KEY: cfg.salesChannelAccessKey,
    SHOPWARE_SALES_CHANNEL_ID: cfg.salesChannelId,
    UCP_AGENT_PROFILE_URL: cfg.agentProfileUrl,
    UCP_AGENT_SIGNING_KEY_PEM_FILE: AGENT_KEY_PATH,
    UCP_TRANSPORT: 'mcp',
    COMMERCE_AGENTS_HANDOFF_SECRET: cfg.handoffSecret,
    STOREFRONT_API_PUBLIC_URL: config.virtualOrigins.shopping,
    WEB_APP_URL: shop,
    // merchant host (merchant/api) — the integration docker/merchant_identity.py created
    SHOPWARE_INTEGRATION_ACCESS_KEY: cfg.integrationAccessKey,
    SHOPWARE_INTEGRATION_SECRET_KEY: cfg.integrationSecretKey,
    SHOPWARE_ADMIN_TRANSPORT: 'mcp',
    SHOPWARE_STORE_NAME: cfg.shopName,
    MERCHANT_OPERATOR: config.operator || 'Demo Merchant',
    MERCHANT_REQUIRE_HOST_APPROVAL: '1',
    MERCHANT_LEDGER_DSN: `sqlite:///${REPO_MOUNT}/merchant/data/ledger.db`,
    // Model access: the SDK is built with a placeholder; bootstrap.py's transport rewrites
    // every api.anthropic.com request for the live mode (proxy or BYOK).
    ANTHROPIC_BASE_URL: 'https://api.anthropic.com',
    ANTHROPIC_API_KEY: 'browser-demo-placeholder',
  };
  return env;
}

async function startRole(role: 'shopping' | 'merchant'): Promise<void> {
  if (!bootstrap) throw new Error('host not booted');
  const t0 = performance.now();
  status(role === 'shopping' ? 'Starting the shopping agent…' : 'Starting the merchant agent…', { phase: `role:${role}` });
  await (bootstrap.boot_role(role) as Promise<unknown>);
  post({ type: 'role-ready', role, ms: Math.round(performance.now() - t0) });
}

async function handleHttp(message: Extract<HostWorkerInbound, { type: 'http' }>): Promise<void> {
  if (!bootstrap) {
    post({ type: 'http-head', id: message.id, status: 503, headers: [['content-type', 'application/json']] });
    post({ type: 'http-chunk', id: message.id, chunk: new TextEncoder().encode('{"detail":"host not booted"}').buffer });
    post({ type: 'http-end', id: message.id });
    return;
  }
  const sendHead = (statusCode: number, headersJson: string) => {
    post({ type: 'http-head', id: message.id, status: statusCode, headers: JSON.parse(headersJson) });
  };
  const sendChunk = (chunk: Uint8Array) => {
    const copy = new Uint8Array(chunk).buffer;
    post({ type: 'http-chunk', id: message.id, chunk: copy }, [copy]);
  };
  try {
    const body = message.body ? new Uint8Array(message.body) : null;
    await (bootstrap.handle_request(message.role, message.method, message.target, JSON.stringify(message.headers), body, sendHead, sendChunk) as Promise<unknown>);
  } catch (error) {
    post({ type: 'log', level: 'error', text: `http ${message.method} ${message.target}: ${(error as Error).message}` });
  } finally {
    post({ type: 'http-end', id: message.id });
  }
}

self.onmessage = async (event: MessageEvent<HostWorkerInbound>) => {
  const message = event.data;
  try {
    switch (message.type) {
      case 'boot':
        await boot(message.config);
        break;
      case 'start-role':
        await startRole(message.role);
        break;
      case 'http':
        await handleHttp(message);
        break;
      case 'shop-result': {
        const waiter = shopWaiters.get(message.id);
        if (!waiter) return;
        shopWaiters.delete(message.id);
        self.clearTimeout(waiter.timer);
        if (message.error) waiter.reject(new Error(message.error));
        else
          waiter.resolve({
            status: message.status,
            headersJson: JSON.stringify(message.headers || {}),
            body: message.body ? new Uint8Array(message.body) : null,
          });
        break;
      }
      case 'set-anthropic': {
        bridge.anthropic = { ...bridge.anthropic, ...message.anthropic };
        if (bootConfig) bootConfig = { ...bootConfig, anthropic: bridge.anthropic };
        post({ type: 'anthropic-mode', mode: bridge.anthropic.mode });
        break;
      }
      default:
        throw new Error(`unknown message ${(message as { type: string }).type}`);
    }
  } catch (error) {
    const text = error instanceof Error ? `${error.message}` : String(error);
    post({ type: 'error', context: message.type, text });
  }
};
