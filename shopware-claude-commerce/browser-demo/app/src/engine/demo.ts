// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * The demo controller: boots the in-browser Shopware (Playground) and the Pyodide agent host
 * side by side, keeps the storefront frame's overlay informed, binds the shopping agent to the
 * storefront's cart, and turns the agent's checkout link into the Shopware handoff. React
 * reads its state through useDemo() (useSyncExternalStore).
 */
import type { AgentRole, AnthropicAccess, HostBootConfig, ShopConfig } from '../../../host/protocol';
import { AgentHost, installFetchShim, VIRTUAL_ORIGINS } from './agent-host';
import {
  BOOTSTRAP_URL,
  DEMO_CONTEXT_PATH,
  HANDOFF_CONTINUE_PATH,
  OVERLAY_MESSAGE_TYPE,
  OVERLAY_STATUS_TYPE,
  PYODIDE_INDEX_URL,
  PROXY_PATH,
  PROXY_STATUS_PATH,
  PYODIDE_PACKAGES_URL,
  REPO_TREE_URL,
  SHOP_CONFIG_URL,
  STORAGE_KEYS,
  STOREFRONT_HOME,
  WHEELS_MANIFEST_URL,
} from './demo-config';
import { Playground } from './playground';
import { rewriteUcpConfig } from '../../../build/ucp-origin.mjs';
import { demoUrl, shopPublicOrigin } from './public-base';
import { extractStorefrontProduct } from './shop-bridge.mjs';

export type DemoView = 'shop' | 'shopping' | 'merchant';
export const DEMO_VIEWS: readonly DemoView[] = ['shop', 'shopping', 'merchant'];
export type StepState = 'pending' | 'active' | 'done' | 'error';
export type BootStep = { id: string; label: string; state: StepState; ms?: number; detail?: string };
export type AgentState = 'idle' | 'loading' | 'ready' | 'error';
/** `ready`: the local server proxies with a key. `unconfigured`: server runs without a key. `absent`: static host. */
export type ProxyStatus = 'unknown' | 'ready' | 'unconfigured' | 'absent';

export type DemoState = {
  view: DemoView;
  shopReady: boolean;
  shopError: string | null;
  hostReady: boolean;
  hostError: string | null;
  agents: Record<AgentRole, AgentState>;
  agentErrors: Partial<Record<AgentRole, string>>;
  steps: BootStep[];
  statusText: string;
  phpBusy: number;
  phpRequests: number;
  anthropic: AnthropicAccess;
  proxyStatus: ProxyStatus;
  shop: ShopConfig | null;
  timings: Record<string, number>;
  versions: Record<string, string>;
  toast: string | null;
  framePath: string;
};

type Listener = () => void;

declare global {
  interface Window {
    __demoTimings?: { start: number; marks: Record<string, number> };
    __demo?: DemoController;
  }
}

const STEP_LABELS: [string, string][] = [
  ['engine', 'Download PHP 8.4 + MariaDB WebAssembly and the Shopware image'],
  ['db', 'Seed MariaDB from the snapshot (products, orders, plugins, UCP config)'],
  ['storefront', 'Render the Shopware storefront'],
  ['pyodide', 'Load Python (Pyodide) + agent packages'],
  ['shopping', 'Start the shopping agent (UCP over MCP, Store API)'],
  ['merchant', 'Start the merchant agent (Admin MCP)'],
];

const OVERLAY_ACTIONS: Record<string, DemoView> = { 'open-shopping': 'shopping', 'open-merchant': 'merchant' };

/** Symfony/Shopware error documents still fire iframe onLoad — do not treat them as a rendered catalogue. */
export function storefrontDocumentLooksFailed(doc: Document | null): boolean {
  const text = doc?.body?.innerText || '';
  return /Oops! An Error Occurred/.test(text) || /The server returned a\s+'[45]\d\d/.test(text) || /The server returned a\s+"[45]\d\d/.test(text);
}

function readStoredMode(): AnthropicAccess['mode'] {
  try {
    return localStorage.getItem(STORAGE_KEYS.anthropicMode) === 'byok' ? 'byok' : 'proxy';
  } catch {
    return 'proxy';
  }
}

