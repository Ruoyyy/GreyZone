// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import {
  ApprovalsBanner,
  askWhy,
  AttentionList,
  AttentionRow,
  Button,
  coverLabel,
  formatComparisonLabel,
  formatDayMonth,
  formatPeriodLabel,
  formatRate,
  greeting,
  Icon,
  KindIcon,
  Notice,
  optionValuesLabel,
  PageHeader,
  Panel,
  Pill,
  QueueOverflow,
  ratioChangePct,
  RecentChanges,
  RecordList,
  Segmented,
  Skeleton,
  StatStrip,
  StatTile,
  ViewLink,
} from "web-shared";
import { averageOrderValue, digestLine, firstName, formatMoney, formatNumber, orderRows } from "@/lib/format";
import { INVENTORY_KINDS, ISSUE_KINDS } from "@/lib/kinds";
import type { DashboardKpi, DashboardResponse, HomeInsight, InventoryAlert, MetricPoint, OrderIssue, OverviewResponse } from "@/lib/types";

type Filter = "all" | "orders" | "stock" | "slow";
type Row = { kind: "issue"; issue: OrderIssue } | { kind: "inventory"; alert: InventoryAlert };

const ROW_CAP = 6;
const RECENT_ORDERS_CAP = 4;
/** Sales from this figure up render without cents in the KPI strip. */
const WHOLE_FROM = 1000;
const NOT_AVAILABLE = "n/a";

function values(points?: MetricPoint[] | null): number[] | undefined {
  return points?.map((point) => point.value);
}

function rows(data: OverviewResponse, filter: Filter): Row[] {
  const { inventory, order_issues } = data.needs_attention;
  const issues = order_issues.map((issue) => ({ kind: "issue" as const, issue }));
  const lowStock = inventory
    .filter((alert) => alert.kind === "low_stock")
    .sort((a, b) => (a.days_of_cover ?? Infinity) - (b.days_of_cover ?? Infinity))
    .map((alert) => ({ kind: "inventory" as const, alert }));
  const slow = inventory.filter((alert) => alert.kind === "slow_mover").map((alert) => ({ kind: "inventory" as const, alert }));
  if (filter === "orders") return issues;
  if (filter === "stock") return lowStock;
  if (filter === "slow") return slow;
  // The most urgent stock alert leads; it is the one a seller acts on first.
  return [...lowStock.slice(0, 1), ...issues, ...lowStock.slice(1), ...slow];
}

/** The clock is read after mount, so the prerendered page never disagrees with the browser's day. */
function useNow(): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);
  return now;
}

function IssueRow({ issue, onAskAssistant }: { issue: OrderIssue; onAskAssistant: (text: string) => void }) {
  const style = ISSUE_KINDS[issue.kind] ?? ISSUE_KINDS.damaged;
  return (
    <AttentionRow
      icon={style.icon}
      tone={style.tone}
      title={issue.summary}
      meta={[style.label, `Order ${issue.order_id}`, issue.opened_at ? `opened ${formatDayMonth(issue.opened_at)}` : ""].filter(Boolean).join(" · ")}
      action={{
        label: issue.kind === "buyer_message" || issue.buyer_message_excerpt ? "Draft reply" : "Ask",
        onClick: () => onAskAssistant(`What are my options for order ${issue.order_id}? ${issue.summary}.`),
      }}
    />
  );
}

