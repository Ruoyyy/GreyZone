// Copyright 2026 agentic-commerce-lab range variant
// SPDX-License-Identifier: MIT

"use client";

import { AskButton, KindIcon, Notice, PageHeader, Panel, Pill, plural, Skeleton, useResource } from "web-shared";
import { fetchMail } from "@/lib/api";
import type { SentMail } from "@/lib/types";

/** When the ISO timestamp won't parse, show it raw rather than "Invalid Date". */
function sentAt(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleString();
}

function MailRow({ mail }: { mail: SentMail }) {
  return (
    <li className="flex gap-3 px-[18px] py-3">
      <KindIcon icon="message" tone="info" />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 text-[13.5px] font-medium leading-snug text-(--ink)" title={mail.subject}>
            {mail.subject || "(no subject)"}
          </div>
          <Pill tone="ok" dot>
            {mail.status}
          </Pill>
        </div>
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[12.5px] text-(--ink-soft)">
          <dt>From</dt>
          <dd className="min-w-0 break-all">{mail.from}</dd>
          <dt>To</dt>
          <dd className="min-w-0 break-all">{mail.to}</dd>
          {mail.cc && mail.cc.length > 0 ? (
            <>
              <dt>Cc</dt>
              <dd className="min-w-0 break-all">{mail.cc.join(", ")}</dd>
            </>
          ) : null}
          <dt>Sent</dt>
          <dd className="tabular-nums">{sentAt(mail.ts)}</dd>
        </dl>
        <p className="mt-2 max-h-[320px] overflow-y-auto whitespace-pre-wrap text-[13px] leading-relaxed text-(--ink)">{mail.body}</p>
      </div>
    </li>
  );
}

/** The assistant's simulated outbox for this session, newest first; read-only. Mail is
 *  recorded locally by the host, never delivered. Re-read after every assistant turn so a
 *  mail the assistant just sent shows up. */
export default function MailView({
  refreshKey,
  turnCount,
  onAskAssistant,
}: {
  refreshKey: number;
  turnCount: number;
  onAskAssistant: (text: string) => void;
}) {
  const { data, failed } = useResource(fetchMail, [refreshKey, turnCount]);

  if (failed) return <Notice>Could not load the outbox.</Notice>;
  if (!data) return <Skeleton className="h-40" />;
  if (!data.enabled) return <Notice>The assistant&apos;s mail tool is turned off.</Notice>;

  const { messages } = data;
  return (
    <>
      <PageHeader title="Mail" subtitle="Emails the assistant sent from the shop this session (simulated, never delivered)">
        <AskButton label="Ask the assistant to send one" onClick={() => onAskAssistant("Send an email to ")} />
      </PageHeader>
      {messages.length === 0 ? (
        <Notice>No mail sent yet. Ask the assistant to email a customer, a supplier, or yourself.</Notice>
      ) : (
        <Panel title="Sent" subtitle={plural(messages.length, "message")}>
          <ul className="divide-y divide-(--line)">
            {messages.map((mail) => (
              <MailRow key={mail.message_id} mail={mail} />
            ))}
          </ul>
        </Panel>
      )}
    </>
  );
}
