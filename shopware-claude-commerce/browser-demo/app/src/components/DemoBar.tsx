// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import type { DemoState, DemoView } from '../engine/demo';

type Props = {
  state: DemoState;
  onView: (view: DemoView) => void;
  onOpenShop: (path: string) => void;
  onKey: () => void;
  onReset: () => void;
  paths: { home: string; cart: string; admin: string };
};

const TABS: { id: DemoView; label: string; testId: string }[] = [
  { id: 'shop', label: 'Storefront', testId: 'tab-shop' },
  { id: 'shopping', label: 'Shopping assistant', testId: 'tab-shopping' },
  { id: 'merchant', label: 'Merchant portal', testId: 'tab-merchant' },
];

export default function DemoBar({ state, onView, onOpenShop, onKey, onReset, paths }: Props) {
  const agentFor = (view: DemoView) => (view === 'shopping' ? state.agents.shopping : view === 'merchant' ? state.agents.merchant : 'ready');
  const busy = state.phpBusy > 0;
  const dotClass = state.shopError || state.hostError ? 'demo-dot--error' : state.shopReady ? 'demo-dot--ready' : '';
  const modeLabel = state.anthropic.mode === 'byok' ? 'Your key' : state.proxyStatus === 'ready' ? 'Local proxy' : state.proxyStatus === 'unknown' ? '…' : 'No key yet';

  return (
    <header className="demo-bar" data-testid="demo-bar">
      <span className="demo-bar__mark" aria-hidden="true">
        S
      </span>
      <span className="demo-bar__title">Shopware × Claude Commerce Agents</span>
      <nav className="demo-bar__tabs" role="tablist" aria-label="Demo views">
        {TABS.map((tab) => {
          const agent = agentFor(tab.id);
          const disabled = tab.id !== 'shop' && (agent === 'idle' || agent === 'loading' || agent === 'error');
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              className="demo-tab"
              aria-selected={state.view === tab.id}
              data-testid={tab.testId}
              disabled={disabled}
              title={agent === 'error' ? state.agentErrors[tab.id as 'shopping' | 'merchant'] : undefined}
              onClick={() => onView(tab.id)}
            >
              {tab.label}
              {tab.id !== 'shop' && agent === 'loading' ? ' …' : ''}
            </button>
          );
        })}
      </nav>
      {state.view === 'shop' ? (
        <span className="demo-bar__tabs" aria-label="Shopware pages">
          <button type="button" className="demo-tab" onClick={() => onOpenShop(paths.home)} data-testid="shop-home">
            Home
          </button>
          <button type="button" className="demo-tab" onClick={() => onOpenShop(paths.cart)} data-testid="shop-cart">
            Cart
          </button>
          <button type="button" className="demo-tab" onClick={() => onOpenShop(paths.admin)} data-testid="shop-admin">
            Admin
          </button>
        </span>
      ) : null}
      <span className="demo-bar__status" data-testid="demo-status" title={state.statusText}>
        <span className={`demo-dot ${dotClass}${busy ? ' demo-dot--busy' : ''}`} aria-hidden="true" />
        <span>
          {state.statusText}
          {state.phpRequests ? ` · ${state.phpRequests} PHP requests` : ''}
        </span>
      </span>
      <button type="button" className={`demo-pill${state.anthropic.mode === 'byok' ? ' demo-pill--accent' : ''}`} onClick={onKey} data-testid="key-mode">
        Claude: {modeLabel}
      </button>
      <button type="button" className="demo-pill" onClick={onReset} title="Wipe the in-browser database and reload" data-testid="reset-demo">
        Reset
      </button>
    </header>
  );
}
