// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";

import { api, checkSession, fetchAuthStatus, signIn as postSignIn, signOut as postSignOut, syncCatalog } from "./api";

const STORAGE_KEY = "shopware-storefront-session";

export interface StoreSession {
  /** Null while the session is starting or after it failed. */
  sessionId: string | null;
  signedIn: boolean;
  /** Validates Shopware credentials and binds the customer to this session. */
  signIn: (email: string, password: string) => Promise<boolean>;
  signOut: () => Promise<void>;
  /** Reloads the page when the host lost this session or its sign-in no longer matches the page. */
  verify: () => Promise<void>;
}

/**
 * Unlike the shared useSession, the session here survives page navigations:
 * The cart is keyed to the session on the host. The id is kept in sessionStorage and revalidated
 * against /api/auth/status on mount; a dead or unknown id falls back to a fresh session.
 */
export function useStoreSession(): StoreSession {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const stored = window.sessionStorage.getItem(STORAGE_KEY);
      if (stored) {
        api.session = stored;
        const status = await fetchAuthStatus();
        if (status) {
          if (!cancelled) {
            setSessionId(stored);
            setSignedIn(status.signed_in);
          }
          void syncCatalog();
          return;
        }
      }
      api.session = null;
      const started = await api.startSession();
      if (cancelled) return;
      const fresh = started?.sessionId ?? null;
      if (fresh) {
        window.sessionStorage.setItem(STORAGE_KEY, fresh);
        api.session = fresh;
        void syncCatalog();
      }
      setSessionId(fresh);
      setSignedIn(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!sessionId) throw new Error("Shopping session is unavailable. Start the storefront API on port 8004.");
    const status = await postSignIn(email, password);
    if (status?.signed_in) setSignedIn(true);
    return status.signed_in === true;
  }, [sessionId]);

  const signOut = useCallback(async () => {
    const status = await postSignOut();
    if (status?.session_id) {
      api.session = status.session_id;
      window.sessionStorage.setItem(STORAGE_KEY, status.session_id);
      setSessionId(status.session_id);
    }
    if (status) setSignedIn(status.signed_in);
  }, []);

  // The mount effect above rebuilds from the host's truth: a dead id falls back to a guest.
  const verify = useCallback(async () => {
    if (!sessionId) return;
    const status = await checkSession();
    if (status === "gone" || (status && status.signed_in !== signedIn)) window.location.reload();
  }, [sessionId, signedIn]);

  return { sessionId, signedIn, signIn, signOut, verify };
}
