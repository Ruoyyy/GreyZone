// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { formatDate, type Order } from "web-shared";
import { formatMoney } from "@/lib/format";

/** The signed-in customer's Shopware orders, newest first; read-only, like the cart drawer's shell. */
export default function OrdersDrawer({
  open,
  orders,
  failed,
  onClose,
}: {
  open: boolean;
  /** Null while loading. */
  orders: Order[] | null;
  failed: boolean;
  onClose: () => void;
}) {
  return (
    <div
      className={`fixed inset-0 z-50 ${open ? "" : "pointer-events-none"}`}
      aria-hidden={!open}
      inert={!open}
    >
      <div
        onClick={onClose}
        aria-hidden
        className={`absolute inset-0 bg-(--ink)/30 transition-opacity duration-300 ${open ? "opacity-100" : "opacity-0"}`}
      />
      <aside
        className={`absolute inset-y-0 right-0 flex w-[min(92vw,380px)] flex-col border-l border-(--line) bg-(--card) transition-transform duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-(--line) px-4 py-3">
          <h2 className="text-sm font-bold text-(--ink)">
            Orders{orders ? ` · ${orders.length}` : ""}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close orders"
            className="rounded-md px-2 py-0.5 text-lg leading-none text-(--ink-soft) hover:text-(--ink)"
          >
            ×
          </button>
        </div>

        <div className="panel-scroll flex-1 overflow-y-auto px-4 py-3">
          {failed ? (
            <p role="alert" className="mt-8 text-center text-[15px] text-(--danger)">Couldn&apos;t load your orders. Try again.</p>
          ) : orders === null ? (
            <p className="mt-8 text-center text-[15px] text-(--ink-soft)/80">Loading orders…</p>
          ) : orders.length === 0 ? (
            <p className="mt-8 text-center text-[15px] text-(--ink-soft)/80">You have no orders yet.</p>
          ) : (
            <ul className="divide-y divide-(--line)">
              {orders.map((order) => (
                <li key={order.order_id} className="ac-reveal py-3 first:pt-0">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-(--ink)">Order #{order.order_id}</div>
                      <div className="text-[11px] text-(--ink-soft)">{formatDate(order.placed_at)}</div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="text-sm font-bold text-(--ink)">{formatMoney(order.total, order.currency)}</div>
                      <span className="rounded-full bg-(--accent-soft) px-1.5 py-0.5 text-[11px] font-semibold capitalize text-(--ink)">
                        {order.status.replaceAll("_", " ")}
                      </span>
                    </div>
                  </div>
                  <ul className="mt-2 space-y-0.5">
                    {order.items.map((item) => (
                      <li key={item.product_id} className="flex justify-between gap-2 text-[12px] text-(--ink-soft)">
                        <span className="min-w-0 truncate" title={item.title}>
                          {item.quantity} × {item.title}
                        </span>
                        <span className="shrink-0">{formatMoney(item.price * item.quantity, order.currency)}</span>
                      </li>
                    ))}
                  </ul>
                  {order.estimated_delivery ? (
                    <div className="mt-1.5 text-[11px] text-(--ink-soft)">
                      Estimated delivery {formatDate(order.estimated_delivery)}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}
