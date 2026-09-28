// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

"use client";

import { useMemo, useState } from "react";
import { Icon, KindIcon, plural } from "web-shared";
import type { StagedChange } from "@/lib/types";
import ChangeCard from "./ChangeCard";

/**
 * The ledger's staged changes at the top of the assistant rail — what the assistant proposed
 * in this or an earlier session and nobody has approved yet. Collapsed to one line until the
 * operator opens it; a change acted on here stays listed with its new status until the strip
 * is closed, so the outcome is visible.
 */
export default function PendingChanges({
  changes,
  onResolved,
}: {
  changes: StagedChange[];
  onResolved: (change: StagedChange) => void;
}) {
  const [open, setOpen] = useState(false);
  const [resolved, setResolved] = useState<Record<string, StagedChange>>({});

  const listed = useMemo(() => {
    const staged = changes.filter((change) => change.status === "staged");
    const stagedIds = new Set(staged.map((change) => change.change_id));
    const settled = Object.values(resolved).filter((change) => !stagedIds.has(change.change_id));
    return [...staged, ...settled];
  }, [changes, resolved]);
  const pendingCount = changes.filter((change) => change.status === "staged").length;

  if (listed.length === 0) return null;

  const toggle = () => {
    if (open) setResolved({});
    setOpen((value) => !value);
  };

  return (
    <section data-pending-changes className="shrink-0 border-b border-l border-(--line) bg-(--violet-soft)/60">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-(--violet-soft)"
      >
        <KindIcon icon="edit" tone="violet" size={28} />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-semibold leading-tight text-(--ink)">
            {pendingCount ? `${plural(pendingCount, "change")} awaiting approval` : "Changes you just resolved"}
          </span>
          <span className="block truncate text-[11.5px] text-(--ink-soft)">
            {open ? "Nothing applies until you approve." : listed.map((change) => change.summary).join(" · ")}
          </span>
        </span>
        <Icon name="chevron-right" size={16} className={`shrink-0 text-(--ink-soft) transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open ? (
        <div className="panel-scroll max-h-[45dvh] overflow-y-auto px-3 pb-3">
          <div className="flex flex-col gap-2.5">
            {listed.map((change) => (
              <ChangeCard
                key={change.change_id}
                change={change}
                headline={change.status === "staged" ? "Awaiting your approval" : "Resolved"}
                onResolved={(next) => {
                  setResolved((current) => ({ ...current, [next.change_id]: next }));
                  onResolved(next);
                }}
              />
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
