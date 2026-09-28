// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Page-side client of host/worker.ts (the Pyodide agent host) and the fetch() shim that
 * routes the web UIs' API calls into it.
 *
 * The storefront and merchant web apps talk to "their" FastAPI host through
 * `AgentApi(root, prefix)` (vendor/web-shared/api.ts) with plain `fetch`. Here `root` is a
 * virtual origin per role (see VIRTUAL_ORIGINS); `installFetchShim()` intercepts those URLs
 * and answers them from the worker with a streaming Response, so SSE chat turns stream
 * exactly like they do against uvicorn.
 */

import type { AgentRole, AnthropicAccess, HostBootConfig, HostWorkerInbound, HostWorkerOutbound } from '../../../host/protocol';
import type { Playground } from './playground';
import { PUBLIC_BASE } from './public-base';
import { phpRequestUrl } from './shop-bridge.mjs';

export const VIRTUAL_ORIGINS: Record<AgentRole, string> = {
  shopping: 'http://shopping.agent-host.invalid',
  merchant: 'http://merchant.agent-host.invalid',
};

type Pending = {
  controller: ReadableStreamDefaultController<Uint8Array> | null;
  resolveHead: (response: Response) => void;
  rejectHead: (error: Error) => void;
  stream: ReadableStream<Uint8Array>;
  headSent: boolean;
};

export type HostEvent =
  | { type: 'status'; text: string; phase?: string }
  | { type: 'booted'; ms: number; versions: Record<string, string> }
  | { type: 'role-ready'; role: AgentRole; ms: number }
  | { type: 'log'; level: 'info' | 'warn' | 'error'; text: string }
  | { type: 'error'; context: string; text: string }
  | { type: 'anthropic-mode'; mode: AnthropicAccess['mode'] };

export class AgentHost {
  private worker: Worker | null = null;
  private pending = new Map<string, Pending>();
  private listeners = new Set<(event: HostEvent) => void>();
  private bootPromise: Promise<void> | null = null;
  private rolePromises = new Map<AgentRole, Promise<void>>();
  private waiters = new Map<string, { resolve: () => void; reject: (error: Error) => void }>();

  constructor(private readonly playground: Playground) {}

