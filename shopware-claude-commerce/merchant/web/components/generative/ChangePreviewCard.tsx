// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import ChangeCard from "@/components/ChangeCard";
import type { ChangePreviewPayload, StagedChange } from "@/lib/types";

/** The streamed preview of one staged change; the card body is shared with the ledger list. */
export default function ChangePreviewCard({
  payload,
  onResolved,
}: {
  payload: ChangePreviewPayload;
  onResolved?: (change: StagedChange) => void;
}) {
  return <ChangeCard change={payload.change} headline={payload.headline ?? "Proposed change"} note={payload.note} onResolved={onResolved} />;
}
