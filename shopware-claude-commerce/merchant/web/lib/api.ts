// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi } from "web-shared";
import type {
  AlertsResponse,
  ChangeActionResponse,
  ChangesResponse,
  ChangeStatus,
  DashboardResponse,
  HealthResponse,
  InvoiceResponse,
  ListingDetailResponse,
  ListingsResponse,
  MailResponse,
  OrdersResponse,
  OverviewResponse,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8005";

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
 * The shared client plus the operator's timezone on every request (`X-Timezone`), so the
 * host's session clock is the operator's, not the server's (`shopware_common.clock`).
 */
class TimezoneAwareApi extends AgentApi {
  headers(json = false): Record<string, string> {
    const headers = super.headers(json);
    const zone = browserTimezone();
    if (zone) headers[TIMEZONE_HEADER] = zone;
    return headers;
  }
}

/** The shared client over the merchant host; every route below lives under `/api/merchant`. */
export const api = new TimezoneAwareApi(API_URL, "/api/merchant");

export const UNREACHABLE =
  "Couldn't reach the Shopware merchant API on port 8005. Start it with " +
  "`uvicorn merchant.api.main:app --port 8005` and try again.";

/** The portal route's maximum (`ORDERS_MAX_LIMIT` in merchant/api/portal.py). */
const ORDERS_LIMIT = "100";

/** Public; the page shows its `error` while the host has no Shopware credentials and no session can start. */
export function fetchHealth(): Promise<HealthResponse | null> {
  return api.get<HealthResponse>("/health");
}

export function fetchOverview(): Promise<OverviewResponse | null> {
  return api.get<OverviewResponse>("/overview");
}

/** The "This week" row: null until the host exposes `/dashboard`, and the page falls back to the snapshot. */
export function fetchDashboard(): Promise<DashboardResponse | null> {
  return api.get<DashboardResponse>("/dashboard");
}

export function fetchInvoice(): Promise<InvoiceResponse | null> {
  return api.get<InvoiceResponse>("/invoice");
}

/** Maximum upload size; the host enforces the same cap (`MAX_PDF_BYTES`). */
export const MAX_INVOICE_PDF_BYTES = 5 * 1024 * 1024;

/** Sends a supplier invoice PDF as the raw body; the host extracts and keeps its text. */
export async function uploadInvoice(file: File): Promise<InvoiceResponse> {
  try {
    const response = await fetch(`${api.base}/invoice`, {
      method: "POST",
      headers: { ...api.headers(), "Content-Type": "application/pdf" },
      body: file,
    });
    const body = await response.json();
    return response.ok ? body : { enabled: true, error: body.detail ?? `Upload failed (${response.status}).` };
  } catch {
    return { enabled: true, error: UNREACHABLE };
  }
}

/** The assistant's simulated outbox for this session, newest first. */
export function fetchMail(): Promise<MailResponse | null> {
  return api.get<MailResponse>("/mail");
}

export function fetchListings(query?: string): Promise<ListingsResponse | null> {
  return api.get<ListingsResponse>("/listings", query ? { query } : undefined);
}

export function fetchListingDetail(listingId: string): Promise<ListingDetailResponse | null> {
  return api.get<ListingDetailResponse>(`/listings/${encodeURIComponent(listingId)}`);
}

export function fetchAlerts(): Promise<AlertsResponse | null> {
  return api.get<AlertsResponse>("/alerts");
}

export function fetchOrders(): Promise<OrdersResponse | null> {
  return api.get<OrdersResponse>("/orders", { limit: ORDERS_LIMIT });
}

export function fetchChanges(status: ChangeStatus | "all"): Promise<ChangesResponse | null> {
  return api.get<ChangesResponse>("/changes", { status });
}

export type ChangeAction = "apply" | "discard";

/**
 * Approve or dismiss through the same gate the assistant's own tools use. Unlike the shared
 * `actOnChange`, the whole answer comes back so a held change can show the gate's `reason`.
 * Null when the request itself failed.
 */
export function actOnChange(changeId: string, action: ChangeAction): Promise<ChangeActionResponse | null> {
  return api.post<ChangeActionResponse>(`/changes/${encodeURIComponent(changeId)}/${action}`);
}
