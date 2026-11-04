#!/usr/bin/env python3
"""Headless login for the local mail-loop (no browser).

Drives the better-auth Google social login against the in-memory Gmail emulator,
which presents an account picker instead of a real consent screen. Selecting the
managed mailbox creates the server-side session and the EmailAccount row that the
rest of the automated setup (oracle-seed, watch) depends on.

Usage:
  python3 scripts/dev-login.py [--app http://localhost:3000] [--email admin@gmail.com]

Env overrides: APP_URL, MANAGED_EMAIL. Exits non-zero if no session is established.
"""
import argparse
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.environ.get("APP_URL", "http://localhost:3000"))
    ap.add_argument("--email", default=os.environ.get("MANAGED_EMAIL", "admin@gmail.com"))
    args = ap.parse_args()
    app = args.app.rstrip("/")
    email = args.email

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def req(url, data=None, headers=None):
        r = urllib.request.Request(url, data=data, headers=headers or {})
        return opener.open(r, timeout=60)

    # Already logged in? (idempotent)
    try:
        body = req(f"{app}/api/auth/get-session").read().decode()
        if body.strip() and body.strip() != "null":
            print(f"[dev-login] already has a session ({email}); nothing to do")
            return 0
    except Exception:
        pass

    # 1. initiate social sign-in -> authorize URL (+ oauth_state cookie)
    resp = req(
        f"{app}/api/auth/sign-in/social",
        data=json.dumps({"provider": "google", "callbackURL": f"{app}/"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    authorize_url = json.load(resp)["url"]
    base = re.match(r"(https?://[^/]+)", authorize_url).group(1)
    if "accounts.google.com" in authorize_url:
        print("[dev-login] ERROR: sign-in points at real Google, not the emulator. "
              "Set GOOGLE_BASE_URL (e.g. http://localhost:4005) in apps/web/.env.", file=sys.stderr)
        return 2

    # 2. fetch the emulator account-picker page
    html = req(authorize_url).read().decode()

    # 3. extract the hidden fields of the form for the managed email, then approve it
    forms = re.split(r"<form", html)
    picked = [f for f in forms if email in f]
    if not picked:
        print(f"[dev-login] ERROR: managed email {email} not offered by the emulator "
              f"account picker (seeded in emulate.local.config.yaml?)", file=sys.stderr)
        return 3
    fields = dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', picked[0]))
    fields["email"] = email
    req(
        f"{base}/o/oauth2/v2/auth/callback",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    # 4. verify a session now exists
    body = req(f"{app}/api/auth/get-session").read().decode()
    if not body.strip() or body.strip() == "null":
        print("[dev-login] ERROR: approval did not establish a session", file=sys.stderr)
        return 4
    print(f"[dev-login] logged in as {email}; session established")
    return 0


if __name__ == "__main__":
    sys.exit(main())