/** One budget id per tab (sessionStorage), sent as `x-demo-session`; the proxy accounts against it. */
function proxySessionToken(): string {
  try {
    const existing = sessionStorage.getItem(STORAGE_KEYS.proxySession);
    if (existing) return existing;
    const minted = crypto.randomUUID();
    sessionStorage.setItem(STORAGE_KEYS.proxySession, minted);
    return minted;
  } catch {
    return crypto.randomUUID();
  }
}

export class DemoController {
  readonly playground: Playground;
  readonly host: AgentHost;
  private frame: HTMLIFrameElement | null = null;
  private listeners = new Set<Listener>();
  private started = false;
  private shopBootPromise: Promise<void> | null = null;
  private storefrontProduct: { id: string; name: string } | null = null;
  private lastFocusedProductId: string | null = null;
  private stepStart = new Map<string, number>();
  state: DemoState;

  constructor() {
    this.state = {
      view: 'shop',
      shopReady: false,
      shopError: null,
      hostReady: false,
      hostError: null,
      agents: { shopping: 'idle', merchant: 'idle' },
      agentErrors: {},
      steps: STEP_LABELS.map(([id, label]) => ({ id, label, state: 'pending' })),
      statusText: 'Starting…',
      phpBusy: 0,
      phpRequests: 0,
      anthropic: { mode: readStoredMode(), proxyUrl: new URL(PROXY_PATH, location.origin).href, sessionToken: proxySessionToken() },
      proxyStatus: 'unknown',
      shop: null,
      timings: {},
      versions: {},
      toast: null,
      framePath: '',
    };
    this.playground = new Playground({ onStatus: (text) => this.setStatus(text) });
    this.host = new AgentHost(this.playground);
    this.playground.onActivity((busy, count) => this.update({ phpBusy: busy, phpRequests: count }));
    this.host.on((event) => this.onHostEvent(event));
    installFetchShim(this.host);
    window.addEventListener('message', (event) => this.onFrameMessage(event));
    document.addEventListener('click', (event) => this.onDocumentClick(event), true);
    window.__demo = this;
  }

  // ------------------------------------------------------------------ store plumbing

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private update(patch: Partial<DemoState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
    this.pushOverlayStatus();
  }

  private setStatus(text: string): void {
    this.update({ statusText: text });
  }

  private mark(name: string): number {
    const at = Math.round(performance.now() - (window.__demoTimings?.start ?? 0));
    if (window.__demoTimings) window.__demoTimings.marks[name] = at;
    this.update({ timings: { ...this.state.timings, [name]: at } });
    return at;
  }

  private step(id: string, state: StepState, detail?: string): void {
    const now = performance.now();
    if (state === 'active') this.stepStart.set(id, now);
    const startedAt = this.stepStart.get(id);
    const steps = this.state.steps.map((step) =>
      step.id === id ? { ...step, state, detail: detail ?? step.detail, ms: state === 'done' && startedAt ? Math.round(now - startedAt) : step.ms } : step
    );
    this.update({ steps });
  }

  toast(text: string | null): void {
    this.update({ toast: text });
    if (text) window.setTimeout(() => this.state.toast === text && this.update({ toast: null }), 6000);
  }

  // ------------------------------------------------------------------------- boot

  attachFrame(frame: HTMLIFrameElement | null): void {
    this.frame = frame;
  }

  start(): void {
    if (this.started) return;
    this.started = true;
    this.shopBootPromise = this.bootShop();
    void this.bootHost();
    void this.probeProxy();
  }

  private async bootShop(): Promise<void> {
    this.step('engine', 'active');
    try {
      const shopConfigRes = await fetch(SHOP_CONFIG_URL, { cache: 'no-store' });
      if (!shopConfigRes.ok) throw new Error('shop-config.json missing — run npm run build');
      const shop = (await shopConfigRes.json()) as ShopConfig;
      this.update({ shop });
      await this.playground.boot();
      this.step('engine', 'done');
      this.step('db', 'active');
      await this.rewriteUcpOrigin(shop.seedOrigin);
      this.step('db', 'done');
      this.mark('engine-ready');
      this.step('storefront', 'active');
      this.openInFrame(STOREFRONT_HOME);
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      this.step('engine', 'error', text);
      this.update({ shopError: text, statusText: 'Shopware failed to boot: ' + text });
    }
  }

