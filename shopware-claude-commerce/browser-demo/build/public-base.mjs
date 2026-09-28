// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * GitHub project Pages serves this demo under /<repo>/, not at the origin root.
 * Local `npm start` stays at `/`. DEMO_BASE_PATH is the single switch (Vite `base`,
 * worker injection, versions.json zip/dump URLs, quoted engine paths).
 */

/** Quoted absolute paths the playground engine fetches from the origin. */
export const ENGINE_PATH_PREFIXES = Object.freeze([
  '/mariadb/',
  '/assets/',
  '/php/',
  '/versions/',
  '/demo/',
  '/api/anthropic/',
  '/browser-worker.js',
  '/service-worker.js',
  '/versions.json',
  '/shopware.zip',
  '/shopware.sql.gz',
  '/app.js',
]);

const PAGES_FILE_LIMIT_BYTES = 100 * 1024 * 1024;

/**
 * Vite `base` form: always starts and ends with `/` (`/` at the origin root).
 * Accepts `/repo`, `/repo/`, `repo`, or empty.
 */
export function viteBaseFromEnv(env = process.env) {
  const raw = env.DEMO_BASE_PATH;
  if (raw == null || raw === '' || raw === '/') return '/';
  const trimmed = String(raw).trim();
  if (!trimmed || trimmed === '/') return '/';
  const withLead = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  return withLead.endsWith('/') ? withLead : `${withLead}/`;
}

/**
 * Prefix written into workers as `self.__DEMO_PUBLIC_BASE__` and prepended to
 * origin-absolute engine URLs. Empty string when the site is served at `/`.
 */
export function publicBasePrefix(base = viteBaseFromEnv()) {
  if (!base || base === '/') return '';
  return base.endsWith('/') ? base.slice(0, -1) : base;
}

export function injectPublicBaseBanner(source, prefix) {
  const value = JSON.stringify(prefix || '');
  const banner = `self.__DEMO_PUBLIC_BASE__ = ${value};\n`;
  if (source.startsWith('self.__DEMO_PUBLIC_BASE__')) {
    return source.replace(/^self\.__DEMO_PUBLIC_BASE__ = .*?;\n/, banner);
  }
  return banner + source;
}

/** Rewrite quoted origin-absolute engine paths so they resolve under a project Pages prefix. */
export function rewriteEngineSource(source, prefix) {
  if (!prefix) return source;
  let out = source;
  for (const path of ENGINE_PATH_PREFIXES) {
    const prefixed = prefix + path;
    for (const quote of ['"', "'"]) {
      const from = quote + path;
      const to = quote + prefixed;
      const already = quote + prefixed;
      out = out.split(already).join('\u0000');
      out = out.split(from).join(to);
      out = out.split('\u0000').join(already);
    }
  }
  return out;
}

/**
 * Prepare a playground worker for the assembled site.
 *
 * The service worker classifies routes *after* `withoutPublicBase()` (logical
 * `/demo/`, `/php/`, …). Rewriting those quoted prefixes to `/<repo>/demo/`
 * makes every check miss, so `/php/auto_prepend.php` and `/demo/host/bootstrap.py`
 * are sent to PHP WASM while that worker is still fetching them — deadlock,
 * status stuck on "mounting backends". Only inject the public-base banner there.
 * `browser-worker.js` still needs the rewrite for hardcoded resource URLs.
 */
export function prepareEngineWorkerSource(filename, source, prefix) {
  const name = String(filename || '').split(/[/\\]/).pop();
  const rewritten = name === 'service-worker.js' ? source : rewriteEngineSource(source, prefix);
  return injectPublicBaseBanner(rewritten, prefix);
}

export function prefixManifestUrls(manifest, prefix) {
  if (!prefix || !manifest || typeof manifest !== 'object') return manifest;
  const prefixUrl = (url) => {
    if (typeof url !== 'string' || !url.startsWith('/') || url.startsWith(prefix + '/')) return url;
    return prefix + url;
  };
  const versions = Array.isArray(manifest.versions)
    ? manifest.versions.map((entry) => ({
        ...entry,
        zip: prefixUrl(entry.zip),
        dump: prefixUrl(entry.dump),
      }))
    : manifest.versions;
  return { ...manifest, versions };
}

export function pagesFileLimitBytes() {
  return PAGES_FILE_LIMIT_BYTES;
}

/**
 * Absolute URL for a Vite-emitted file (`demo/assets/MerchantView-….js`).
 * Relative `demo/assets/…` mapDeps resolve against the wrong base inside
 * workers / `about:client` on project Pages.
 */
export function builtAssetUrl(filename, base = viteBaseFromEnv()) {
  const name = String(filename || '').replace(/^\//, '');
  const prefix = !base || base === '/' ? '/' : base.endsWith('/') ? base : `${base}/`;
  return `${prefix}${name}`;
}
