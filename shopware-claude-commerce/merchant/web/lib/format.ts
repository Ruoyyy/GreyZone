// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * Shopware-specific formatting over web-shared's helpers. The shared `formatMoney` is
 * en-US/USD; this shop reports in EUR, so every amount the portal renders itself goes
 * through here ("1.234,56 €"). Percentages and dates keep the shared English forms.
 */

import { formatDayMonth, formatRate, plural, type RecordRowData, titleCase } from "web-shared";
import { ORDER_STATUS } from "./kinds";
import type { BusinessSnapshot, OverviewResponse, RecentOrder } from "./types";

const LOCALE = "de-DE";
export const DEFAULT_CURRENCY = "EUR";

const moneyFormatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(
  value: number,
  currency: string | null | undefined = DEFAULT_CURRENCY,
  options: { whole?: boolean } = {},
): string {
  const code = currency || DEFAULT_CURRENCY;
  const key = `${code}:${options.whole ? 0 : 2}`;
  let formatter = moneyFormatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, {
      style: "currency",
      currency: code,
      maximumFractionDigits: options.whole ? 0 : 2,
    });
    moneyFormatters.set(key, formatter);
  }
  return formatter.format(value);
}

const plain = new Intl.NumberFormat(LOCALE);

export function formatNumber(value: number): string {
  return plain.format(value);
}

/** Shopware categories arrive as names ("Home & Living") or slugs; slugs get title-cased. */
export function formatCategoryLabel(category: string): string {
  return /^[a-z0-9-]+$/.test(category) ? titleCase(category.replaceAll("-", "_")) : category;
}

/**
 * The greeting's name: the first name of "Avery Chen", the local part of an operator
 * configured as an email ("ops@example.com" → "Ops").
 */
export function firstName(operator: string | null | undefined): string | null {
  if (!operator) return null;
  const trimmed = operator.trim();
  if (!trimmed) return null;
  const local = trimmed.includes("@") ? trimmed.split("@")[0] : trimmed;
  const first = local.split(/[\s._-]+/).filter(Boolean)[0];
  return first ? first.charAt(0).toUpperCase() + first.slice(1) : null;
}

export function orderNumber(order: RecentOrder): string {
  return order.order_number || order.order_id;
}

export function orderItemCount(order: RecentOrder): number {
  return Array.isArray(order.items) ? order.items.reduce((sum, line) => sum + line.quantity, 0) : order.items;
}

export function orderStatusStyle(status: string): { label: string; tone: RecordRowData["status"]["tone"] } {
  return ORDER_STATUS[status] ?? { label: status.replaceAll("_", " "), tone: "muted" };
}

export function orderRows(orders: RecentOrder[], currency?: string | null): RecordRowData[] {
  return orders.map((order) => ({
    id: orderNumber(order),
    detail: [plural(orderItemCount(order), "item"), order.customer ?? ""].filter(Boolean).join(" · "),
    sub: `${formatDayMonth(order.placed_at)} · ${formatMoney(order.total, order.currency ?? currency)}`,
    status: orderStatusStyle(order.status),
  }));
}

/** "up 1.7%", "down 3.2%", "flat" from a signed change. */
export function describeChange(changePct: number | null | undefined): string | null {
  if (changePct == null) return null;
  if (Math.abs(changePct) < 0.05) return "flat";
  return `${changePct > 0 ? "up" : "down"} ${formatRate(Math.abs(changePct))}`;
}

/**
 * One line from the real snapshot: the sales move, then what needs the operator.
 * "Sales are up 1.7% on the week. 5 orders and 6 listings need you today."
 */
export function digestLine(data: OverviewResponse): string {
  const parts: string[] = [];
  const move = describeChange(data.snapshot.sales_change_pct);
  if (move) parts.push(move === "flat" ? "Sales are flat on the week." : `Sales are ${move} on the week.`);
  const orders = data.needs_attention.order_issues.length;
  const listings = data.needs_attention.inventory.length;
  const needs = [orders ? plural(orders, "order") : "", listings ? plural(listings, "listing") : ""].filter(Boolean);
  parts.push(needs.length ? `${needs.join(" and ")} need you today.` : "Nothing needs you today.");
  return parts.join(" ");
}

/** The snapshot's average order when the host does not send one. */
export function averageOrderValue(snapshot: BusinessSnapshot): number | null {
  if (snapshot.average_order_value != null) return snapshot.average_order_value;
  return snapshot.orders > 0 ? snapshot.sales / snapshot.orders : null;
}