  /** Called by the frame's onLoad: the first real Shopware document ends the boot. */
  frameLoaded(): void {
    const frame = this.frame;
    let path = '';
    let title = '';
    let oops = false;
    try {
      const loc = frame?.contentWindow?.location;
      path = loc && loc.href !== 'about:blank' ? (loc.pathname || '/') + (loc.search || '') : '';
      title = frame?.contentDocument?.title || '';
      oops = storefrontDocumentLooksFailed(frame?.contentDocument ?? null);
    } catch {
      path = '';
    }
    if (!path) return;
    this.update({ framePath: path });
    this.captureStorefrontProduct(frame?.contentDocument ?? null);
    if (!this.state.shopReady) {
      const ms = this.mark('storefront-rendered');
      if (oops) {
        this.step('storefront', 'error', title || 'Shopware returned an error page');
        this.update({
          shopError: 'Shopware storefront returned an error page (Oops / 400 / 500). Try Reset, then a hard refresh.',
          statusText: `Shopware storefront failed after ${(ms / 1000).toFixed(1)} s`,
        });
        return;
      }
      this.step('storefront', 'done', title);
      this.update({ shopReady: true, statusText: `Shopware storefront rendered after ${(ms / 1000).toFixed(1)} s${title ? ' — ' + title : ''}` });
      void this.startAgentsWhenReady();
    }
    this.pushOverlayStatus();
  }

  private async bootHost(): Promise<void> {
    this.step('pyodide', 'active');
    try {
      const [packagesRes, wheelsRes, shopConfigRes] = await Promise.all([
        fetch(PYODIDE_PACKAGES_URL),
        fetch(WHEELS_MANIFEST_URL),
        fetch(SHOP_CONFIG_URL, { cache: 'no-store' }),
      ]);
      if (!packagesRes.ok || !wheelsRes.ok || !shopConfigRes.ok) {
        throw new Error('agent host artifacts missing under /demo/ — run npm run build:host');
      }
      const packages = (await packagesRes.json()) as { packages: string[] };
      const wheels = (await wheelsRes.json()) as { wheels: string[] };
      const shop = (await shopConfigRes.json()) as ShopConfig;
      const config: HostBootConfig = {
        pyodideIndexUrl: new URL(PYODIDE_INDEX_URL, location.origin).href,
        pyodidePackages: packages.packages,
        wheelUrls: wheels.wheels.map((name) => new URL(demoUrl(`/demo/wheels/${name}`), location.origin).href),
        repoTreeUrl: new URL(REPO_TREE_URL, location.origin).href,
        bootstrapUrl: new URL(BOOTSTRAP_URL, location.origin).href,
        shopOrigin: shopPublicOrigin(),
        shop,
        agentSigningKeyPem: shop.agentSigningKeyPem,
        virtualOrigins: VIRTUAL_ORIGINS,
        anthropic: this.state.anthropic,
        operator: 'Demo Merchant',
        logLevel: new URLSearchParams(location.search).get('log') || 'INFO',
      };
      await this.host.boot(config);
      this.step('pyodide', 'done');
      this.mark('host-ready');
      this.update({ hostReady: true });
      void this.startAgentsWhenReady();
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      this.step('pyodide', 'error', text);
      this.update({ hostError: text });
    }
  }

  private agentsStarting = false;

  private async startAgentsWhenReady(): Promise<void> {
    if (!this.state.hostReady || !this.state.shopReady || this.agentsStarting) return;
    this.agentsStarting = true;
    // Sequential: both lifespans warm up against the single PHP instance.
    for (const role of ['shopping', 'merchant'] as AgentRole[]) {
      this.step(role, 'active');
      this.update({ agents: { ...this.state.agents, [role]: 'loading' } });
      try {
        await this.host.startRole(role);
        this.step(role, 'done');
        this.mark(`${role}-ready`);
        this.update({ agents: { ...this.state.agents, [role]: 'ready' } });
      } catch (error) {
        const text = error instanceof Error ? error.message : String(error);
        this.step(role, 'error', text);
        this.update({ agents: { ...this.state.agents, [role]: 'error' }, agentErrors: { ...this.state.agentErrors, [role]: text } });
      }
    }
    const agentsReadyAt = this.mark('agents-ready');
    const failed = (['shopping', 'merchant'] as AgentRole[]).filter((role) => this.state.agents[role] === 'error');
    this.setStatus(
      failed.length
        ? `Agent${failed.length > 1 ? 's' : ''} failed to start: ${failed.join(', ')}`
        : `Shopware and both agents run in this tab (ready after ${(agentsReadyAt / 1000).toFixed(1)} s)`
    );
  }

