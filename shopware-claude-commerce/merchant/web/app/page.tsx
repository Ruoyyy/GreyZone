// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AssistantRail,
  Inspector,
  Notice,
  PageHeader,
  type PortalNavItem,
  PortalShell,
  type Prefill,
  useMerchantChat,
  useResource,
  useSession,
} from "web-shared";
import AssistantPanel from "@/components/AssistantPanel";
import CatalogView from "@/components/views/CatalogView";
import HomeView from "@/components/views/HomeView";
import InventoryView from "@/components/views/InventoryView";
import InvoicesView from "@/components/views/InvoicesView";
import MailView from "@/components/views/MailView";
import OrdersView from "@/components/views/OrdersView";
import { api, fetchChanges, fetchDashboard, fetchHealth, fetchOverview, UNREACHABLE } from "@/lib/api";
import { BRIEFING_PROMPT, insightsFromChat, syncChangeInTranscript } from "@/lib/changes";
import type { StagedChange } from "@/lib/types";

type PortalView = "home" | "catalog" | "orders" | "inventory" | "invoices" | "mail";

/** The rail is part of the default layout from this width up; narrower screens open it on demand. */
const RAIL_DEFAULT_OPEN_FROM_PX = 1024;
const FALLBACK_SHOP_NAME = "Shopware";
const OPERATOR_ROLE = "Operator";

/**
 * Before a session exists there is nothing to render; if the host is down or unconfigured,
 * its health answer says so. Null while the session may still start.
 */
function NoSession({ health, unreachable }: { health: { ok: boolean; error?: string | null } | null; unreachable: boolean }) {
  if (!unreachable && (health === null || health.ok)) return null;
  return (
    <>
      <PageHeader title="Merchant workspace" subtitle="Waiting for the Shopware merchant API" />
      <Notice>
        {unreachable ? (
          <>
            The merchant API on port 8005 isn&apos;t reachable. Start it with{" "}
            <code className="rounded bg-(--well) px-1 font-mono text-[13px]">uvicorn merchant.api.main:app --port 8005</code> and reload.
          </>
        ) : (
          <>The merchant API is running without Shopware credentials{health?.error ? `: ${health.error}` : "."}</>
        )}
      </Notice>
    </>
  );
}

/** The store's mark: a Shopware-blue tile with the shop's initial. */
function StoreMark({ name }: { name: string }) {
  return (
    <span
      aria-hidden
      className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-[10px] bg-(--brand) text-[16px] font-bold text-(--on-accent) shadow-[inset_0_-3px_0_rgba(0,0,0,0.18)]"
    >
      {name.trim().charAt(0).toUpperCase() || "S"}
    </span>
  );
}

