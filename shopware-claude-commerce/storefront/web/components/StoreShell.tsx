// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { type AgentEvent, type AgentTurn, Inspector, type Order, useAgentTurn } from "web-shared";
import { addToCart, api, attachCart, fetchBrand, fetchCart, fetchMail, UNREACHABLE } from "@/lib/api";
import { useStoreSession } from "@/lib/session";
import type { Brand, CartPayload, SentMail } from "@/lib/types";
import Assistant from "./Assistant";
import CartDrawer from "./CartDrawer";
import Header from "./Header";
import MailDrawer from "./MailDrawer";
import OrdersDrawer from "./OrdersDrawer";

interface StoreContextValue {
  sessionId: string | null;
  signedIn: boolean;
  signIn: (email: string, password: string) => Promise<boolean>;
  signOut: () => Promise<void>;
  brand: Brand | null;
  cart: CartPayload | null;
  chat: AgentTurn;
  /** Adds via the host's direct-add route; false means the server refused. */
  addProduct: (productId: string) => Promise<boolean>;
  openCart: () => void;
  /** Opens the rail and, when given a message, sends it. */
  askAssistant: (message?: string) => void;
}

const StoreContext = createContext<StoreContextValue | null>(null);

// The cart id is kept here so a guest session can pick up its cart after navigation.
const CART_ID_KEY = "shopware-storefront-cart-id";

export function useStore(): StoreContextValue {
  const value = useContext(StoreContext);
  if (!value) throw new Error("useStore must be used inside StoreShell");
  return value;
}

