// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { CHANGE_STATUS, DigestList, DigestRow, GenCard, GenCardHeader, type IconName, plural, type Tone } from "web-shared";
import { triagePrompt } from "@/lib/changes";
import { formatMoney, formatNumber } from "@/lib/format";
import { INVENTORY_KINDS } from "@/lib/kinds";
import type { DigestEntry, DigestPayload } from "@/lib/types";

const KINDS: Record<DigestEntry["kind"], { icon: IconName; tone: Tone }> = {
  ...INVENTORY_KINDS,
  order_issue: { icon: "inbox", tone: "danger" },
  metric: { icon: "chart", tone: "ok" },
  pending_change: { icon: "edit", tone: "violet" },
  note: { icon: "message", tone: "muted" },
};

function context(item: DigestEntry) {
  if (item.listing) {
    return (
      <span>
        {item.listing.listing_id} · {item.listing.stock === 0 ? "sold out" : `${formatNumber(item.listing.stock)} in stock`} ·{" "}
        {formatMoney(item.listing.price, item.listing.currency)}
      </span>
    );
  }
  if (item.change) {
    return (
      <span>
        {item.change.change_id} · {CHANGE_STATUS[item.change.status].label.toLowerCase()}
      </span>
    );
  }
  return null;
}

/** The morning briefing: one row per thing that needs the operator, each with a hand-off. */
export default function DigestCard({ payload, onPrefill }: { payload: DigestPayload; onPrefill?: (text: string) => void }) {
  const items = payload.items ?? [];
  return (
    <GenCard>
      <GenCardHeader title={payload.title ?? "Needs attention"} aside={plural(items.length, "item")} />
      <DigestList>
        {items.map((item, index) => {
          const triage = onPrefill ? triagePrompt(item) : null;
          const style = KINDS[item.kind] ?? KINDS.note;
          const soldOut = item.kind === "low_stock" && item.listing?.stock === 0;
          return (
            <DigestRow
              key={`${item.ref_id ?? item.headline}-${index}`}
              icon={style.icon}
              tone={soldOut ? "danger" : style.tone}
              headline={item.headline}
              why={item.why_it_matters}
              context={context(item)}
              action={triage ? { label: triage.label, onClick: () => onPrefill?.(triage.prompt) } : null}
            />
          );
        })}
      </DigestList>
    </GenCard>
  );
}
