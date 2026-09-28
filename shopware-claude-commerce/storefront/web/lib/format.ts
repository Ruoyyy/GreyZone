// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Shopware-specific formatting over web-shared's helpers. The shared `formatMoney` is
 * en-US/USD; this shop sells in EUR to a German locale, so every price the app renders
 * itself goes through here ("1.234,56 €").
 */

const LOCALE = "de-DE";
const DEFAULT_CURRENCY = "EUR";

const moneyFormatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(value: number, currency: string | null | undefined = DEFAULT_CURRENCY): string {
  const code = currency || DEFAULT_CURRENCY;
  let formatter = moneyFormatters.get(code);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, { style: "currency", currency: code, maximumFractionDigits: 2 });
    moneyFormatters.set(code, formatter);
  }
  return formatter.format(value);
}

const plain = new Intl.NumberFormat(LOCALE);

export function formatNumber(value: number): string {
  return plain.format(value);
}

/**
 * The checkout handoff comes from the storefront API (never from the model); a plain
 * http(s) URL is the only thing the "Checkout in Shopware" link may point at.
 */
export function isSafeCheckoutUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}
