// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

"use client";

import { AssistantPanel as PanelShell, type MerchantChat, type Prefill } from "web-shared";
import type { StagedChange } from "@/lib/types";
import GenerativeBlock from "./generative";
import PendingChanges from "./PendingChanges";

const COPY = {
  title: "Merchant assistant",
  intro: "Ask about performance, inventory, pricing, or campaigns.",
  starters: [
    "What needs my attention this morning?",
    "How did sales do this week compared to last?",
    "Which listings are running low on stock?",
    "Which slow movers should we mark down?",
  ],
  label: "Message the merchant assistant",
  placeholder: "Ask about sales, stock, pricing…",
};

/** The rail: the ledger's staged changes, then the shared assistant panel with our cards. */
export default function AssistantPanel({
  chat,
  prefill,
  pendingChanges,
  onPrefill,
  onChangeResolved,
  ...shell
}: {
  chat: MerchantChat<StagedChange>;
  prefill: Prefill | null;
  pendingChanges: StagedChange[];
  onPrefill: (text: string) => void;
  onChangeResolved: (change: StagedChange) => void;
  newMemoryCount: number;
  onOpenActivity: () => void;
  onClose: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
}) {
  return (
    <div className="flex h-full w-full flex-col">
      <PendingChanges changes={pendingChanges} onResolved={onChangeResolved} />
      <div className="min-h-0 flex-1">
        <PanelShell
          chat={chat}
          copy={COPY}
          prefill={prefill}
          renderBlock={(segment) => (
            <GenerativeBlock block={segment.block} status={segment.status} onChangeResolved={onChangeResolved} onPrefill={onPrefill} />
          )}
          {...shell}
        />
      </div>
    </div>
  );
}
