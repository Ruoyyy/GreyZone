// Copyright 2026 agentic-commerce-lab range variant
// SPDX-License-Identifier: MIT

"use client";

import type { SentMail } from "@/lib/types";

/** When the ISO timestamp won't parse, show it raw rather than "Invalid Date". */
function sentAt(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleString();
}

/** The assistant's simulated outbox for this session, newest first; read-only, like the
 *  orders drawer. Mail is recorded locally, never delivered. */
export default function MailDrawer({
  open,
  messages,
  failed,
  onClose,
}: {
  open: boolean;
  /** Null while loading. */
  messages: SentMail[] | null;
  failed: boolean;
  onClose: () => void;
}) {
  return (
    <div
      className={`fixed inset-0 z-50 ${open ? "" : "pointer-events-none"}`}
      aria-hidden={!open}
      inert={!open}
    >
      <div
        onClick={onClose}
        aria-hidden
        className={`absolute inset-0 bg-(--ink)/30 transition-opacity duration-300 ${open ? "opacity-100" : "opacity-0"}`}
      />
      <aside
        className={`absolute inset-y-0 right-0 flex w-[min(92vw,420px)] flex-col border-l border-(--line) bg-(--card) transition-transform duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-(--line) px-4 py-3">
          <h2 className="text-sm font-bold text-(--ink)">
            Mail{messages ? ` · ${messages.length}` : ""}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close mail"
            className="rounded-md px-2 py-0.5 text-lg leading-none text-(--ink-soft) hover:text-(--ink)"
          >
            ×
          </button>
        </div>

        <div className="panel-scroll flex-1 overflow-y-auto px-4 py-3">
          {failed ? (
            <p role="alert" className="mt-8 text-center text-[15px] text-(--danger)">Couldn&apos;t load your mail. Try again.</p>
          ) : messages === null ? (
            <p className="mt-8 text-center text-[15px] text-(--ink-soft)/80">Loading mail…</p>
          ) : messages.length === 0 ? (
            <p className="mt-8 text-center text-[15px] text-(--ink-soft)/80">No mail sent yet.</p>
          ) : (
            <ul className="divide-y divide-(--line)">
              {messages.map((mail) => (
                <li key={mail.message_id} className="ac-reveal py-3 first:pt-0">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 text-[13px] font-semibold text-(--ink)" title={mail.subject}>
                      {mail.subject || "(no subject)"}
                    </div>
                    <span className="shrink-0 rounded-full bg-(--accent-soft) px-1.5 py-0.5 text-[11px] font-semibold capitalize text-(--ink)">
                      {mail.status}
                    </span>
                  </div>
                  <dl className="mt-1 space-y-0.5 text-[11px] text-(--ink-soft)">
                    <div className="flex gap-1">
                      <dt className="font-semibold">To</dt>
                      <dd className="min-w-0 truncate">{mail.to}</dd>
                    </div>
                    {mail.cc && mail.cc.length > 0 ? (
                      <div className="flex gap-1">
                        <dt className="font-semibold">Cc</dt>
                        <dd className="min-w-0 truncate">{mail.cc.join(", ")}</dd>
                      </div>
                    ) : null}
                    <div className="flex gap-1">
                      <dt className="font-semibold">Sent</dt>
                      <dd>{sentAt(mail.ts)}</dd>
                    </div>
                  </dl>
                  <p className="mt-2 whitespace-pre-wrap text-[12px] leading-relaxed text-(--ink)">{mail.body}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}
