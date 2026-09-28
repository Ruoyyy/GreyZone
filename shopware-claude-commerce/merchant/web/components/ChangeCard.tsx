// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

"use client";

import { useEffect, useState } from "react";
import {
  ApproveBar,
  ChangeStatusPill,
  describeProposer,
  formatDate,
  formatRate,
  GenCard,
  GenCardHeader,
  humanizeField,
  Icon,
  isLongTextDiff,
  LongTextDiff,
} from "web-shared";
import { actOnChange, type ChangeAction } from "@/lib/api";
import { changeKindLabel } from "@/lib/changes";
import { formatMoney, formatNumber } from "@/lib/format";
import type { ChangeItem, StagedChange } from "@/lib/types";

const CURRENCY_FIELD = /(^|_)(price|cost|budget|spend|revenue|amount|fee|total)(_|$)/;
const PERCENT_FIELD = /(^|_)(pct|percent|margin)(_|$)/;
const ACTION_FAILED = "That action did not go through. Check the merchant API and try again.";

/** A diff value by what its field name says it is; money in the change's currency. */
function formatValue(field: string, value: unknown, currency: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  let numeric = value;
  if (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value.trim()) && (CURRENCY_FIELD.test(field) || PERCENT_FIELD.test(field))) {
    numeric = Number(value);
  }
  if (typeof numeric === "number") {
    if (CURRENCY_FIELD.test(field)) return formatMoney(numeric, currency);
    if (PERCENT_FIELD.test(field)) return formatRate(numeric);
    return Number.isInteger(numeric) ? formatNumber(numeric) : numeric.toFixed(2);
  }
  if (typeof numeric === "boolean") return numeric ? "yes" : "no";
  if (typeof numeric === "object") return JSON.stringify(numeric);
  return String(numeric);
}

/** One row per short field change: target, field, before struck through, after bold. */
function DiffRows({ items, currency }: { items: ChangeItem[]; currency: string | null | undefined }) {
  if (!items.length) return null;
  return (
    <div className="mx-3.5 mt-2.5 divide-y divide-(--line) rounded-[11px] bg-(--ground)">
      {items.map((item, index) => (
        <div key={`${item.target}-${item.field}-${index}`} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-3 py-2">
          <div className="min-w-0 text-[12.5px] text-(--ink-soft)">
            <span className="tabular-nums break-all">{item.target}</span>
            <span> · {humanizeField(item.field)}</span>
          </div>
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-[14px] tabular-nums break-words">
            <s className="min-w-0 text-(--ink-soft) decoration-(--ink-faint)">{formatValue(item.field, item.before, currency)}</s>
            <Icon name="arrow-right" size={13} className="self-center text-(--ink-faint)" />
            <b className="min-w-0 text-[15px] font-bold text-(--ink)">{formatValue(item.field, item.after, currency)}</b>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Shopware's server-side dry run of the write, as the host relays it in `guardrail_notes`. */
export function ShopwarePreview({ notes }: { notes?: string[] | null }) {
  if (!notes?.length) return null;
  return (
    <div data-shopware-preview className="mx-3.5 mt-2.5 rounded-[11px] border border-(--accent)/25 bg-(--accent-soft) px-3 py-2 text-[12.5px] leading-snug text-(--ink)">
      <div className="mb-1 flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-(--accent-ink)">
        <Icon name="alert" size={13} className="text-(--accent)" />
        Shopware preview
      </div>
      <ul className="space-y-1">
        {notes.map((note, index) => (
          <li key={`${index}-${note}`} className="whitespace-pre-line break-words">
            {note}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Tracks the change a card shows: starts from the payload, is replaced by the API's answer
 * when the operator acts, and re-syncs when a later turn rewrites the payload. A gate that
 * holds the change surfaces its reason instead of a generic failure.
 */
function useChangeCard(streamed: StagedChange, onResolved?: (change: StagedChange) => void) {
  const [change, setChange] = useState(streamed);
  useEffect(() => {
    setChange(streamed);
  }, [streamed]);
  const [busy, setBusy] = useState<ChangeAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (action: ChangeAction) => {
    if (busy) return;
    setBusy(action);
    setError(null);
    const response = await actOnChange(change.change_id, action);
    if (response?.ok && response.change) {
      setChange(response.change);
      onResolved?.(response.change);
    } else {
      setError(response?.reason?.trim() || ACTION_FAILED);
    }
    setBusy(null);
  };

  return { change, busy, error, act };
}

/**
 * A staged change, wherever it shows: streamed as a change_preview card or listed from
 * the ledger. Diff rows, the Shopware preview, then Approve / Dismiss through the host's gate.
 */
export default function ChangeCard({
  change: streamed,
  headline,
  note,
  onResolved,
}: {
  change: StagedChange;
  headline?: string | null;
  note?: string | null;
  /** Called with the change as the API returned it after an approve or dismiss. */
  onResolved?: (change: StagedChange) => void;
}) {
  const { change, busy, error, act } = useChangeCard(streamed, onResolved);
  const shortItems = change.items.filter((item) => !isLongTextDiff(item));
  const longItems = change.items.filter(isLongTextDiff);

  return (
    <GenCard>
      <GenCardHeader
        title={headline ?? "Proposed change"}
        meta={
          <>
            <ChangeStatusPill status={change.status} />
            <span>{changeKindLabel(change)}</span>
            <span aria-hidden>·</span>
            <span>{describeProposer(change)}</span>
            <span aria-hidden>·</span>
            <span>{formatDate(change.created_at)}</span>
          </>
        }
      />
      <p className="px-3.5 pt-2 text-[14px] leading-snug text-(--ink)">{change.summary}</p>
      {note ? <p className="px-3.5 pt-1 text-[12.5px] leading-snug text-(--ink-soft)">{note}</p> : null}

      <DiffRows items={shortItems} currency={change.currency} />
      {longItems.map((item, index) => (
        <LongTextDiff key={`${item.target}-${item.field}-${index}`} item={item} />
      ))}

      {change.margin_impact != null ? (
        <p className="mx-3.5 mt-2 text-[12.5px] tabular-nums text-(--ink-soft)">
          Margin impact{" "}
          <b className={`font-semibold ${change.margin_impact < 0 ? "text-(--danger)" : "text-(--ok)"}`}>
            {change.margin_impact > 0 ? "+" : ""}
            {formatMoney(change.margin_impact, change.currency)}
          </b>
        </p>
      ) : null}

      <ShopwarePreview notes={change.guardrail_notes} />
      <ApproveBar change={change} busy={busy} error={error} canAct onAct={(action) => void act(action)} />
    </GenCard>
  );
}
