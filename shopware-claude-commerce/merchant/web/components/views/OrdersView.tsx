// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { AttentionList, AttentionRow, formatDayMonth, Icon, Notice, PageHeader, Panel, Pill, plural, QuotedAsData, Skeleton, useResource } from "web-shared";
import { fetchAlerts, fetchOrders } from "@/lib/api";
import { formatMoney, orderItemCount, orderNumber, orderStatusStyle } from "@/lib/format";
import { ISSUE_KINDS } from "@/lib/kinds";
import type { OrderIssue, RecentOrder } from "@/lib/types";

function IssueRow({ issue, onAskAssistant }: { issue: OrderIssue; onAskAssistant: (text: string) => void }) {
  const style = ISSUE_KINDS[issue.kind] ?? ISSUE_KINDS.damaged;
  return (
    <AttentionRow
      icon={style.icon}
      tone={style.tone}
      title={issue.summary}
      meta={[style.label, `Order ${issue.order_id}`, issue.listing_id ?? "", issue.opened_at ? `opened ${formatDayMonth(issue.opened_at)}` : ""].filter(Boolean).join(" · ")}
      note={
        issue.buyer_message_excerpt ? (
          <div className="mt-1 rounded-[10px] bg-(--ground) px-3 py-2">
            <blockquote className="text-[13px] leading-snug text-(--ink-2)">&ldquo;{issue.buyer_message_excerpt}&rdquo;</blockquote>
            {/* A buyer's text can carry instructions, so the note sits beside the quote. */}
            <QuotedAsData subject="Buyer message" className="mt-1.5" />
          </div>
        ) : null
      }
      action={{
        label: issue.kind === "buyer_message" || issue.buyer_message_excerpt ? "Draft reply" : "Ask",
        onClick: () => onAskAssistant(`What are my options for order ${issue.order_id}? ${issue.summary}.`),
      }}
    />
  );
}

/** Payment states that mean money went back; the order's own state does not change. */
const REFUND_STATES = new Set(["refunded", "refunded_partially"]);

/** One order as Shopware lists it: number, lines, buyer, date, total, and its state — followed by the refund, when the payment went back, as the later state. */
function OrderRow({ order, currency, onAskAssistant }: { order: RecentOrder; currency?: string | null; onAskAssistant: (text: string) => void }) {
  const status = orderStatusStyle(order.status);
  const refund = order.payment_status && REFUND_STATES.has(order.payment_status) ? orderStatusStyle(order.payment_status) : null;
  const number = orderNumber(order);
  const lines = Array.isArray(order.items) ? order.items : [];
  return (
    <li className="flex items-center gap-3 px-[18px] py-2.5">
      <div className="min-w-0 flex-1 tabular-nums">
        <div className="text-[13px] font-semibold text-(--ink)">
          {number}
          <span className="ml-1.5 text-[12.5px] font-normal text-(--ink-soft)">
            · {plural(orderItemCount(order), "item")}
            {order.customer ? ` · ${order.customer}` : ""}
          </span>
        </div>
        <div className="text-[12px] text-(--ink-soft)">
          {formatDayMonth(order.placed_at)} · {formatMoney(order.total, order.currency ?? currency)}
          {lines.length ? <span className="ml-1.5 truncate">· {lines.map((line) => `${line.quantity}× ${line.title}`).join(", ")}</span> : null}
        </div>
        {order.issue ? <div className="mt-0.5 text-[12px] font-medium text-(--warn)">{order.issue.summary}</div> : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Pill tone={status.tone} dot>
          {status.label}
        </Pill>
        {refund ? (
          <>
            <Icon name="arrow-right" size={12} aria-hidden className="text-(--ink-faint)" />
            <Pill tone={refund.tone} dot title={`${status.label}, then ${refund.label.toLowerCase()}`}>
              {refund.label}
            </Pill>
          </>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onAskAssistant(`What's the status of order ${number}, and is anything needed from me?`)}
        className="text-[12.5px] font-semibold text-(--ink-soft) hover:text-(--ink) hover:underline"
      >
        Ask
      </button>
    </li>
  );
}

export default function OrdersView({
  refreshKey,
  recentOrders,
  currency,
  onAskAssistant,
}: {
  refreshKey: number;
  /** The overview's recent orders, the list until `/orders` answers. */
  recentOrders: RecentOrder[] | null;
  currency?: string | null;
  onAskAssistant: (text: string) => void;
}) {
  const { data, failed } = useResource(fetchAlerts, [refreshKey]);
  const { data: orderData, failed: ordersFailed } = useResource(fetchOrders, [refreshKey]);
  const issues = data?.order_issues ?? [];
  const orders = orderData?.orders ?? (ordersFailed ? recentOrders : null);

  return (
    <div className="ac-reveal flex flex-col gap-4">
      <PageHeader title="Orders" subtitle={data ? (issues.length ? plural(issues.length, "open issue") : "No open issues") : undefined} />
      {failed && !data ? (
        <Notice>The merchant API isn&apos;t reachable, so order issues can&apos;t load.</Notice>
      ) : !data ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Skeleton className="h-96" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Panel title="Open issues" subtitle={issues.length ? String(issues.length) : undefined}>
            {issues.length === 0 ? (
              <p className="px-[18px] pb-4 text-[13.5px] text-(--ink-soft)">No open order issues.</p>
            ) : (
              <AttentionList>
                {issues.map((issue) => (
                  <IssueRow key={issue.issue_id} issue={issue} onAskAssistant={onAskAssistant} />
                ))}
              </AttentionList>
            )}
          </Panel>
          <Panel title="Recent orders" subtitle={orders ? plural(orders.length, "order") : undefined}>
            {!orders ? (
              <Skeleton className="mx-[18px] mb-4 h-40" />
            ) : orders.length === 0 ? (
              <p className="px-[18px] pb-4 text-[13px] text-(--ink-soft)">No recent orders to show.</p>
            ) : (
              <ul className="divide-y divide-(--line) pb-2">
                {orders.map((order) => (
                  <OrderRow key={order.order_id} order={order} currency={currency} onAskAssistant={onAskAssistant} />
                ))}
              </ul>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