  /**
   * The seed baked the build-time loopback origin into the UCP config (profileDomain,
   * continueUrlTemplate, embedded origins). Point URL fields at the live shop public
   * URL (Pages prefix included) but keep `embeddedAllowedOrigins` /
   * `embeddedFrameAncestors` as pathless origins — SwagAgenticCommerce rejects a path
   * there (`UcpConfigException`) and the storefront answers 500, then 400.
   */
  private async rewriteUcpOrigin(seedOrigin: string): Promise<void> {
    const livePublic = shopPublicOrigin();
    const liveOrigin = location.origin;
    const previous = localStorage.getItem(STORAGE_KEYS.ucpOrigin);
    const staleOrigins = [seedOrigin, previous].filter((o): o is string => !!o);
    const escape = (value: string) => value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    try {
      const result = await this.playground.sql(
        'SELECT LOWER(HEX(sales_channel_id)) AS sales_channel_id, config_json FROM swag_agentic_commerce_ucp_config'
      );
      for (const row of result.rows as { sales_channel_id: string; config_json: string }[]) {
        const config = JSON.parse(String(row.config_json)) as Record<string, unknown>;
        const rewritten = rewriteUcpConfig(config, staleOrigins, livePublic, liveOrigin) as Record<string, unknown>;
        const next = JSON.stringify(rewritten);
        if (next === JSON.stringify(config)) continue;
        await this.playground.sql(
          `UPDATE swag_agentic_commerce_ucp_config SET config_json = '${escape(next)}' WHERE sales_channel_id = UNHEX('${row.sales_channel_id}')`
        );
        console.info('ucp config origin rewritten →', livePublic, 'for sales channel', row.sales_channel_id);
      }
      localStorage.setItem(STORAGE_KEYS.ucpOrigin, livePublic);
    } catch (error) {
      console.warn('ucp config origin rewrite skipped', error);
    }
  }

  // ------------------------------------------------------------------------ frame

  openInFrame(path: string): void {
    if (!this.frame) return;
    const target = path.startsWith('/') ? path : '/' + path;
    this.frame.src = shopPublicOrigin() + target;
  }

  /**
   * The storefront session's cart token, served by the DemoOverlay plugin's JSON route (the
   * session cookie travels along: same origin, and the SW routes the call into PHP WASM).
   */
  async storefrontContextToken(): Promise<string | null> {
    if (!this.state.shopReady) return null;
    try {
      const res = await fetch(DEMO_CONTEXT_PATH, { headers: { accept: 'application/json' }, cache: 'no-store' });
      if (!res.ok) return null;
      const body = (await res.json()) as { token?: string };
      return body.token || null;
    } catch {
      return null;
    }
  }

  private pushOverlayStatus(): void {
    const win = this.frame?.contentWindow;
    if (!win) return;
    const { shopReady, hostReady, hostError, shopError, agents, statusText } = this.state;
    const phase = shopError || hostError ? 'error' : shopReady && hostReady && agents.shopping !== 'loading' && agents.merchant !== 'loading' ? 'ready' : 'booting';
    const text = hostError
      ? 'Agent host failed: ' + hostError
      : !hostReady
        ? 'Shopware is up; loading the Python agent host (Pyodide)…'
        : agents.shopping === 'loading'
          ? 'Starting the shopping agent…'
          : agents.merchant === 'loading'
            ? 'Starting the merchant agent…'
            : agents.shopping === 'ready' && agents.merchant === 'ready'
              ? 'Everything runs in this tab. Pick a demo.'
              : statusText;
    try {
      win.postMessage({ type: OVERLAY_STATUS_TYPE, text, phase, agents }, location.origin);
    } catch {
      /* frame navigating */
    }
  }

  private onFrameMessage(event: MessageEvent): void {
    if (event.origin !== location.origin) return;
    const data = event.data || {};
    if (data.type !== OVERLAY_MESSAGE_TYPE) return;
    if (data.action === 'ready') {
      this.pushOverlayStatus();
      const productId = typeof data.productId === 'string' ? data.productId : '';
      if (/^[0-9a-f]{32}$/i.test(productId)) {
        this.rememberStorefrontProduct(productId, String(data.productName || ''));
      } else {
        this.captureStorefrontProduct(this.frame?.contentDocument ?? null);
      }
      return;
    }
    const view = OVERLAY_ACTIONS[String(data.action)];
    if (view) this.setView(view);
  }

