// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT
//
// Adapted from FriendsOfShopware/shopware-playground src/ui/playground.js (commit c86f241):
// the framework-agnostic boot sequence (version manifest, service worker, dedicated PHP
// worker, SW → page → worker request bridge). Rewritten in TypeScript without the Svelte
// stores, plus the demo's own boot-time fix-ups (UCP config origin rewrite).

import { idbNamesForVersion } from '../../../playground/src/idb-names.mjs';
import { VERSION_COOKIE } from '../../../playground/src/app-route.mjs';
import { PUBLIC_BASE, demoUrl, shopPublicOrigin } from './public-base';

export type PhpRequest = {
  url: string;
  method?: string;
  headers?: Record<string, string>;
  body?: ArrayBuffer | null;
};

export type PhpResponse = {
  status: number;
  headers: Record<string, string | string[]>;
  body: ArrayBuffer | null;
  text: string;
  errors: string;
};

export type VersionEntry = { id: string; label?: string; zip: string; dump: string; seed?: string };

type WorkerReply = { id?: string; type: string; text?: string; error?: string; [key: string]: unknown };

const WORKER_URL = demoUrl('/browser-worker.js');
const SERVICE_WORKER_URL = demoUrl('/service-worker.js');
const VERSIONS_URL = demoUrl('/versions.json');
const SW_SCOPE = PUBLIC_BASE ? `${PUBLIC_BASE}/` : '/';
const LOCAL_STORAGE_VERSION = 'sw-playground-version';
const SEED_MARKER_PREFIX = 'sw-playground-seed-';
const SQL_ROW_CAP = 500;

export class Playground {
  private worker: Worker | null = null;
  private version = '';
  private onStatus: (text: string) => void;
  private requestCount = 0;
  private busy = 0;
  private listeners = new Set<(busy: number, count: number) => void>();

  constructor(options: { onStatus?: (text: string) => void } = {}) {
    this.onStatus = options.onStatus || (() => {});
  }

  get activeVersion(): string {
    return this.version;
  }

  /** Subscribe to PHP request activity (in-flight count, total). */
  onActivity(listener: (busy: number, count: number) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private send(payload: Record<string, unknown>, transfer: Transferable[] = []): Promise<WorkerReply> {
    const worker = this.worker;
    if (!worker) return Promise.reject(new Error('playground worker not started'));
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const onMessage = (event: MessageEvent<WorkerReply>) => {
        const data = event.data || ({} as WorkerReply);
        if (data.id !== id) return;
        if (data.type === 'status') {
          if (typeof data.text === 'string' && !/^Shopware (GET|POST|PATCH|PUT|DELETE) /.test(data.text)) this.onStatus(data.text);
          return;
        }
        worker.removeEventListener('message', onMessage);
        if (data.type === 'error') {
          reject(new Error(data.error || 'worker error'));
          return;
        }
        resolve(data);
      };
      worker.addEventListener('message', onMessage);
      worker.postMessage({ ...payload, id }, transfer);
    });
  }

  /** One HTTP request into Shopware (PHP WASM). Serialized by the worker. */
  async phpRequest(req: PhpRequest): Promise<PhpResponse> {
    const transfer: Transferable[] = [];
    if (req.body instanceof ArrayBuffer) transfer.push(req.body);
    this.busy += 1;
    this.requestCount += 1;
    this.notify();
    try {
      const res = await this.send({ type: 'request', req }, transfer);
      return {
        status: Number(res.status || 200),
        headers: (res.headers as Record<string, string | string[]>) || {},
        body: (res.body as ArrayBuffer) || null,
        text: String(res.text || ''),
        errors: String(res.errors || ''),
      };
    } finally {
      this.busy -= 1;
      this.notify();
    }
  }

  /** Run SQL on MariaDB WASM (the playground's console channel). */
  async sql(statement: string): Promise<{ rows?: Record<string, unknown>[]; affectedRows?: number; truncated: boolean }> {
    const res = await this.send({ type: 'sql', sql: statement });
    const result = (res.result as { rows?: Record<string, unknown>[]; affectedRows?: number }) || {};
    return { ...result, truncated: Boolean(res.truncated) };
  }

  async reset(): Promise<void> {
    await this.send({ type: 'reset' });
    localStorage.removeItem(LOCAL_STORAGE_VERSION);
  }

  private notify(): void {
    for (const listener of this.listeners) listener(this.busy, this.requestCount);
  }

  // ---------------------------------------------------------------- boot sequence

  async boot(): Promise<VersionEntry> {
    this.onStatus('Loading version manifest…');
    const { list, active } = await loadVersions();
    this.version = active;
    selectVersion(active);
    const entry = list.find((v) => v.id === active) || list[0];
    await ensureSeedFreshness(active, entry.seed);

    this.onStatus('Registering service worker…');
    this.attachServiceWorkerBridge();
    await registerServiceWorker();
    this.pushVersionToServiceWorker();
    ensureCrossOriginIsolation(this.onStatus);

    this.onStatus('Starting PHP WASM + MariaDB WASM…');
    const worker = new Worker(WORKER_URL, { type: 'module' });
    this.worker = worker;
    worker.addEventListener('error', (event) => {
      this.onStatus('Worker error: ' + (event.message || String(event)));
    });
    await this.send({
      type: 'boot',
      origin: shopPublicOrigin(),
      host: location.host,
      version: entry.id,
      zipUrl: entry.zip,
      dumpUrl: entry.dump,
    });
    return entry;
  }

  private attachServiceWorkerBridge(): void {
    navigator.serviceWorker.addEventListener('controllerchange', () => this.pushVersionToServiceWorker());
    navigator.serviceWorker.addEventListener('message', async (event: MessageEvent) => {
      const data = event.data || {};
      const port = event.ports && event.ports[0];
      if (data.type === 'sw-playground-version-query') {
        if (port) port.postMessage({ version: this.version });
        return;
      }
      if (data.type !== 'php-request' || !port) return;
      try {
        const res = await this.phpRequest(data.req || {});
        const body = res.body || null;
        port.postMessage({ ok: true, status: res.status, headers: res.headers, body, text: res.text, errors: res.errors }, body ? [body] : []);
      } catch (error) {
        port.postMessage({ ok: false, error: error instanceof Error ? error.message : String(error) });
      }
    });
  }

  private pushVersionToServiceWorker(): void {
    const post = (sw: ServiceWorker | null) => sw && sw.postMessage({ type: 'sw-playground-version', version: this.version });
    if (navigator.serviceWorker.controller) post(navigator.serviceWorker.controller);
    else navigator.serviceWorker.ready.then((reg) => post(reg.active));
  }
}