export default function StoreShell({ children }: { children: ReactNode }) {
  const { sessionId, signedIn, signIn, signOut, verify } = useStoreSession();
  const [brand, setBrand] = useState<Brand | null>(null);
  const [cart, setCart] = useState<CartPayload | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  const [ordersOpen, setOrdersOpen] = useState(false);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [ordersFailed, setOrdersFailed] = useState(false);
  const [mailOpen, setMailOpen] = useState(false);
  const [mail, setMail] = useState<SentMail[] | null>(null);
  const [mailFailed, setMailFailed] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);

  // The brand's primary color pair drives the theme; the CSS defaults hold until it
  // lands — and for good, when the host's contrast guard dropped the pair.
  useEffect(() => {
    void fetchBrand().then((value) => {
      if (!value) return;
      setBrand(value);
      if (!value.colors) return;
      const root = document.documentElement;
      root.style.setProperty("--brand", value.colors.background);
      root.style.setProperty("--brand-contrast", value.colors.foreground);
    });
  }, []);

  const landCart = useCallback((next: CartPayload) => {
    setCart(next);
    if (next.cart_id) window.localStorage.setItem(CART_ID_KEY, next.cart_id);
    else window.localStorage.removeItem(CART_ID_KEY);
  }, []);

  const refetchCart = useCallback(async () => {
    const next = await fetchCart();
    if (next) landCart(next);
  }, [landCart]);

  // The cart_update event omits checkout_url, so the event's cart lands immediately and a
  // refetch of /api/cart fills the extras in.
  const onEvent = useCallback(
    (event: AgentEvent) => {
      if (event.type !== "cart_update") return;
      setCart((current) => ({
        ...(event.data.cart as CartPayload),
        checkout_url: current?.checkout_url,
        cart_id: current?.cart_id,
      }));
      void refetchCart();
    },
    [refetchCart],
  );

  // A turn that failed because the host lost the session ends with a reload, not a stale "Signed in".
  const chat = useAgentTurn(api, { sessionId, unreachable: UNREACHABLE, onEvent, onTurnEnd: () => void verify() });

  // The host may have restarted, or the Shopware sign-in expired, while this tab was in the background.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void verify();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [verify]);

  // A new session joins the cart in progress: `?cart=` from a page that holds the buyer's
  // cart, else the id remembered from the last session. Without one it starts empty.
  useEffect(() => {
    if (!sessionId) return;
    const incoming =
      new URLSearchParams(window.location.search).get("cart") ??
      window.localStorage.getItem(CART_ID_KEY);
    if (!incoming) {
      void refetchCart();
      return;
    }
    void (async () => {
      const attached = await attachCart(incoming);
      if (attached) landCart(attached);
      else {
        window.localStorage.removeItem(CART_ID_KEY);
        await refetchCart();
      }
      const params = new URLSearchParams(window.location.search);
      if (!params.has("cart")) return;
      params.delete("cart");
      const query = params.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
    })();
  }, [sessionId, refetchCart, landCart]);

  // The rail is part of the default layout on wide screens; narrow screens open it on demand.
  useEffect(() => {
    if (window.matchMedia("(min-width: 1024px)").matches) setAssistantOpen(true);
  }, []);

  useEffect(() => {
    if (signedIn) void refetchCart();
  }, [signedIn, refetchCart]);

  // The chat transcript, activity trace, orders, and memory all belong to the account, so
  // a reload drops every piece of it; the fresh guest session is already in sessionStorage.
  const signOutAndClear = useCallback(async () => {
    window.localStorage.removeItem(CART_ID_KEY);
    setCart(null);
    await signOut();
    window.location.reload();
  }, [signOut]);

  // Refetched on every open so an order the assistant just placed shows up.
  const openOrders = useCallback(async () => {
    setOrdersOpen(true);
    setOrdersFailed(false);
    const next = await api.fetchOrders();
    if (next) setOrders(next);
    else setOrdersFailed(true);
  }, []);

  // Refetched on every open so a mail the assistant just sent shows up.
  const openMail = useCallback(async () => {
    setMailOpen(true);
    setMailFailed(false);
    const next = await fetchMail();
    if (next) setMail(next.messages);
    else setMailFailed(true);
  }, []);

  const addProduct = useCallback(
    async (productId: string) => {
      const next = await addToCart(productId);
      if (!next) return false;
      landCart(next);
      return true;
    },
    [landCart],
  );

  const askAssistant = useCallback(
    (message?: string) => {
      setAssistantOpen(true);
      if (message) void chat.send(message);
    },
    [chat],
  );

  const value: StoreContextValue = {
    sessionId,
    signedIn,
    signIn,
    signOut: signOutAndClear,
    brand,
    cart,
    chat,
    addProduct,
    openCart: () => setCartOpen(true),
    askAssistant,
  };

  return (
    <StoreContext.Provider value={value}>
      <div className="flex h-dvh flex-col">
        <Header
          brand={brand}
          sessionId={sessionId}
          signedIn={signedIn}
          onSignIn={signIn}
          onSignOut={() => void signOutAndClear()}
          cartCount={cart?.item_count ?? 0}
          onOpenCart={() => setCartOpen(true)}
          onOpenOrders={() => void openOrders()}
          onOpenMail={() => void openMail()}
          assistantOpen={assistantOpen}
          onToggleAssistant={() => setAssistantOpen((open) => !open)}
          streaming={chat.streaming}
          newMemoryCount={chat.newMemoryKeys.size}
          onOpenActivity={() => setActivityOpen(true)}
        />
        <main className="flex min-h-0 flex-1">
          <div className="panel-scroll min-w-0 flex-1 overflow-y-auto">{children}</div>
          <Assistant
            open={assistantOpen}
            chat={chat}
            onClose={() => setAssistantOpen(false)}
            onAdd={addProduct}
            checkoutUrl={cart?.checkout_url}
          />
        </main>
        <CartDrawer
          open={cartOpen}
          cart={cart}
          onClose={() => setCartOpen(false)}
          onAction={(message) => {
            setCartOpen(false);
            askAssistant(message);
          }}
        />
        <OrdersDrawer
          open={ordersOpen}
          orders={orders}
          failed={ordersFailed}
          onClose={() => setOrdersOpen(false)}
        />
        <MailDrawer
          open={mailOpen}
          messages={mail}
          failed={mailFailed}
          onClose={() => setMailOpen(false)}
        />
        {activityOpen ? (
          <Inspector
            turnCount={chat.turnCount}
            streaming={chat.streaming}
            trace={chat.trace}
            memory={chat.memory}
            newMemoryKeys={chat.newMemoryKeys}
            onClose={() => setActivityOpen(false)}
          />
        ) : null}
      </div>
    </StoreContext.Provider>
  );
}