  // ------------------------------------------------------------------------- views

  setView(view: DemoView): void {
    if (!DEMO_VIEWS.includes(view)) {
      console.warn('ignoring unknown demo view', view);
      return;
    }
    if (view === 'shopping' && this.state.view !== 'shopping') {
      // The vendored StoreShell reads the cart id when it mounts: bind first, then mount.
      void this.bindShoppingCart()
        .finally(() => this.applyView(view))
        .then(() => {
          void this.syncShoppingCatalogWhenReady();
          void this.focusStorefrontProductWhenReady();
        });
      return;
    }
    this.applyView(view);
  }

  private applyView(view: DemoView): void {
    this.update({ view });
    document.documentElement.classList.toggle('demo-theme-storefront', view === 'shopping');
    document.documentElement.classList.toggle('demo-theme-merchant', view === 'merchant');
  }

  /**
   * Same cart in the assistant and in the shop: the storefront's context token becomes the
   * cart the shopping session attaches to (storefront/web reads this key on session start).
   */
  async bindShoppingCart(): Promise<string | null> {
    const token = await this.storefrontContextToken();
    if (!token) return null;
    try {
      const current = localStorage.getItem(STORAGE_KEYS.storefrontCartId);
      if (current !== token) {
        localStorage.setItem(STORAGE_KEYS.storefrontCartId, token);
        // A previous session may hold another cart: start fresh so it re-attaches.
        sessionStorage.removeItem(STORAGE_KEYS.storefrontSession);
        this.lastFocusedProductId = null;
      }
    } catch {
      /* storage unavailable */
    }
    return token;
  }

  private captureStorefrontProduct(doc: Document | null): void {
    if (!doc) return;
    const found = extractStorefrontProduct(doc.documentElement?.outerHTML || doc.body?.innerHTML || '');
    if (found) this.rememberStorefrontProduct(found.id, found.name);
  }

  private rememberStorefrontProduct(id: string, name: string): void {
    if (!/^[0-9a-f]{32}$/i.test(id)) return;
    this.storefrontProduct = { id, name };
    if (this.state.view === 'shopping') void this.focusStorefrontProductWhenReady();
  }