  on(listener: (event: HostEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(event: HostEvent): void {
    for (const listener of this.listeners) listener(event);
  }

  boot(config: HostBootConfig): Promise<void> {
    if (this.bootPromise) return this.bootPromise;
    this.bootPromise = new Promise<void>((resolve, reject) => {
      const worker = new Worker(new URL('../../../host/worker.ts', import.meta.url), { type: 'module' });
      this.worker = worker;
      worker.addEventListener('message', (event: MessageEvent<HostWorkerOutbound>) => this.onMessage(event.data));
      worker.addEventListener('error', (event) => {
        const error = new Error('agent host worker failed: ' + (event.message || 'unknown error'));
        reject(error);
        this.emit({ type: 'error', context: 'worker', text: error.message });
      });
      this.waiters.set('boot', { resolve, reject });
      this.post({ type: 'boot', config });
    });
    return this.bootPromise;
  }

  startRole(role: AgentRole): Promise<void> {
    const existing = this.rolePromises.get(role);
    if (existing) return existing;
    const promise = new Promise<void>((resolve, reject) => {
      this.waiters.set(`role:${role}`, { resolve, reject });
      this.post({ type: 'start-role', role });
    });
    this.rolePromises.set(role, promise);
    promise.catch(() => this.rolePromises.delete(role));
    return promise;
  }

  setAnthropic(anthropic: Partial<AnthropicAccess>): void {
    this.post({ type: 'set-anthropic', anthropic });
  }

  private post(message: HostWorkerInbound, transfer: Transferable[] = []): void {
    if (!this.worker) throw new Error('agent host not started');
    this.worker.postMessage(message, transfer);
  }

  /** Answer a request for one role's FastAPI app from the worker, streaming the body. */
  async fetch(role: AgentRole, input: Request): Promise<Response> {
    const url = new URL(input.url);
    const id = crypto.randomUUID();
    const headers: Record<string, string> = {};
    input.headers.forEach((value, key) => {
      headers[key] = value;
    });
    headers.host = 'localhost';
    const body = input.method === 'GET' || input.method === 'HEAD' ? null : await input.arrayBuffer();
    return new Promise<Response>((resolveHead, rejectHead) => {
      const pending: Pending = { controller: null, resolveHead, rejectHead, headSent: false, stream: null as unknown as ReadableStream<Uint8Array> };
      pending.stream = new ReadableStream<Uint8Array>({
        start: (controller) => {
          pending.controller = controller;
        },
      });
      this.pending.set(id, pending);
      this.post({ type: 'http', id, role, method: input.method, target: url.pathname + url.search, headers, body }, body ? [body] : []);
    });
  }

  private onMessage(message: HostWorkerOutbound): void {
    switch (message.type) {
      case 'status':
        this.emit({ type: 'status', text: message.text, phase: message.phase });
        return;
      case 'booted':
        this.waiters.get('boot')?.resolve();
        this.waiters.delete('boot');
        this.emit(message);
        return;
      case 'role-ready':
        this.waiters.get(`role:${message.role}`)?.resolve();
        this.waiters.delete(`role:${message.role}`);
        this.emit(message);
        return;
      case 'anthropic-mode':
        this.emit(message);
        return;
      case 'log':
        this.emit(message);
        return;
      case 'error': {
        const error = new Error(message.text);
        if (message.context === 'boot') {
          this.waiters.get('boot')?.reject(error);
          this.waiters.delete('boot');
        } else if (message.context === 'start-role') {
          for (const [key, waiter] of this.waiters) {
            if (key.startsWith('role:')) {
              waiter.reject(error);
              this.waiters.delete(key);
            }
          }
        }
        this.emit(message);
        return;
      }
      case 'http-head': {
        const pending = this.pending.get(message.id);
        if (!pending || pending.headSent) return;
        pending.headSent = true;
        const headers = new Headers();
        for (const [key, value] of message.headers) {
          if (['content-length', 'transfer-encoding', 'content-encoding'].includes(key.toLowerCase())) continue;
          headers.append(key, value);
        }
        const empty = [101, 103, 204, 205, 304].includes(message.status);
        pending.resolveHead(new Response(empty ? null : pending.stream, { status: message.status, headers }));
        return;
      }
      case 'http-chunk': {
        const pending = this.pending.get(message.id);
        pending?.controller?.enqueue(new Uint8Array(message.chunk));
        return;
      }
      case 'http-end': {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (!pending.headSent) {
          pending.headSent = true;
          pending.resolveHead(new Response(JSON.stringify({ detail: 'agent host returned no response' }), { status: 502, headers: { 'content-type': 'application/json' } }));
        }
        try {
          pending.controller?.close();
        } catch {
          /* already closed */
        }
        return;
      }
      case 'shop-request':
        void this.answerShopRequest(message);
        return;
      default:
        return;
    }
  }

  private async answerShopRequest(message: Extract<HostWorkerOutbound, { type: 'shop-request' }>): Promise<void> {
    try {
      const href = phpRequestUrl(message.url, PUBLIC_BASE);
      const res = await this.playground.phpRequest({
        url: href,
        method: message.method,
        headers: { ...message.headers, Host: location.host },
        body: message.body,
      });
      this.post({ type: 'shop-result', id: message.id, status: res.status, headers: res.headers, body: res.body }, res.body ? [res.body] : []);
    } catch (error) {
      this.post({ type: 'shop-result', id: message.id, error: error instanceof Error ? error.message : String(error) });
    }
  }
}

/**
 * Route fetch() calls for the virtual host origins into the worker. Everything else is
 * untouched (same-origin Shopware requests still go through the playground SW).
 */
export function installFetchShim(host: AgentHost): void {
  const original = window.fetch.bind(window);
  const roleFor = (url: string): AgentRole | null => {
    for (const [role, origin] of Object.entries(VIRTUAL_ORIGINS) as [AgentRole, string][]) {
      if (url.startsWith(origin + '/')) return role;
    }
    return null;
  };
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const role = roleFor(request.url);
    if (!role) return original(input, init);
    return host.fetch(role, request);
  }) as typeof window.fetch;
}