export default function PortalPage() {
  const session = useSession(api);
  const [view, setView] = useState<PortalView>("home");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  // Bumped whenever a staged change moves, so every widget re-reads the store the agent wrote.
  const [refreshKey, setRefreshKey] = useState(0);
  const refreshPortal = useCallback(() => setRefreshKey((value) => value + 1), []);

  const chat = useMerchantChat<StagedChange>(api, {
    ...session,
    unreachable: UNREACHABLE,
    onPortalRefresh: refreshPortal,
  });

  // Public, so it answers even when no session can start and explains why.
  const { data: health, failed: healthFailed } = useResource(session.sessionId ? null : fetchHealth, [session.sessionId, refreshKey]);
  // The overview feeds the home page, the sidebar counts, and the shop identity, so it loads here.
  const { data: overview, failed: overviewFailed } = useResource(session.sessionId ? fetchOverview : null, [session.sessionId, refreshKey]);
  const { data: dashboard, failed: dashboardFailed } = useResource(session.sessionId ? fetchDashboard : null, [session.sessionId, refreshKey]);
  // The ledger's staged changes; the overview's (capped) list stands in until `/changes` answers.
  const { data: stagedData, failed: stagedFailed } = useResource(session.sessionId ? () => fetchChanges("staged") : null, [session.sessionId, refreshKey]);

  useEffect(() => {
    setAssistantOpen(window.innerWidth >= RAIL_DEFAULT_OPEN_FROM_PX);
  }, []);

  const askAssistant = useCallback((text: string) => {
    setAssistantOpen(true);
    setPrefill({ text, nonce: Date.now() });
  }, []);

  const startBriefing = useCallback(() => {
    setAssistantOpen(true);
    void chat.send(BRIEFING_PROMPT);
  }, [chat]);

  // An approve or dismiss from any card: the transcript's cards follow, and every widget re-reads.
  const setItems = chat.setItems;
  const onChangeResolved = useCallback(
    (change: StagedChange) => {
      setItems((items) => syncChangeInTranscript(items, change));
      refreshPortal();
    },
    [setItems, refreshPortal],
  );

  const pendingChanges = useMemo(() => {
    const source = stagedData?.changes ?? (stagedFailed ? overview?.needs_attention.pending_changes : null) ?? [];
    return source.filter((change) => change.status === "staged");
  }, [stagedData, stagedFailed, overview]);

  // The sidebar's pending-approvals badge. PortalShell (vendored, unmodified) renders the
  // Assistant toggle itself with no slot for a count, so the count is set as `data-pending`
  // on that button — the sidebar's only `aria-pressed` control — and globals.css draws it.
  // (The rail is an aside too; the sidebar is the one holding the "Portal views" nav.)
  const pendingCount = pendingChanges.length;
  useEffect(() => {
    const toggle = document.querySelector<HTMLButtonElement>('aside:has(> nav[aria-label="Portal views"]) button[aria-pressed]');
    if (!toggle) return;
    if (pendingCount > 0) {
      toggle.dataset.pending = String(pendingCount);
      toggle.setAttribute("aria-description", `${pendingCount} change${pendingCount === 1 ? "" : "s"} awaiting approval`);
    } else {
      delete toggle.dataset.pending;
      toggle.removeAttribute("aria-description");
    }
  }, [pendingCount]);

  const insights = useMemo(() => insightsFromChat(chat.items), [chat.items]);

  const nav = useMemo<PortalNavItem<PortalView>[]>(() => {
    const alerts = overview?.snapshot.alerts;
    const needs = overview?.needs_attention;
    const issues = alerts?.order_issues ?? needs?.order_issues.length ?? null;
    const inventory =
      alerts && (alerts.low_stock != null || alerts.slow_movers != null)
        ? (alerts.low_stock ?? 0) + (alerts.slow_movers ?? 0)
        : (needs?.inventory.length ?? null);
    return [
      { id: "home", label: "Home", icon: "home" },
      { id: "catalog", label: "Catalog", icon: "tag" },
      { id: "orders", label: "Orders", icon: "inbox", attention: issues || null },
      { id: "inventory", label: "Inventory", icon: "box", count: inventory },
      { id: "invoices", label: "Invoices", icon: "inbox" },
      { id: "mail", label: "Mail", icon: "message" },
    ];
  }, [overview]);

  const shop = overview?.shop;
  const shopName = shop?.name || FALLBACK_SHOP_NAME;
  const operator = shop?.operator || session.operator || null;
  const currency = shop?.currency ?? overview?.snapshot.currency;

  return (
    <>
      <PortalShell
        brand={{ mark: <StoreMark name={shopName} />, name: shopName, detail: "Merchant workspace" }}
        nav={nav}
        view={view}
        onViewChange={setView}
        operator={{ name: operator ?? OPERATOR_ROLE, role: shop?.sales_channel ? `${OPERATOR_ROLE} · ${shop.sales_channel}` : OPERATOR_ROLE }}
        assistantOpen={assistantOpen}
        assistantBusy={chat.busy}
        onToggleAssistant={() => setAssistantOpen((open) => !open)}
        rail={
          <AssistantRail open={assistantOpen} storageKey="shopware-merchant-panel-width" onClose={() => setAssistantOpen(false)}>
            {(rail) => (
              <AssistantPanel
                chat={chat}
                prefill={prefill}
                pendingChanges={pendingChanges}
                onPrefill={askAssistant}
                onChangeResolved={onChangeResolved}
                newMemoryCount={chat.newMemoryKeys.size}
                onOpenActivity={() => setActivityOpen(true)}
                {...rail}
              />
            )}
          </AssistantRail>
        }
      >
        {session.sessionId ? (
          <>
            {view === "home" ? (
              <HomeView
                data={overview}
                failed={overviewFailed}
                dashboard={dashboard}
                dashboardFailed={dashboardFailed}
                operator={operator}
                insights={insights}
                briefing={chat.busy}
                onAskAssistant={askAssistant}
                onStartBriefing={startBriefing}
                onNavigate={setView}
              />
            ) : null}
            {view === "catalog" ? <CatalogView refreshKey={refreshKey} onAskAssistant={askAssistant} /> : null}
            {view === "orders" ? (
              <OrdersView
                refreshKey={refreshKey}
                recentOrders={overview?.recent_orders ?? (overviewFailed ? [] : null)}
                currency={currency}
                onAskAssistant={askAssistant}
              />
            ) : null}
            {view === "inventory" ? <InventoryView refreshKey={refreshKey} onAskAssistant={askAssistant} /> : null}
            {view === "invoices" ? <InvoicesView onAskAssistant={askAssistant} /> : null}
            {view === "mail" ? <MailView refreshKey={refreshKey} turnCount={chat.turnCount} onAskAssistant={askAssistant} /> : null}
          </>
        ) : (
          <NoSession health={health} unreachable={healthFailed} />
        )}
      </PortalShell>
      {activityOpen ? (
        <Inspector
          turnCount={chat.turnCount}
          streaming={chat.streaming}
          trace={chat.trace}
          memory={chat.memory}
          newMemoryKeys={chat.newMemoryKeys}
          memoryTitle="Business memory"
          onClose={() => setActivityOpen(false)}
        />
      ) : null}
    </>
  );
}