function InventoryRow({ alert, onAskAssistant }: { alert: InventoryAlert; onAskAssistant: (text: string) => void }) {
  const style = INVENTORY_KINDS[alert.kind];
  const soldOut = alert.kind === "low_stock" && alert.stock === 0;
  const low = alert.kind === "low_stock";
  const chosen = optionValuesLabel(alert);
  const name = chosen ? `${alert.title} · ${chosen}` : alert.title;
  const ref = `${name} (${alert.listing_id})`;
  return (
    <AttentionRow
      icon={style.icon}
      tone={soldOut ? "danger" : style.tone}
      title={name}
      meta={
        <>
          <span className={soldOut ? "font-semibold text-(--danger)" : low ? "font-semibold text-(--warn)" : ""}>
            {soldOut ? "Sold out" : `${formatNumber(alert.stock)} in stock`}
          </span>
          {[
            "",
            alert.days_of_cover != null && !soldOut ? coverLabel(alert.days_of_cover) : "",
            alert.sales_last_30d != null ? `${formatNumber(alert.sales_last_30d)} sold in 30 days` : "",
            alert.listing_id,
            soldOut && alert.storefront_visible === false ? "hidden from the storefront" : "",
          ]
            .filter((part, index) => index === 0 || part)
            .join(" · ")}
        </>
      }
      note={
        low && alert.stock > 0 && alert.storefront_visible ? (
          <Pill tone="warn" dot>
            Storefront shows “Only {formatNumber(alert.stock)} left”
          </Pill>
        ) : null
      }
      action={{
        label: low ? "Draft restock" : "Plan markdown",
        onClick: () => onAskAssistant(low ? `Draft a restock plan for ${ref}.` : `Plan a markdown for ${ref}.`),
      }}
    />
  );
}

