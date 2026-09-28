// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Minimal `next/navigation` for the vendored Next.js pages: an in-memory router scoped to
 * the view that mounts <DemoRouter>. Routes never touch the real URL — the shell's URL is
 * owned by the playground (Shopware paths open in the storefront frame).
 */
import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from 'react';

export type DemoRoute = { pathname: string; params: Record<string, string> };

type RouterContextValue = {
  route: DemoRoute;
  push: (href: string) => void;
  replace: (href: string) => void;
  back: () => void;
};

const RouterContext = createContext<RouterContextValue | null>(null);

const PRODUCT_ROUTE = /^\/products\/([^/?#]+)/;

function parse(href: string): DemoRoute {
  const pathname = href.split(/[?#]/)[0] || '/';
  const product = PRODUCT_ROUTE.exec(pathname);
  return { pathname, params: product ? { id: decodeURIComponent(product[1]) } : {} };
}

export function DemoRouter({ children, initialPath = '/' }: { children: ReactNode; initialPath?: string }) {
  const [history, setHistory] = useState<DemoRoute[]>([parse(initialPath)]);
  const route = history[history.length - 1];
  const push = useCallback((href: string) => setHistory((stack) => [...stack, parse(href)]), []);
  const replace = useCallback((href: string) => setHistory((stack) => [...stack.slice(0, -1), parse(href)]), []);
  const back = useCallback(() => setHistory((stack) => (stack.length > 1 ? stack.slice(0, -1) : stack)), []);
  const value = useMemo(() => ({ route, push, replace, back }), [route, push, replace, back]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

function useDemoRouterContext(): RouterContextValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error('next/navigation shim used outside <DemoRouter>');
  return value;
}

export function useRouter() {
  const { push, replace, back } = useDemoRouterContext();
  return useMemo(() => ({ push, replace, back, forward: () => {}, refresh: () => {}, prefetch: async () => {} }), [push, replace, back]);
}

export function usePathname(): string {
  return useDemoRouterContext().route.pathname;
}

export function useParams<T extends Record<string, string> = Record<string, string>>(): T {
  return useDemoRouterContext().route.params as T;
}

export function useSearchParams(): URLSearchParams {
  return new URLSearchParams();
}

/** The current route, for the view that renders the page components. */
export function useDemoRoute(): DemoRoute {
  return useDemoRouterContext().route;
}
