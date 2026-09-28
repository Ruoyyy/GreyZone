// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Same-origin Anthropic Messages API proxy: `/api/anthropic/v1/messages` → api.anthropic.com.
 *
 *   - injects `x-api-key` (+ `anthropic-workspace-id`) from the server environment; the key
 *     never reaches the browser and is never logged
 *   - streams the upstream body through unchanged (SSE works turn by turn)
 *   - simple budgets: requests + output tokens per demo session (`x-demo-session`, minted by
 *     the shell), requests per client IP per hour, concurrent streams per session, and an
 *     upper bound for `max_tokens`
 *   - only the Messages endpoints are exposed; everything else under /api/anthropic is 404
 *
 * The playground service worker leaves `/api/anthropic/*` alone (patches/playground-*.patch);
 * without that the request would be routed into PHP as a Shopware Admin API call.
 */
import { Readable } from 'node:stream';

export const PROXY_PREFIX = '/api/anthropic';
const UPSTREAM = 'https://api.anthropic.com';
const ALLOWED_ENDPOINTS = new Set(['/v1/messages', '/v1/messages/count_tokens']);
const MAX_BODY_BYTES = 4 * 1024 * 1024;
const SESSION_HEADER = 'x-demo-session';
const SESSION_TOKEN = /^[A-Za-z0-9_-]{8,128}$/;
const HOUR_MS = 60 * 60 * 1000;
const SWEEP_INTERVAL_MS = 5 * 60 * 1000;
const SESSION_IDLE_TTL_MS = 6 * HOUR_MS;
/** Request headers copied from the browser to the upstream (everything else is dropped). */
const FORWARDED_REQUEST_HEADERS = new Set(['content-type', 'accept', 'anthropic-version', 'anthropic-beta', 'x-stainless-retry-count', 'x-stainless-timeout']);
/** Upstream response headers handed back to the browser. */
const FORWARDED_RESPONSE_HEADERS = new Set(['content-type', 'request-id', 'anthropic-ratelimit-requests-remaining', 'anthropic-ratelimit-tokens-remaining', 'retry-after']);

export const DEFAULT_LIMITS = Object.freeze({
  /** Messages requests one demo session may make (a chat turn with tools is several). */
  requestsPerSession: 120,
  /** Output tokens one demo session may consume (counted from streamed `usage`). */
  outputTokensPerSession: 200_000,
  /** Messages requests per client IP per rolling hour (all sessions of that IP). */
  requestsPerIpPerHour: 600,
  /** Streams one session may keep open at once (the two agents + one retry). */
  concurrentPerSession: 3,
  /** Upper bound applied to the request's `max_tokens`. */
  maxTokensPerRequest: 8192,
});

export function limitsFromEnv(env, envInt) {
  return {
    requestsPerSession: envInt(env, 'DEMO_PROXY_REQUESTS_PER_SESSION', DEFAULT_LIMITS.requestsPerSession),
    outputTokensPerSession: envInt(env, 'DEMO_PROXY_OUTPUT_TOKENS_PER_SESSION', DEFAULT_LIMITS.outputTokensPerSession),
    requestsPerIpPerHour: envInt(env, 'DEMO_PROXY_REQUESTS_PER_IP_PER_HOUR', DEFAULT_LIMITS.requestsPerIpPerHour),
    concurrentPerSession: envInt(env, 'DEMO_PROXY_CONCURRENT_PER_SESSION', DEFAULT_LIMITS.concurrentPerSession),
    maxTokensPerRequest: envInt(env, 'DEMO_PROXY_MAX_TOKENS_PER_REQUEST', DEFAULT_LIMITS.maxTokensPerRequest),
    allowedModels: (env.DEMO_PROXY_ALLOWED_MODELS || '')
      .split(',')
      .map((model) => model.trim())
      .filter(Boolean),
  };
}

function json(res, status, body, extraHeaders = {}) {
  const text = JSON.stringify(body);
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extraHeaders });
  res.end(text);
}

