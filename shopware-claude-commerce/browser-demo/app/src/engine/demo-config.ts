// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** Static locations and defaults of the demo shell. Everything under /demo/ is a build artifact. */

import { demoUrl } from './public-base';

export const DEMO_BASE = `${demoUrl('/demo')}/`;
export const PYODIDE_INDEX_URL = `${DEMO_BASE}pyodide/`;
/** Written by build/fetch-pyodide.mjs: the Pyodide-built packages the host loads. */
export const PYODIDE_PACKAGES_URL = `${PYODIDE_INDEX_URL}packages.json`;
/** Written by build/build-wheels.sh: the pure-Python wheels (blueprint + pins) to install. */
export const WHEELS_MANIFEST_URL = `${DEMO_BASE}wheels/manifest.json`;
export const REPO_TREE_URL = `${DEMO_BASE}host/repo-tree.tar`;
export const BOOTSTRAP_URL = `${DEMO_BASE}host/bootstrap.py`;
export const SHOP_CONFIG_URL = `${DEMO_BASE}shop-config.json`;

/**
 * Same-origin Anthropic proxy of the local Node server (browser-demo/server/anthropic-proxy.mjs).
 * GitHub Pages is static: nothing answers here and the shell falls back to a key pasted in the UI.
 */
export const PROXY_PATH = demoUrl('/api/anthropic');
export const PROXY_STATUS_PATH = `${PROXY_PATH}/status`;

export const STORAGE_KEYS = {
  anthropicMode: 'commerce-agents-demo:anthropic-mode',
  /** sessionStorage: the per-tab budget id the proxy accounts against. */
  proxySession: 'commerce-agents-demo:proxy-session',
  ucpOrigin: 'commerce-agents-demo:ucp-config-origin',
  /** storefront/web's own key for the cart the session should join (lib/StoreShell.tsx). */
  storefrontCartId: 'shopware-storefront-cart-id',
  storefrontSession: 'shopware-storefront-session',
} as const;

export const SHOPWARE_BLUE = '#189EFF';
export const OVERLAY_MESSAGE_TYPE = 'commerce-agents-demo';
export const OVERLAY_STATUS_TYPE = 'commerce-agents-demo-status';
export const STOREFRONT_HOME = '/index.php';
export const STOREFRONT_CART_PATH = '/checkout/cart';
export const HANDOFF_CONTINUE_PATH = '/claude-commerce/continue';
/** DemoOverlay plugin: the storefront session's context token as JSON (demo-only route). */
export const DEMO_CONTEXT_PATH = demoUrl('/commerce-agents-demo/context');
export const ADMIN_PATH = '/admin';