// -------------------------------------------------------------------- helpers

async function loadVersions(): Promise<{ list: VersionEntry[]; active: string }> {
  const res = await fetch(VERSIONS_URL, { cache: 'no-store' });
  if (!res.ok) throw new Error('versions.json missing; run npm run build');
  const manifest = (await res.json()) as { default?: string; versions?: VersionEntry[] };
  const list = Array.isArray(manifest.versions) ? manifest.versions : [];
  if (!list.length) throw new Error('versions.json lists no Shopware versions; run npm run build');
  const stored = localStorage.getItem(LOCAL_STORAGE_VERSION);
  const active = list.some((v) => v.id === stored) ? (stored as string) : manifest.default || list[0].id;
  return { list, active };
}

function selectVersion(version: string): void {
  localStorage.setItem(LOCAL_STORAGE_VERSION, version);
  document.cookie = VERSION_COOKIE + '=' + encodeURIComponent(version) + '; path=' + SW_SCOPE + '; SameSite=Strict';
}

async function registerServiceWorker(): Promise<void> {
  if (!('serviceWorker' in navigator)) throw new Error('Service Worker support is required for the in-browser Shopware');
  const registration = await navigator.serviceWorker.register(SERVICE_WORKER_URL, { type: 'module', scope: SW_SCOPE });
  if (registration.waiting) registration.waiting.postMessage({ type: 'skip-waiting' });
  await navigator.serviceWorker.ready;
  if (navigator.serviceWorker.controller) return;
  await new Promise<void>((resolve) => {
    navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true });
  });
}

const COI_RELOAD_MARKER = 'commerce-agents-demo:coi-reload';

/**
 * coi-serviceworker fallback for static hosts that cannot set COOP/COEP (GitHub Pages): the
 * patched playground service worker adds the headers to every response it controls, so one
 * reload after it took control makes the document crossOriginIsolated (SharedArrayBuffer for
 * MariaDB WASM). Reloads at most once per tab; otherwise explains what is missing.
 */
function ensureCrossOriginIsolation(onStatus: (text: string) => void): void {
  if (self.crossOriginIsolated) return;
  let reloaded = false;
  try {
    reloaded = sessionStorage.getItem(COI_RELOAD_MARKER) === '1';
    if (!reloaded) sessionStorage.setItem(COI_RELOAD_MARKER, '1');
  } catch {
    reloaded = true;
  }
  if (!reloaded && navigator.serviceWorker.controller) {
    onStatus('Enabling cross-origin isolation through the service worker (one reload)…');
    location.reload();
    throw new Error('reloading for cross-origin isolation');
  }
  throw new Error(
    'This page is not cross-origin isolated. The service worker should add COOP/COEP after one reload (GitHub Pages cannot set those headers). Try a hard refresh, or use a Chromium/Firefox desktop browser with service workers enabled.',
  );
}

/** Wipe a persisted database whose seed dump differs from the deployed one. */
async function ensureSeedFreshness(version: string, seed?: string): Promise<void> {
  if (!version || !seed || typeof indexedDB === 'undefined') return;
  const markerKey = SEED_MARKER_PREFIX + version;
  let stored = '';
  try {
    stored = localStorage.getItem(markerKey) || '';
  } catch {
    return;
  }
  if (stored === seed) return;
  const names = new Set<string>(idbNamesForVersion(version) as string[]);
  try {
    for (const info of await indexedDB.databases()) {
      if (info.name && info.name.endsWith('shopware-playground-' + version)) names.add(info.name);
    }
  } catch {
    /* enumeration unsupported */
  }
  for (const name of names) {
    await new Promise<void>((resolve) => {
      const req = indexedDB.deleteDatabase(name);
      req.onsuccess = req.onerror = req.onblocked = () => resolve();
    });
  }
  try {
    localStorage.setItem(markerKey, seed);
  } catch {
    /* private mode */
  }
}

export { SQL_ROW_CAP };
