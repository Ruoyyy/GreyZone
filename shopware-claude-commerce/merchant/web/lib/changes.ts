// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** What the transcript and the home page derive from streamed cards and staged changes. */

import { type ChatItem, titleCase } from "web-shared";
import type { DigestEntry, DigestPayload, HomeInsight, StagedChange } from "./types";

const CHANGE_PREVIEW = "change_preview";
const DIGEST = "digest";
/** How many briefing items the home's "From the assistant" card shows. */
const INSIGHT_CAP = 3;

export const BRIEFING_PROMPT = "What needs my attention this morning?";

/** The ledger carries a refund as a `campaign` change marked by its `payment_state` item
 *  (merchant/api/refunds.py); every other change shows its own kind. */
export function changeKindLabel(change: StagedChange): string {
  const refund = change.kind === "campaign" && change.items.some((item) => item.field === "payment_state");
  return refund ? "Refund" : titleCase(change.kind);
}

/**
 * After the operator acts on a card, every change_preview for the same change in the
 * transcript shows the new status, and chips written for the staged state go stale.
 */
export function syncChangeInTranscript(items: ChatItem[], change: StagedChange): ChatItem[] {
  return items.map((item) => {
    if (item.kind !== "assistant") return item;
    let touched = false;
    const segments = item.segments.map((segment) => {
      if (
        segment.type !== "ui" ||
        segment.block.component !== CHANGE_PREVIEW ||
        (segment.block.payload as { change_id?: string }).change_id !== change.change_id
      ) {
        return segment;
      }
      touched = true;
      return { ...segment, block: { ...segment.block, payload: { ...(segment.block.payload as object), change } } };
    });
    if (!touched) return item;
    return { ...item, segments, suggestionsStale: item.suggestionsStale || change.status !== "staged" };
  });
}

/** The hand-off a briefing item offers; pending changes have none — approval stays on the card. */
export function triagePrompt(item: DigestEntry): { label: string; prompt: string } | null {
  const listingRef = item.listing ? `${item.listing.title} (${item.listing.listing_id})` : item.ref_id;
  switch (item.kind) {
    case "low_stock":
      return listingRef ? { label: "Draft restock", prompt: `Draft a restock plan for ${listingRef}.` } : null;
    case "slow_mover":
      return listingRef ? { label: "Plan markdown", prompt: `Plan a markdown for ${listingRef}.` } : null;
    case "order_issue":
      return {
        label: "Draft reply",
        prompt: item.ref_id ? `Help me handle order ${item.ref_id}: ${item.headline}` : `Help me handle this order issue: ${item.headline}`,
      };
    case "metric":
      return { label: "Ask why", prompt: `What's driving this: ${item.headline}?` };
    default:
      return null;
  }
}

/**
 * The home's "From the assistant" cards come from the assistant's own briefing: the newest
 * settled digest card in the transcript, its items with a follow-up question each.
 */
export function insightsFromChat(items: ChatItem[]): HomeInsight[] {
  for (let index = items.length - 1; index >= 0; index--) {
    const item = items[index];
    if (item.kind !== "assistant" || item.pending) continue;
    const digest = item.segments.find(
      (segment) => segment.type === "ui" && segment.block.component === DIGEST && segment.status === "final",
    );
    if (!digest || digest.type !== "ui") continue;
    const entries = (digest.block.payload as DigestPayload).items ?? [];
    return entries
      .filter((entry) => entry.kind !== "pending_change")
      .slice(0, INSIGHT_CAP)
      .map((entry, position) => ({
        insight_id: `${item.turn}-${position}`,
        kind: entry.kind,
        headline: entry.headline,
        detail: entry.why_it_matters,
        prompt: triagePrompt(entry)?.prompt ?? `Tell me more: ${entry.headline}`,
      }));
  }
  return [];
}
