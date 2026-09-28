// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { originOnly, rewriteUcpConfig } from './ucp-origin.mjs';

describe('UCP origin rewrite for project Pages', () => {
  const seed = 'http://127.0.0.1:4188';
  const livePublic = 'https://agentic-commerce-lab.github.io/shopware-claude-commerce';
  const liveOrigin = 'https://agentic-commerce-lab.github.io';

  it('strips a path from origin-only fields even after a previous bad rewrite', () => {
    const config = {
      profileDomain: livePublic,
      continueUrlTemplate: `${livePublic}/checkout/confirm?checkoutId={checkoutId}`,
      embeddedAllowedOrigins: [livePublic],
      embeddedFrameAncestors: [livePublic],
    };
    const next = rewriteUcpConfig(config, [seed, livePublic], livePublic, liveOrigin);
    assert.equal(next.profileDomain, livePublic);
    assert.equal(next.continueUrlTemplate, `${livePublic}/checkout/confirm?checkoutId={checkoutId}`);
    assert.deepEqual(next.embeddedAllowedOrigins, [liveOrigin]);
    assert.deepEqual(next.embeddedFrameAncestors, [liveOrigin]);
  });

  it('rewrites the seed loopback origin and keeps pathless embedded origins', () => {
    const config = {
      profileDomain: seed,
      continueUrlTemplate: `${seed}/checkout/confirm?checkoutId={checkoutId}`,
      embeddedAllowedOrigins: [seed],
      embeddedFrameAncestors: [seed],
      webhookUrlOverride: null,
    };
    const next = rewriteUcpConfig(config, [seed], livePublic, liveOrigin);
    assert.equal(next.profileDomain, livePublic);
    assert.equal(next.continueUrlTemplate, `${livePublic}/checkout/confirm?checkoutId={checkoutId}`);
    assert.deepEqual(next.embeddedAllowedOrigins, [liveOrigin]);
    assert.deepEqual(next.embeddedFrameAncestors, [liveOrigin]);
    assert.equal(next.webhookUrlOverride, null);
  });

  it('originOnly keeps scheme, host and port', () => {
    assert.equal(originOnly('https://assistant.example:8443/app'), 'https://assistant.example:8443');
    assert.equal(originOnly('http://127.0.0.1:4188'), 'http://127.0.0.1:4188');
    assert.equal(originOnly('not a url', 'https://fallback.example'), 'https://fallback.example');
  });
});
