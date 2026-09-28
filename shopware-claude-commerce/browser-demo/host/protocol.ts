// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** Message protocol between the page (app/src/engine/agent-host.ts) and host/worker.ts. */

export type AgentRole = 'shopping' | 'merchant';

export type AnthropicAccess = {
  /**
   * `proxy`: the local Node server (browser-demo/server) injects the key at /api/anthropic/*.
   * GitHub Pages has no proxy. `byok`: the visitor's key, kept in the agent-host worker's memory.
   */
  mode: 'proxy' | 'byok';
  /** Absolute URL of the proxy prefix (same origin `/api/anthropic`, plus the Pages path if any). */
  proxyUrl: string;
  apiKey?: string;
  workspaceId?: string;
  /** Per-tab budget id, sent as `x-demo-session` to the proxy. */
  sessionToken?: string;
};

/** build/prepare-shop.mjs writes this to app/public/demo/shop-config.json. */
export type ShopConfig = {
  builtAt: string;
  shopwareVersion: string;
  seedOrigin: string;
  shopName: string;
  salesChannelId: string;
  salesChannelAccessKey: string;
  integrationAccessKey: string;
  integrationSecretKey: string;
  handoffSecret: string;
  agentProfileUrl: string;
  agentProfile: Record<string, unknown>;
  agentSigningKeyPem: string;
  ucpSignaturePolicy: string;
  admin: { username: string; password: string };
  activePlugins: string[];
  counts: Record<string, number>;
};

export type HostBootConfig = {
  pyodideIndexUrl: string;
  pyodidePackages: string[];
  wheelUrls: string[];
  repoTreeUrl: string;
  bootstrapUrl: string;
  shopOrigin: string;
  shop: ShopConfig;
  agentSigningKeyPem: string;
  /** Origins the page's fetch() shim routes into this worker, one per role. */
  virtualOrigins: Record<AgentRole, string>;
  anthropic: AnthropicAccess;
  operator?: string;
  logLevel?: string;
};

export type HostWorkerInbound =
  | { type: 'boot'; config: HostBootConfig }
  | { type: 'start-role'; role: AgentRole }
  | { type: 'http'; id: string; role: AgentRole; method: string; target: string; headers: Record<string, string>; body: ArrayBuffer | null }
  | { type: 'shop-result'; id: string; status?: number; headers?: Record<string, string | string[]>; body?: ArrayBuffer | null; error?: string }
  | { type: 'set-anthropic'; anthropic: Partial<AnthropicAccess> };

export type HostWorkerOutbound =
  | { type: 'status'; text: string; phase?: string }
  | { type: 'booted'; ms: number; versions: Record<string, string> }
  | { type: 'role-ready'; role: AgentRole; ms: number }
  | { type: 'http-head'; id: string; status: number; headers: [string, string][] }
  | { type: 'http-chunk'; id: string; chunk: ArrayBuffer }
  | { type: 'http-end'; id: string }
  | { type: 'shop-request'; id: string; method: string; url: string; headers: Record<string, string>; body: ArrayBuffer | null }
  | { type: 'anthropic-mode'; mode: AnthropicAccess['mode'] }
  | { type: 'log'; level: 'info' | 'warn' | 'error'; text: string }
  | { type: 'error'; context: string; text: string };
