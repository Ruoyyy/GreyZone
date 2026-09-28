// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

import { AgentApi } from "web-shared";

import type { AuthStatus, Brand, CartPayload, MailResponse, Product, ProductDetails } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8004";

const TIMEZONE_HEADER = "X-Timezone";

/** The browser's IANA zone, when the runtime knows it (a server render does not). */
function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    return null;
  }
}

/**
 * The shared client plus the customer's timezone on every request (`X-Timezone`), so the
 * host's session clock is the customer's, not the server's (`shopware_common.clock`).
 */
class TimezoneAwareApi extends AgentApi {
  headers(json = false): Record<string, string> {
    const headers = super.headers(json);
    const zone = browserTimezone();
    if (zone) headers[TIMEZONE_HEADER] = zone;
    return headers;
  }
}

/** The shared client: `root` is the API's origin, the storefront routes live under `/api`. */
export const api = new TimezoneAwareApi(API_URL, "/api");

export const UNREACHABLE =
  "Couldn't reach the storefront host on port 8004. Start it with `uvicorn storefront.api.main:app --port 8004` and try again.";

const GRID_LIMIT = "24";

/** `GET /api/cart`: the shared cart shape plus Shopware's `checkout_url` and `cart_id`. */
export function fetchCart(): Promise<CartPayload | null> {
  return api.fetchCart<CartPayload>();
}

export async function fetchProducts(): Promise<Product[] | null> {
  const data = await api.get<{ products: Product[] }>("/products", { limit: GRID_LIMIT });
  return data?.products ?? null;
}

type ProductReviews = Pick<ProductDetails, "rating" | "review_count" | "review_highlights">;

/** The cached product plus its live Shopware reviews; a failed review read leaves the product as is. */
export async function fetchProduct(productId: string): Promise<ProductDetails | null> {
  const id = encodeURIComponent(productId);
  const [product, reviews] = await Promise.all([
    api.get<ProductDetails>(`/products/${id}`),
    api.get<ProductReviews>(`/reviews/${id}`),
  ]);
  return product && reviews ? { ...product, ...reviews } : product;
}

/** `POST /api/cart/add` answers `{ ok, cart, checkout_url, cart_id }`; the extras are folded into the cart. */
export async function addToCart(productId: string, quantity = 1): Promise<CartPayload | null> {
  const data = await api.post<{
    cart: CartPayload | null;
    checkout_url?: string | null;
    cart_id?: string | null;
  }>("/cart/add", { product_id: productId, quantity });
  if (!data?.cart) return null;
  return { ...data.cart, checkout_url: data.checkout_url, cart_id: data.cart_id };
}

/** Binds the session to a cart that exists already — the storefront's `cart` cookie value
 * (the Shopware context token) works as-is. Null when the shop doesn't know the id. */
export function attachCart(cartId: string): Promise<CartPayload | null> {
  return api.post<CartPayload>("/cart/attach", { cart_id: cartId });
}

/** Grid catalog → shopping session (search + provenance) so chat can name visible products. */
export function syncCatalog(): Promise<{ ok: boolean; products: number } | null> {
  return api.post<{ ok: boolean; products: number }>("/session/sync-catalog", {});
}

export function fetchBrand(): Promise<Brand | null> {
  return api.get<Brand>("/brand");
}

/** `GET /api/mail`: the assistant's simulated outbox for this session, newest first. */
export function fetchMail(): Promise<MailResponse | null> {
  return api.get<MailResponse>("/mail");
}

/** "gone" when the host no longer knows this session (a restart, an expired Shopware sign-in);
 * null when the host is unreachable, so an outage never reads as a lost session. */
export async function checkSession(): Promise<AuthStatus | "gone" | null> {
  try {
    const response = await fetch(`${api.base}/auth/status`, { headers: api.headers() });
    if (response.status === 401) return "gone";
    return response.ok ? ((await response.json()) as AuthStatus) : null;
  } catch {
    return null;
  }
}

export function fetchAuthStatus(): Promise<AuthStatus | null> {
  return api.get<AuthStatus>("/auth/status");
}

export function signOut(): Promise<(AuthStatus & { session_id?: string }) | null> {
  return api.post<AuthStatus & { session_id?: string }>("/auth/signout", {});
}

/** Shopware validates the credentials; preserve a useful error for the sign-in form. */
export async function signIn(email: string, password: string): Promise<AuthStatus> {
  let response: Response;
  try {
    response = await fetch(`${api.base}/auth/shopware/login`, {
      method: "POST",
      headers: api.headers(true),
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new Error("Storefront API is unreachable. Start the API on port 8004 and try again.");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `Sign-in failed (HTTP ${response.status}).`);
  }
  return response.json() as Promise<AuthStatus>;
}