/** What the assistant's briefing surfaced; before the first briefing, the way to start one. */
function Insights({
  insights,
  briefing,
  onAskAssistant,
  onStartBriefing,
}: {
  insights: HomeInsight[];
  briefing: boolean;
  onAskAssistant: (text: string) => void;
  onStartBriefing: () => void;
}) {
  return (
    <Panel title="From the assistant" icon={<KindIcon icon="spark" tone="accent" size={24} />}>
      {insights.length === 0 ? (
        <div className="px-[18px] pb-4 pt-1">
          <p className="text-[13px] leading-snug text-(--ink-soft)">
            {briefing ? "Reading the store…" : "No briefing yet this session. The assistant reads stock, orders, and sales and lists what needs you."}
          </p>
          <Button variant="secondary" size="sm" icon="spark" className="mt-2.5" onClick={onStartBriefing} disabled={briefing}>
            {briefing ? "Briefing…" : "Start the morning briefing"}
          </Button>
        </div>
      ) : (
        <ul className="divide-y divide-(--line)">
          {insights.map((insight) => (
            <li key={insight.insight_id} className="px-[18px] py-2.5">
              <div className="text-[13px] font-medium leading-snug text-(--ink)">{insight.headline}</div>
              {insight.detail ? <div className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-(--ink-soft)">{insight.detail}</div> : null}
              <button
                type="button"
                onClick={() => onAskAssistant(insight.prompt)}
                className="mt-1.5 inline-flex items-center gap-1 text-[12.5px] font-semibold text-(--accent-ink) hover:underline"
              >
                Ask <Icon name="arrow-right" size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** A KPI Shopware has no figure for: "n/a" and the host's note, never a made-up number. */
function UnavailableTile({ label, note }: { label: string; note?: string | null }) {
  return (
    <div className="block w-full px-[18px] pb-3.5 pt-4 text-left">
      <div className="text-[12.5px] font-medium whitespace-nowrap text-(--ink-soft)">{label}</div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="text-[26px] font-semibold leading-none tracking-[-0.02em] tabular-nums text-(--ink-faint)">{NOT_AVAILABLE}</span>
      </div>
      {note ? <p className="mt-2 text-[11.5px] leading-snug text-(--ink-soft)">{note}</p> : null}
    </div>
  );
}

function kpiTile(
  label: string,
  kpi: DashboardKpi | undefined,
  format: (value: number) => string,
  comparison: string,
  onAskAssistant: (text: string) => void,
): ReactNode {
  if (!kpi || kpi.value == null) return <UnavailableTile key={label} label={label} note={kpi?.note} />;
  return (
    <StatTile
      key={label}
      label={label}
      value={format(kpi.value)}
      changePct={kpi.change_pct}
      points={values(kpi.points)}
      prior={values(kpi.prior_points)}
      onClick={() => onAskAssistant(askWhy(label, kpi.change_pct, comparison))}
      ariaLabel={`${label}: ask the assistant why`}
    />
  );
}

/** "This week" from `/dashboard`; the snapshot on `/overview` stands in until that route answers. */
function ThisWeek({
  data,
  dashboard,
  dashboardFailed,
  onAskAssistant,
}: {
  data: OverviewResponse;
  dashboard: DashboardResponse | null;
  dashboardFailed: boolean;
  onAskAssistant: (text: string) => void;
}) {
  const currency = data.shop?.currency ?? data.snapshot.currency;
  const money = (unit?: string | null, whole = false) => (value: number) => formatMoney(value, unit ?? currency, { whole: whole && value >= WHOLE_FROM });

  if (dashboard) {
    const against = dashboard.period.against ?? "";
    const comparison = against.replace(/^the\s+/i, "");
    return (
      <Panel title="This week" subtitle={`${dashboard.period.label}${against ? ` · against ${against}` : ""}`} bodyClassName="pb-1">
        <StatStrip>
          {kpiTile("Sales", dashboard.kpis.sales, money(dashboard.kpis.sales?.unit, true), comparison, onAskAssistant)}
          {kpiTile("Orders", dashboard.kpis.orders, formatNumber, comparison, onAskAssistant)}
          {kpiTile("Conversion", dashboard.kpis.conversion, formatRate, comparison, onAskAssistant)}
          {kpiTile("Average order", dashboard.kpis.average_order, money(dashboard.kpis.average_order?.unit), comparison, onAskAssistant)}
        </StatStrip>
      </Panel>
    );
  }
  if (!dashboardFailed) {
    return (
      <Panel title="This week" bodyClassName="pb-1">
        <Skeleton className="mx-[18px] mb-3 h-[108px]" />
      </Panel>
    );
  }

  const { snapshot } = data;
  const comparison = formatComparisonLabel(snapshot.period, snapshot.compare_to);
  const aov = averageOrderValue(snapshot);
  const aovChangePct = ratioChangePct(snapshot.sales_change_pct, snapshot.orders_change_pct);
  const asKpi = (value: number | null | undefined, change_pct: number | null | undefined, series?: string): DashboardKpi => ({
    value: value ?? null,
    change_pct,
    points: series ? data.trends?.[series] : undefined,
    prior_points: series ? data.trends_prior?.[series] : undefined,
    note: snapshot.note,
  });
  return (
    <Panel title="This week" subtitle={`${formatPeriodLabel(snapshot.period)}${comparison ? ` · against the ${comparison}` : ""}`} bodyClassName="pb-1">
      <StatStrip>
        {kpiTile("Sales", asKpi(snapshot.sales, snapshot.sales_change_pct, "sales"), money(undefined, true), comparison, onAskAssistant)}
        {kpiTile("Orders", asKpi(snapshot.orders, snapshot.orders_change_pct, "orders"), formatNumber, comparison, onAskAssistant)}
        {kpiTile("Conversion", asKpi(snapshot.conversion_rate, snapshot.conversion_change_pct, "conversion"), formatRate, comparison, onAskAssistant)}
        {kpiTile("Average order", asKpi(aov, aovChangePct, "average_order_value"), money(), comparison, onAskAssistant)}
      </StatStrip>
    </Panel>
  );
}

export default function HomeView({
  data,
  failed,
  dashboard,
  dashboardFailed,
  operator,
  insights,
  briefing,
  onAskAssistant,
  onStartBriefing,
  onNavigate,
}: {
  data: OverviewResponse | null;
  failed: boolean;
  dashboard: DashboardResponse | null;
  dashboardFailed: boolean;
  operator?: string | null;
  insights: HomeInsight[];
  /** A briefing turn is streaming. */
  briefing: boolean;
  /** Prefills the composer; nothing is sent. */
  onAskAssistant: (text: string) => void;
  /** Sends the briefing prompt. */
  onStartBriefing: () => void;
  onNavigate: (view: "orders" | "inventory") => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const pending = useMemo(() => (data?.needs_attention.pending_changes ?? []).filter((change) => change.status === "staged"), [data]);
  const queue = useMemo(() => (data ? rows(data, filter) : []), [data, filter]);
  const now = useNow();
  const name = firstName(operator);
  const title = `${now ? greeting(now) : "Hello"}${name ? `, ${name}` : ""}`;
  const today = now ? now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }) : "\u00a0";

  if (failed && !data) {
    return (
      <>
        <PageHeader title={title} subtitle={today} />
        <Notice>
          The Shopware merchant API on port 8005 isn&apos;t reachable. Start it with{" "}
          <code className="rounded bg-(--well) px-1 font-mono text-[13px]">uvicorn merchant.api.main:app --port 8005</code> and reload.
        </Notice>
      </>
    );
  }
  if (!data) {
    return (
      <>
        <PageHeader title={title} subtitle={today} />
        <Skeleton className="h-36" />
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          <Skeleton className="h-96" />
          <Skeleton className="h-72" />
        </div>
      </>
    );
  }

  const counts = {
    orders: data.needs_attention.order_issues.length,
    stock: data.needs_attention.inventory.filter((alert) => alert.kind === "low_stock").length,
    slow: data.needs_attention.inventory.filter((alert) => alert.kind === "slow_mover").length,
  };
  const currency = data.shop?.currency ?? data.snapshot.currency;

  return (
    <div className="ac-reveal flex flex-col gap-5">
      <PageHeader title={title} subtitle={`${today} · ${digestLine(data)}`} />

      <ApprovalsBanner changes={pending} onReview={() => onAskAssistant("Walk me through the changes awaiting my approval and what each one would do.")} />

      <ThisWeek data={data} dashboard={dashboard} dashboardFailed={dashboardFailed} onAskAssistant={onAskAssistant} />

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
        <Panel
          title="Needs you today"
          action={
            <Segmented<Filter>
              label="Filter attention items"
              value={filter}
              onChange={setFilter}
              options={[
                { id: "all", label: "All", count: counts.orders + counts.stock + counts.slow },
                { id: "orders", label: "Orders", count: counts.orders },
                { id: "stock", label: "Low stock", count: counts.stock },
                { id: "slow", label: "Slow", count: counts.slow },
              ]}
            />
          }
        >
          {queue.length === 0 ? (
            <p className="px-[18px] pb-4 pt-1 text-[13.5px] text-(--ink-soft)">Nothing needs you today.</p>
          ) : (
            <>
              <AttentionList>
                {queue.slice(0, ROW_CAP).map((row) =>
                  row.kind === "issue" ? (
                    <IssueRow key={row.issue.issue_id} issue={row.issue} onAskAssistant={onAskAssistant} />
                  ) : (
                    <InventoryRow key={`${row.alert.kind}-${row.alert.listing_id}`} alert={row.alert} onAskAssistant={onAskAssistant} />
                  ),
                )}
              </AttentionList>
              <QueueOverflow
                hidden={queue.length - ROW_CAP}
                link={{
                  label: "See all",
                  // The hidden rows are order issues first, so open Orders when any of them is one.
                  onClick: () => onNavigate(queue.slice(ROW_CAP).some((row) => row.kind === "issue") ? "orders" : "inventory"),
                }}
              />
            </>
          )}
        </Panel>

        <div className="flex flex-col gap-4">
          <Insights insights={insights.length ? insights : (data.insights ?? [])} briefing={briefing} onAskAssistant={onAskAssistant} onStartBriefing={onStartBriefing} />
          <Panel title="Recent orders" action={<ViewLink label="All orders" onClick={() => onNavigate("orders")} />}>
            {data.recent_orders.length === 0 ? (
              <p className="px-[18px] pb-4 text-[13px] text-(--ink-soft)">No orders yet.</p>
            ) : (
              <RecordList rows={orderRows(data.recent_orders.slice(0, RECENT_ORDERS_CAP), currency)} />
            )}
          </Panel>
          <RecentChanges changes={data.recent_changes} />
        </div>
      </div>
    </div>
  );
}
