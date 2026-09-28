// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ActivityButton } from "web-shared/ActivityButton";
import type { Brand } from "@/lib/types";

const SIGN_IN_ERROR_MS = 5000;

/** Shopware account login for orders; the password goes straight to the storefront API. */
function ShopSignIn({
  sessionId,
  signedIn,
  onSignIn,
  onSignOut,
}: {
  sessionId: string | null;
  signedIn: boolean;
  onSignIn: (email: string, password: string) => Promise<boolean>;
  onSignOut: () => void;
}) {
  const [phase, setPhase] = useState<"idle" | "busy" | "error">("idle");
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (phase !== "error") return;
    const timer = window.setTimeout(() => setPhase("idle"), SIGN_IN_ERROR_MS);
    return () => window.clearTimeout(timer);
  }, [phase]);

  if (signedIn) {
    return (
      <span
        data-signed-in
        className="flex items-center gap-1.5 rounded-full bg-(--accent-soft) py-1 pl-3 pr-1.5 text-[13px] font-semibold text-(--ink)"
      >
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-600" />
        Signed in
        <button
          type="button"
          onClick={onSignOut}
          className="rounded-full px-1.5 py-0.5 text-[11px] font-medium text-(--ink-soft) hover:text-(--ink)"
        >
          Sign out
        </button>
      </span>
    );
  }
  return <>
    <button
      type="button"
      onClick={() => setOpen(true)}
      className="rounded-full bg-(--brand) px-3.5 py-1.5 text-[13px] font-semibold text-(--brand-contrast) transition hover:brightness-110"
    >
      Sign in
    </button>
    {open ? (
      <div className="fixed inset-0 z-50 flex items-start justify-end bg-(--ink)/30 p-4" onClick={() => setOpen(false)}>
        <form
          className="w-full max-w-sm rounded-2xl bg-(--card) p-5 shadow-xl"
          onClick={(event) => event.stopPropagation()}
          onSubmit={async (event) => {
            event.preventDefault();
            setPhase("busy");
            setError("");
            try {
              const signed = await onSignIn(email, password);
              if (signed) {
                setPassword("");
                setPhase("idle");
                setOpen(false);
              } else {
                setError("Shopware did not confirm the sign-in.");
                setPhase("error");
              }
            } catch (cause) {
              setError(cause instanceof Error ? cause.message : "Sign-in failed.");
              setPhase("error");
            }
          }}
        >
          <h2 className="text-base font-semibold text-(--ink)">Sign in to Shopware</h2>
          <p className="mt-1 text-sm text-(--ink-soft)">Sign in before placing an order.</p>
          {!sessionId ? <p role="alert" className="mt-2 text-sm text-(--danger)">Shopping session unavailable. Start the storefront API on port 8004.</p> : null}
          <label className="mt-4 block text-sm text-(--ink)">Email
            <input type="email" required autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} className="mt-1 w-full rounded-lg border border-(--line) bg-(--card) px-3 py-2" />
          </label>
          <label className="mt-3 block text-sm text-(--ink)">Password
            <input type="password" required autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} className="mt-1 w-full rounded-lg border border-(--line) bg-(--card) px-3 py-2" />
          </label>
          {phase === "error" ? <p role="alert" className="mt-2 text-sm text-(--danger)">{error}</p> : null}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" onClick={() => setOpen(false)} className="px-3 py-2 text-sm text-(--ink-soft)">Cancel</button>
            <button type="submit" disabled={phase === "busy"} className="btn-primary px-4 py-2">{phase === "busy" ? "Signing in…" : "Sign in"}</button>
          </div>
        </form>
      </div>
    ) : null}
  </>;
}

export default function Header({
  brand,
  sessionId,
  signedIn,
  onSignIn,
  onSignOut,
  cartCount,
  onOpenCart,
  onOpenOrders,
  onOpenMail,
  assistantOpen,
  onToggleAssistant,
  streaming,
  newMemoryCount,
  onOpenActivity,
}: {
  brand: Brand | null;
  sessionId: string | null;
  signedIn: boolean;
  onSignIn: (email: string, password: string) => Promise<boolean>;
  onSignOut: () => void;
  cartCount: number;
  onOpenCart: () => void;
  onOpenOrders: () => void;
  onOpenMail: () => void;
  assistantOpen: boolean;
  onToggleAssistant: () => void;
  streaming: boolean;
  newMemoryCount: number;
  onOpenActivity: () => void;
}) {
  // Pulse the counter when the cart grows; it is the only visible cue for mid-turn agent adds.
  const previousCountRef = useRef(cartCount);
  const [pulsing, setPulsing] = useState(false);
  useEffect(() => {
    if (cartCount > previousCountRef.current) {
      setPulsing(true);
      const timer = window.setTimeout(() => setPulsing(false), 600);
      previousCountRef.current = cartCount;
      return () => window.clearTimeout(timer);
    }
    previousCountRef.current = cartCount;
  }, [cartCount]);

  return (
    <header className="flex items-center justify-between gap-3 border-b border-(--line) bg-(--card) px-5 py-3">
      <Link href="/" className="flex min-w-0 items-center gap-2.5">
        {brand?.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={brand.logo_url} alt="" className="h-8 w-8 shrink-0 rounded-md object-contain" />
        ) : null}
        <span className="truncate text-lg font-extrabold tracking-tight text-(--ink)">
          {brand?.name ?? "Storefront"}
        </span>
        {brand?.slogan ? (
          <span className="hidden truncate text-[13px] text-(--ink-soft)/80 md:inline">{brand.slogan}</span>
        ) : null}
      </Link>
      <div className="flex shrink-0 items-center gap-2">
        <ShopSignIn sessionId={sessionId} signedIn={signedIn} onSignIn={onSignIn} onSignOut={onSignOut} />
        <ActivityButton streaming={streaming} newMemoryCount={newMemoryCount} onClick={onOpenActivity} />
        <button
          type="button"
          onClick={onOpenMail}
          className="rounded-full border border-(--line) bg-(--card) px-3 py-1 text-[13px] font-semibold text-(--ink) transition hover:border-(--brand)"
        >
          Mail
        </button>
        {signedIn ? (
          <button
            type="button"
            onClick={onOpenOrders}
            className="rounded-full border border-(--line) bg-(--card) px-3 py-1 text-[13px] font-semibold text-(--ink) transition hover:border-(--brand)"
          >
            Orders
          </button>
        ) : null}
        <button
          type="button"
          onClick={onOpenCart}
          aria-label={`Open cart, ${cartCount} item${cartCount === 1 ? "" : "s"}`}
          className="flex items-center gap-1.5 rounded-full border border-(--line) bg-(--card) px-3 py-1 text-[13px] font-semibold text-(--ink) transition hover:border-(--brand)"
        >
          Cart
          <span
            data-cart-target
            className={`rounded-full bg-(--accent-soft) px-1.5 py-0.5 text-[11px] font-bold text-(--ink) ${
              pulsing ? "ac-pop" : ""
            }`}
          >
            {cartCount}
          </span>
        </button>
        <button
          type="button"
          onClick={onToggleAssistant}
          aria-pressed={assistantOpen}
          className={`rounded-full border px-3 py-1 text-[13px] font-semibold transition ${
            assistantOpen
              ? "border-(--brand) bg-(--accent-soft) text-(--ink)"
              : "border-(--line) bg-(--card) text-(--ink) hover:border-(--brand)"
          }`}
        >
          Assistant
        </button>
      </div>
    </header>
  );
}