function apiError(res, status, type, message, extraHeaders) {
  json(res, status, { type: 'error', error: { type, message } }, extraHeaders);
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(Object.assign(new Error('request body too large'), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function clientIp(req, trustProxyHeader) {
  if (trustProxyHeader) {
    const forwarded = req.headers['x-forwarded-for'];
    if (typeof forwarded === 'string' && forwarded.length) return forwarded.split(',')[0].trim();
  }
  return req.socket.remoteAddress || 'unknown';
}

/** In-memory budgets. Sessions are per browser tab (sessionStorage) — fine for a demo host. */
class Budget {
  constructor(limits) {
    this.limits = limits;
    this.sessions = new Map();
    this.ips = new Map();
    this.timer = setInterval(() => this.sweep(), SWEEP_INTERVAL_MS);
    this.timer.unref?.();
  }

  session(id) {
    let entry = this.sessions.get(id);
    if (!entry) {
      entry = { requests: 0, outputTokens: 0, inFlight: 0, lastSeen: Date.now() };
      this.sessions.set(id, entry);
    }
    entry.lastSeen = Date.now();
    return entry;
  }

  ipWindow(ip) {
    const now = Date.now();
    let stamps = this.ips.get(ip);
    if (!stamps) {
      stamps = [];
      this.ips.set(ip, stamps);
    }
    while (stamps.length && now - stamps[0] > HOUR_MS) stamps.shift();
    return stamps;
  }

  /** Returns `null` when the request may proceed, otherwise `{status, type, message}`. */
  check(sessionId, ip) {
    const session = this.session(sessionId);
    const limits = this.limits;
    if (session.inFlight >= limits.concurrentPerSession) {
      return { status: 429, type: 'rate_limit_error', message: `Demo budget: at most ${limits.concurrentPerSession} concurrent requests per session.` };
    }
    if (session.requests >= limits.requestsPerSession) {
      return { status: 429, type: 'rate_limit_error', message: `Demo budget exhausted: ${limits.requestsPerSession} requests per session. Reload the tab for a new session or use your own key.` };
    }
    if (session.outputTokens >= limits.outputTokensPerSession) {
      return { status: 429, type: 'rate_limit_error', message: `Demo budget exhausted: ${limits.outputTokensPerSession} output tokens per session. Use your own key to continue.` };
    }
    if (this.ipWindow(ip).length >= limits.requestsPerIpPerHour) {
      return { status: 429, type: 'rate_limit_error', message: `Demo budget: ${limits.requestsPerIpPerHour} requests per hour per client reached.` };
    }
    return null;
  }

  begin(sessionId, ip) {
    const session = this.session(sessionId);
    session.requests += 1;
    session.inFlight += 1;
    this.ipWindow(ip).push(Date.now());
    return session;
  }

  end(session, outputTokens) {
    session.inFlight = Math.max(0, session.inFlight - 1);
    session.outputTokens += outputTokens;
  }

  remaining(sessionId) {
    const session = this.sessions.get(sessionId);
    return {
      requests: this.limits.requestsPerSession - (session?.requests || 0),
      outputTokens: this.limits.outputTokensPerSession - (session?.outputTokens || 0),
    };
  }

  sweep() {
    const now = Date.now();
    for (const [id, entry] of this.sessions) {
      if (entry.inFlight === 0 && now - entry.lastSeen > SESSION_IDLE_TTL_MS) this.sessions.delete(id);
    }
    for (const [ip, stamps] of this.ips) {
      while (stamps.length && now - stamps[0] > HOUR_MS) stamps.shift();
      if (!stamps.length) this.ips.delete(ip);
    }
  }

  close() {
    clearInterval(this.timer);
  }
}

/**
 * Follows `"output_tokens":N` through the streamed SSE text (or the JSON body of a
 * non-streaming answer). The last value seen is the final count of the message.
 */
class UsageTracker {
  constructor() {
    this.tail = '';
    this.outputTokens = 0;
  }

  feed(chunk) {
    const text = this.tail + chunk.toString('latin1');
    const matches = text.matchAll(/"output_tokens"\s*:\s*(\d+)/g);
    for (const match of matches) this.outputTokens = Number(match[1]);
    this.tail = text.slice(-64);
  }
}

/**
 * @param {object} options
 * @param {string} options.apiKey            Anthropic key (empty → proxy answers 503 "not configured")
 * @param {string} [options.workspaceId]
 * @param {object} [options.limits]          see DEFAULT_LIMITS (+ optional `allowedModels`)
 * @param {boolean} [options.trustProxyHeader] honour X-Forwarded-For (only behind a reverse proxy)
 * @param {(line: string) => void} [options.log]
 * @param {string} [options.upstreamUrl]     defaults to https://api.anthropic.com (tests point it at a mock)
 */
export function createAnthropicProxy(options) {
  const upstreamBase = (options.upstreamUrl || UPSTREAM).replace(/\/$/, '');
  const limits = { ...DEFAULT_LIMITS, allowedModels: [], ...(options.limits || {}) };
  const budget = new Budget(limits);
  const log = options.log || (() => {});
  const configured = Boolean(options.apiKey);

  function status(res, sessionId) {
    json(res, 200, {
      configured,
      limits: {
        requestsPerSession: limits.requestsPerSession,
        outputTokensPerSession: limits.outputTokensPerSession,
        maxTokensPerRequest: limits.maxTokensPerRequest,
        concurrentPerSession: limits.concurrentPerSession,
      },
      remaining: sessionId ? budget.remaining(sessionId) : null,
    });
  }

  async function forward(req, res, endpoint) {
    if (!configured) {
      apiError(res, 503, 'proxy_not_configured', 'The demo proxy has no ANTHROPIC_API_KEY. Put it into the repo .env (see .env.example) or use your own key in the demo.');
      return;
    }
    const sessionId = String(req.headers[SESSION_HEADER] || '');
    if (!SESSION_TOKEN.test(sessionId)) {
      apiError(res, 400, 'invalid_request_error', `Missing or malformed ${SESSION_HEADER} header.`);
      return;
    }
    const ip = clientIp(req, options.trustProxyHeader);
    const denied = budget.check(sessionId, ip);
    if (denied) {
      log(`proxy ${endpoint} 429 session=${sessionId.slice(0, 8)} ${denied.message}`);
      apiError(res, denied.status, denied.type, denied.message, { 'Retry-After': '60' });
      return;
    }

    let raw;
    try {
      raw = await readBody(req, MAX_BODY_BYTES);
    } catch (error) {
      apiError(res, error.status || 400, 'invalid_request_error', error.message);
      return;
    }
    let payload;
    try {
      payload = JSON.parse(raw.toString('utf8'));
    } catch {
      apiError(res, 400, 'invalid_request_error', 'Body must be JSON.');
      return;
    }
    if (typeof payload !== 'object' || payload === null) {
      apiError(res, 400, 'invalid_request_error', 'Body must be a JSON object.');
      return;
    }
    if (limits.allowedModels.length && !limits.allowedModels.includes(String(payload.model))) {
      apiError(res, 400, 'invalid_request_error', `Model ${JSON.stringify(payload.model)} is not enabled on this demo proxy.`);
      return;
    }
    if (endpoint === '/v1/messages') {
      const requested = Number(payload.max_tokens);
      if (!Number.isFinite(requested) || requested <= 0 || requested > limits.maxTokensPerRequest) payload.max_tokens = limits.maxTokensPerRequest;
    }

    const headers = new Headers();
    for (const [name, value] of Object.entries(req.headers)) {
      if (FORWARDED_REQUEST_HEADERS.has(name) && typeof value === 'string') headers.set(name, value);
    }
    if (!headers.has('content-type')) headers.set('content-type', 'application/json');
    if (!headers.has('anthropic-version')) headers.set('anthropic-version', '2023-06-01');
    headers.set('x-api-key', options.apiKey);
    if (options.workspaceId) headers.set('anthropic-workspace-id', options.workspaceId);

    const controller = new AbortController();
    const onClientGone = () => controller.abort();
    res.on('close', onClientGone);
    const session = budget.begin(sessionId, ip);
    const usage = new UsageTracker();
    const startedAt = Date.now();
    let upstreamStatus = 0;
    try {
      const upstream = await fetch(`${upstreamBase}${endpoint}`, { method: 'POST', headers, body: JSON.stringify(payload), signal: controller.signal });
      upstreamStatus = upstream.status;
      const responseHeaders = { 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no' };
      for (const [name, value] of upstream.headers) {
        if (FORWARDED_RESPONSE_HEADERS.has(name)) responseHeaders[name] = value;
      }
      res.writeHead(upstream.status, responseHeaders);
      if (!upstream.body) {
        res.end();
        return;
      }
      const reader = Readable.fromWeb(upstream.body);
      reader.on('data', (chunk) => {
        usage.feed(chunk);
        if (!res.write(chunk)) reader.pause();
      });
      res.on('drain', () => reader.resume());
      await new Promise((resolve, reject) => {
        reader.on('end', resolve);
        reader.on('error', reject);
      });
      res.end();
    } catch (error) {
      if (controller.signal.aborted) {
        log(`proxy ${endpoint} aborted by client session=${sessionId.slice(0, 8)}`);
      } else if (!res.headersSent) {
        apiError(res, 502, 'api_error', `Upstream request failed: ${error.message}`);
      } else {
        res.destroy(error);
      }
    } finally {
      res.off('close', onClientGone);
      budget.end(session, usage.outputTokens);
      log(`proxy ${endpoint} ${upstreamStatus || '-'} ${Date.now() - startedAt}ms session=${sessionId.slice(0, 8)} out_tokens=${usage.outputTokens} used=${session.requests}/${limits.requestsPerSession}`);
    }
  }

  return {
    prefix: PROXY_PREFIX,
    limits,
    configured,
    close: () => budget.close(),
    /** Handles the request when it is under the proxy prefix; returns false otherwise. */
    async handle(req, res, pathname) {
      if (pathname !== PROXY_PREFIX && !pathname.startsWith(`${PROXY_PREFIX}/`)) return false;
      const endpoint = pathname.slice(PROXY_PREFIX.length) || '/';
      if (endpoint === '/status' && req.method === 'GET') {
        status(res, String(req.headers[SESSION_HEADER] || ''));
        return true;
      }
      if (!ALLOWED_ENDPOINTS.has(endpoint)) {
        apiError(res, 404, 'not_found_error', `Only ${[...ALLOWED_ENDPOINTS].join(', ')} are proxied.`);
        return true;
      }
      if (req.method !== 'POST') {
        apiError(res, 405, 'invalid_request_error', 'Use POST.', { Allow: 'POST' });
        return true;
      }
      await forward(req, res, endpoint);
      return true;
    },
  };
}
