# Merchant portal web UI

Next.js 16 back office for the Shopware merchant agent: the upstream retail merchant portal
(`examples/retail/merchant-web` + `web-shared/portal` at commit `fd4d5922`) with Shopware data
wiring and Shopware blue (`#189EFF`) as the accent. Everything it shows comes from
`/api/merchant/*` on port 8005; there is no mock data.

![Merchant portal against the Docker shop: briefing in the rail, a price change staged with Shopware's dry-run preview and approved](../../docs/screenshots/merchant-portal.png)

Captured against the live Docker shop (seeded order history) after the browser pass in
`merchant/README.md`: "What needs my attention this morning?", then a staged price change on
`CA-OIL` approved from the rail (Shopware price checked before and after), then reverted the same way.

## Run

```bash
# from the repo root
npm install
npm run dev:merchant          # http://localhost:3006
npm run build -w merchant/web
```

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8005` | Origin of the merchant API; every call goes to `${NEXT_PUBLIC_API_URL}/api/merchant/...`. |

Chat needs `ANTHROPIC_API_KEY` on the API side; the dashboard, catalog, orders, inventory, and the
Approve / Dismiss buttons work without it.

## Layout

- **Sidebar** — Home, Catalog, Orders (badge: open order issues), Inventory (count: low-stock +
  slow-mover alerts), the Assistant toggle with the **pending-approvals count** as a blue badge
  (the vendored `PortalShell` has no slot for it, so `app/page.tsx` sets `data-pending` on that
  button and `globals.css` draws the badge; it also carries an `aria-description`), and the
  operator at the bottom. Shop name and operator come from `shop.name` / `shop.operator` on
  `/overview` (falling back to the session's operator).
- **Home** — greeting with the operator's first name, today's date, and a one-line digest built
  from the snapshot ("Sales are up 1.7% on the week. 5 orders and 6 listings need you today."; down
  / flat wording when negative / zero). Then the "This week" KPI row, "Needs you today" with filter
  chips (All / Orders / Low stock / Slow), "From the assistant", "Recent orders", "Recent changes".
- **Catalog** — listings table (stock, price, status, content) with search and filters; a row
  opens a sheet with facts, variants, and Shopware's pricing context (floor, ceiling, cost, margin).
- **Orders** — open issues (buyer messages quoted as data) and the order list.
- **Inventory** — low-stock and slow-mover alerts with days of cover.
- **Assistant rail** — the blueprint chat over SSE, "You approve every change", streamed briefing
  / metrics / change-preview cards, and the staged-changes strip. The rail's expand button gives the
  full-width chat; the sidebar's Assistant entry shows / hides it.

Below 1280px the sidebar collapses to icons; below 1024px it becomes a top bar and the rail opens on
demand.

## Data wiring

| Route | Feeds |
|---|---|
| `POST /session` | Session id and operator (shared `useSession`). |
| `POST /chat` (SSE) | The rail (`useMerchantChat`); `change_update` events refresh every widget. |
| `GET /overview` | Sidebar counts, greeting digest, "Needs you today", recent orders, recent changes, `shop` identity; also the fallback for the KPI row and the pending list. |
| `GET /dashboard` | "This week": `period.label`, `against`, `kpis.{sales,orders,conversion,average_order}` with `change_pct` and `points` sparklines. **Conversion shows "n/a" plus `note` when `value` is null** — never a substitute number. Until the route answers, the snapshot on `/overview` stands in. |
| `GET /alerts` | Inventory and Orders pages; Catalog row annotations. |
| `GET /listings?query=`, `GET /listings/{id}` | Catalog table and detail sheet (listing + pricing context). |
| `GET /orders?limit=20` | Orders page list (`order_number`, items, customer, total, status, `issue`); `overview.recent_orders` until it answers. |
| `GET /changes?status=staged` | The rail's staged-changes strip and the sidebar's pending count; `overview.needs_attention.pending_changes` until it answers. Listing a staged change also admits it to this session's provenance on the host, so a change staged in an earlier session (or before a host restart) can be approved or dismissed from its card. Resolved changes list on Home from `overview.recent_changes`. |
| `POST /changes/{id}/apply`, `POST /changes/{id}/discard` | Approve / Dismiss on every change card. `{ ok: false, reason }` shows the gate's reason on the card. |
| `GET/DELETE /memory` | The Activity inspector's "Business memory" view. |
| `GET /health` | Shown when no session can start: unreachable, or running without Shopware credentials (`error`). |

Amounts render in EUR with `de-DE` formatting (`lib/format.ts`); the shared `formatMoney` is
en-US/USD and is not used for money the app renders itself.

## Staged changes and the Shopware preview

Every `StagedChange` renders through `components/ChangeCard.tsx`, whether it arrived as a streamed
`change_preview` card or from the ledger:

- `items[]` as `target · field` rows with `before → after` (prices in the change's currency);
  long text fields as stacked before / after blocks.
- **`guardrail_notes[]` as a "Shopware preview" block** — this is where the host's server-side
  dry run (`dryRun=true` on the Admin API) reports what the write would do.
- Approve / Dismiss, then the applied or dismissed state with who and when.

Acting on a card syncs every other card for the same change in the transcript and re-reads the
portal (`onChangeResolved` in `app/page.tsx`).

## Files

| Path | Role |
|---|---|
| `app/page.tsx` | Session, overview / dashboard / staged reads, `PortalShell`, rail, inspector |
| `components/views/HomeView.tsx` | Dashboard: greeting, KPI row, attention queue, insights, recent orders / changes |
| `components/views/CatalogView.tsx`, `OrdersView.tsx`, `InventoryView.tsx` | The other pages |
| `components/AssistantPanel.tsx`, `PendingChanges.tsx` | Rail: shared panel + staged-changes strip |
| `components/ChangeCard.tsx` | Change diff, Shopware preview, Approve / Dismiss |
| `components/generative/*` | `metrics`, `digest`, `change_preview` cards streamed by the assistant |
| `lib/api.ts`, `lib/types.ts`, `lib/format.ts`, `lib/kinds.ts`, `lib/changes.ts` | API client, payload types, EUR formatting, record styles, transcript helpers |
