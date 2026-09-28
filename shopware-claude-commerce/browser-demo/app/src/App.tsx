// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import { lazy, Suspense, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { ADMIN_PATH, STOREFRONT_CART_PATH, STOREFRONT_HOME } from './engine/demo-config';
import { type DemoState, type DemoView, getDemo } from './engine/demo';
import BootScreen from './components/BootScreen';
import DemoBar from './components/DemoBar';
import KeyDialog from './components/KeyDialog';

// The vendored web UIs are heavy; load them when their view first opens.
const ShoppingView = lazy(() => import('./views/ShoppingView'));
const MerchantView = lazy(() => import('./views/MerchantView'));

export function useDemo(): DemoState {
  const demo = getDemo();
  return useSyncExternalStore(
    (listener) => demo.subscribe(listener),
    () => demo.state,
    () => demo.state
  );
}

export default function App() {
  const demo = getDemo();
  const state = useDemo();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [keyDialogOpen, setKeyDialogOpen] = useState(false);
  const [openedViews, setOpenedViews] = useState<Set<DemoView>>(() => new Set());

  useEffect(() => {
    demo.attachFrame(frameRef.current);
    demo.start();
    return () => demo.attachFrame(null);
  }, [demo]);

  useEffect(() => {
    if (state.view !== 'shop') setOpenedViews((views) => (views.has(state.view) ? views : new Set(views).add(state.view)));
  }, [state.view]);

  const booting = !state.shopReady && !state.shopError;

  return (
    <>
      <DemoBar
        state={state}
        onView={(view) => demo.setView(view)}
        onOpenShop={(path) => {
          demo.setView('shop');
          demo.openInFrame(path);
        }}
        onKey={() => setKeyDialogOpen(true)}
        onReset={() => void demo.resetDemo()}
        paths={{ home: STOREFRONT_HOME, cart: STOREFRONT_CART_PATH, admin: ADMIN_PATH }}
      />

      <iframe
        ref={frameRef}
        title="Shopware (in-browser)"
        className={`demo-frame${state.view === 'shop' ? '' : ' demo-hidden'}`}
        onLoad={() => demo.frameLoaded()}
        allow="clipboard-write"
      />

      {booting || state.shopError ? <BootScreen state={state} /> : null}

      {openedViews.has('shopping') ? (
        <div className={`demo-view${state.view === 'shopping' ? '' : ' demo-hidden'}`} data-demo-view="shopping">
          <Suspense fallback={<ViewLoading label="Loading the storefront UI…" />}>
            <ShoppingView />
          </Suspense>
        </div>
      ) : null}
      {openedViews.has('merchant') ? (
        <div className={`demo-view${state.view === 'merchant' ? '' : ' demo-hidden'}`} data-demo-view="merchant">
          <Suspense fallback={<ViewLoading label="Loading the merchant portal…" />}>
            <MerchantView />
          </Suspense>
        </div>
      ) : null}

      {keyDialogOpen ? <KeyDialog state={state} onClose={() => setKeyDialogOpen(false)} onSave={(access) => demo.setAnthropic(access)} /> : null}
      {state.toast ? (
        <div className="demo-toast" role="status">
          {state.toast}
        </div>
      ) : null}
    </>
  );
}

function ViewLoading({ label }: { label: string }) {
  return (
    <div style={{ display: 'grid', placeItems: 'center', height: '100%', color: 'var(--demo-ink-soft)', fontSize: 14 }}>{label}</div>
  );
}