  private async shoppingSessionId(attempts = 25): Promise<string> {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const sessionId = sessionStorage.getItem(STORAGE_KEYS.storefrontSession) || '';
        if (sessionId) return sessionId;
      } catch {
        /* storage unavailable */
      }
      await new Promise((resolve) => window.setTimeout(resolve, 150));
    }
    return '';
  }

  /** Grid products → session provenance so chat can name what the customer already sees. */
  private async syncShoppingCatalogWhenReady(): Promise<void> {
    const sessionId = await this.shoppingSessionId();
    if (!sessionId) return;
    try {
      await fetch(`${VIRTUAL_ORIGINS.shopping}/api/session/sync-catalog`, {
        method: 'POST',
        headers: { accept: 'application/json', 'X-Session-Id': sessionId },
      });
    } catch {
      /* host still booting */
    }
  }

  /**
   * H7: the iframe PDP must enter session provenance through get_product_details
   * before chat can talk about "the current one" or add it.
   */
  private async focusStorefrontProductWhenReady(): Promise<void> {
    const product = this.storefrontProduct;
    if (!product || product.id === this.lastFocusedProductId) return;
    for (let attempt = 0; attempt < 25; attempt += 1) {
      const sessionId = await this.shoppingSessionId(1);
      if (sessionId) {
        try {
          const res = await fetch(`${VIRTUAL_ORIGINS.shopping}/api/session/focus`, {
            method: 'POST',
            headers: {
              accept: 'application/json',
              'content-type': 'application/json',
              'X-Session-Id': sessionId,
            },
            body: JSON.stringify({ product_id: product.id }),
          });
          if (res.ok) {
            this.lastFocusedProductId = product.id;
            return;
          }
        } catch {
          /* host still booting */
        }
      }
      await new Promise((resolve) => window.setTimeout(resolve, 150));
    }
  }

  /**
   * The agent's checkout link points at the (virtual) storefront host; resolve it here:
   * fetch the one-time handoff code from the host and continue in the Shopware checkout.
   */
  private onDocumentClick(event: MouseEvent): void {
    const target = event.target as HTMLElement | null;
    const anchor = target?.closest?.('a[data-checkout-link]') as HTMLAnchorElement | null;
    if (!anchor) return;
    const href = anchor.getAttribute('href') || '';
    if (!href.startsWith(VIRTUAL_ORIGINS.shopping + '/')) return;
    event.preventDefault();
    event.stopPropagation();
    void this.continueCheckout(href);
  }

  async continueCheckout(ticketUrl: string): Promise<void> {
    try {
      const res = await fetch(ticketUrl, { headers: { Accept: 'text/html' } });
      if (!res.ok) throw new Error(`handoff ticket answered ${res.status}`);
      const html = await res.text();
      const match = /name="code" value="([^"]+)"/.exec(html);
      if (!match) throw new Error('no handoff code in the ticket page');
      const code = match[1].replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>');
      this.setView('shop');
      this.openInFrame(`${HANDOFF_CONTINUE_PATH}?code=${encodeURIComponent(code)}`);
      this.toast('Handing the cart to the Shopware checkout…');
    } catch (error) {
      this.toast('Checkout handoff failed: ' + (error instanceof Error ? error.message : String(error)));
    }
  }

  // --------------------------------------------------------------------- model access

  /**
   * Ask the same-origin proxy whether it exists and holds a key. On a static host the request
   * yields the SPA shell (HTML) or a 404 → `absent`; the visitor then needs their own key.
   */
  async probeProxy(): Promise<ProxyStatus> {
    let status: ProxyStatus = 'absent';
    try {
      const res = await fetch(PROXY_STATUS_PATH, { headers: { accept: 'application/json', 'x-demo-session': this.state.anthropic.sessionToken || '' }, cache: 'no-store' });
      if (res.ok && (res.headers.get('content-type') || '').includes('application/json')) {
        const body = (await res.json()) as { configured?: boolean };
        status = body.configured ? 'ready' : 'unconfigured';
      }
    } catch {
      status = 'absent';
    }
    if (status === 'absent' && this.state.anthropic.mode === 'proxy' && !this.state.anthropic.apiKey) {
      this.update({ proxyStatus: status, anthropic: { ...this.state.anthropic, mode: 'byok' } });
    } else {
      this.update({ proxyStatus: status });
    }
    if (status !== 'ready' && !this.state.anthropic.apiKey) {
      this.toast(
        status === 'absent'
          ? 'This GitHub Pages build has no Anthropic proxy. Paste your own API key (top right) to chat; the shop runs without one.'
          : 'The local server has no ANTHROPIC_API_KEY — add it to .env or paste your own key (top right).',
      );
    }
    return status;
  }

  setAnthropic(access: Partial<AnthropicAccess>): void {
    const next = { ...this.state.anthropic, ...access };
    this.update({ anthropic: next });
    try {
      localStorage.setItem(STORAGE_KEYS.anthropicMode, next.mode);
    } catch {
      /* storage unavailable */
    }
    if (this.state.hostReady) this.host.setAnthropic(next);
  }

  async resetDemo(): Promise<void> {
    this.setStatus('Resetting the in-browser database…');
    try {
      await this.playground.reset();
    } finally {
      try {
        localStorage.removeItem(STORAGE_KEYS.ucpOrigin);
        localStorage.removeItem(STORAGE_KEYS.storefrontCartId);
        sessionStorage.removeItem(STORAGE_KEYS.storefrontSession);
      } catch {
        /* storage unavailable */
      }
      location.reload();
    }
  }

  private onHostEvent(event: Parameters<Parameters<AgentHost['on']>[0]>[0]): void {
    switch (event.type) {
      case 'status':
        this.setStatus(event.text);
        break;
      case 'booted':
        this.update({ versions: event.versions });
        break;
      case 'log':
        if (event.level === 'error') console.error('[host]', event.text);
        else if (event.level === 'warn') console.warn('[host]', event.text);
        else console.info('[host]', event.text);
        break;
      case 'error':
        console.error('[host]', event.context, event.text);
        break;
      default:
        break;
    }
  }
}

let controller: DemoController | null = null;

export function getDemo(): DemoController {
  if (!controller) controller = new DemoController();
  return controller;
}
