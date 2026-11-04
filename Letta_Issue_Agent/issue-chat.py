#!/usr/bin/env python3
"""issue-chat — chat from the terminal with the @letta-code bot in Gitea issues (zero config).

Usage:
  ./issue-chat.py --new [-t title]    open a new issue and start chatting
  ./issue-chat.py 1                   continue the conversation in issue #1
  ./issue-chat.py --list              list issues

Zero config: no tea CLI, token, config file, or key needed — clone and run.
The built-in default identity is alice, the Gitea collaboration account; every request
uses HTTP basic auth, so Gitea always sees alice's actions. Only the Python 3 stdlib is required.

During a chat: type a message and press Enter to send (the @letta-code prefix is added
automatically); a message starting with [--new] = the bot opens a fresh session; /quit exits.
While waiting for a reply, Ctrl-C only cancels the wait — the background job keeps running,
and unread replies are collected automatically the next time you enter.
"""

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request

# Built-in default identity: the Gitea deployed by this project's docker compose and the alice test account.
DEFAULT_URL = "http://localhost:3000"
DEFAULT_USER = "alice"
DEFAULT_PASSWORD = "alice"
DEFAULT_REPO = "letta-admin/AgentThreatDataset"
BOT = "gitea-actions"
DONE_MARK = "Letta Code finished"
FAIL_MARK = "Letta Code failed"
QUIT = ("/quit", "/q", "quit", "exit")


def api(cfg, method, path, data=None):
    """Call the Gitea API with alice's basic auth."""
    req = urllib.request.Request(
        cfg["url"].rstrip("/") + "/api/v1" + path,
        method=method,
        headers={
            "Authorization": "Basic " + base64.b64encode(
                ("%s:%s" % (cfg["user"], cfg["password"])).encode()).decode(),
            "Content-Type": "application/json",
        },
        data=json.dumps(data).encode() if data is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        sys.exit("API %s %s %s: %s" % (e.code, method, path, e.read().decode("utf-8", "replace")[:300]))


def do_list(cfg, args):
    issues = api(cfg, "GET", "/repos/%s/issues?state=%s&limit=30&type=issues"
                 % (args.repo, args.state)) or []
    if not issues:
        print("(%s has no %s issues)" % (args.repo, args.state))
        return
    print("%-5s %-7s %-10s %-16s %s" % ("#", "State", "Author", "Updated", "Title"))
    for it in sorted(issues, key=lambda x: x["number"]):
        updated = (it.get("updated_at") or "")[:16].replace("T", " ")
        title = (it.get("title") or "")[:44]
        print("%-5s %-7s %-10s %-16s %s" % (it["number"], it["state"],
              it["user"]["login"], updated, title))


def bot_comments(cfg, repo, num):
    cs = api(cfg, "GET", "/repos/%s/issues/%s/comments?limit=50" % (repo, num)) or []
    return [c for c in cs if c["user"]["login"] == BOT]


def clean_body(body):
    """Strip the working-status header, stats-scaffolding footer and hidden metadata; keep only the reply body."""
    body = re.sub(r"<!-- letta-metadata.*?-->", "", body, flags=re.S)
    lines = body.splitlines()
    if lines and lines[0].startswith("**Letta Code"):
        lines = lines[1:]
    body = "\n".join(lines)
    body = body.split("\n🤖")[0]
    return body.strip().strip("-").strip()


def print_reply(c):
    tag = "!! FAILED" if FAIL_MARK in c["body"] else "bot>"
    print("\n%s %s\n" % (tag, clean_body(c["body"]).replace("\n", "\n   ")))


def do_chat(cfg, args):
    num = None
    if args.new:
        title = args.title or input("New issue title: ").strip()
        if not title:
            sys.exit("title must not be empty")
    else:
        num = args.issue
        it = api(cfg, "GET", "/repos/%s/issues/%s" % (args.repo, num))
        print("issue #%s %s (%s)" % (num, it["title"], it["state"]))

    last_id = max((c["id"] for c in bot_comments(cfg, args.repo, num)), default=0) if num else 0
    print("identity: %s @ %s | type a message and press Enter to send; /quit to exit; [--new] prefix = bot opens a new session\n"
          % (cfg["user"], cfg["url"]))

    while True:
        # First collect unread replies from the previous round (e.g. the last wait was interrupted by Ctrl-C)
        if num:
            for c in bot_comments(cfg, args.repo, num):
                if c["id"] > last_id and (DONE_MARK in c["body"] or FAIL_MARK in c["body"]):
                    print_reply(c)
                    last_id = c["id"]
                    break

        try:
            msg = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not msg:
            continue
        if msg in QUIT:
            return
        body = msg if "@letta-code" in msg else "@letta-code " + msg

        if args.new:
            iss = api(cfg, "POST", "/repos/%s/issues" % args.repo, {"title": title, "body": body})
            num = iss["number"]
            args.new = False
            last_id = 0
            print("created issue #%s: %s" % (num, title))
        else:
            api(cfg, "POST", "/repos/%s/issues/%s/comments" % (args.repo, num), {"body": body})

        print("  sent, waiting for reply... (Ctrl-C cancels the wait, the job keeps running)")
        start = time.time()
        got = None
        try:
            while time.time() - start < args.timeout:
                time.sleep(args.interval)
                bots = [c for c in bot_comments(cfg, args.repo, num) if c["id"] > last_id]
                if bots:
                    c = bots[-1]
                    if DONE_MARK in c["body"] or FAIL_MARK in c["body"]:
                        got = c
                        break
                print("\r  waiting %ds" % int(time.time() - start), end="", flush=True)
        except KeyboardInterrupt:
            print("\n  wait cancelled (the background job keeps running); press Enter to fetch unread replies")
            continue
        print("\r" + " " * 24 + "\r", end="")
        if got:
            last_id = got["id"]
            print_reply(got)
        else:
            print("  timed out waiting for a reply; check the Actions page, or re-enter later to collect replies.")


def main():
    ap = argparse.ArgumentParser(description="chat from the terminal with @letta-code in Gitea issues (zero config, identity is always alice)")
    ap.add_argument("issue", nargs="?", type=int, help="issue number (continue a conversation)")
    ap.add_argument("--new", action="store_true", help="open a new issue and start chatting")
    ap.add_argument("-t", "--title", help="new issue title (non-interactive with --new)")
    ap.add_argument("--list", action="store_true", help="list issues and exit")
    ap.add_argument("--state", default="open", choices=["open", "closed", "all"], help="state filter for --list")
    ap.add_argument("--url", default=DEFAULT_URL, help="Gitea URL, default %s" % DEFAULT_URL)
    ap.add_argument("--user", default=DEFAULT_USER, help="Gitea user, default %s" % DEFAULT_USER)
    ap.add_argument("--password", default=DEFAULT_PASSWORD, help="password, default is the project test account")
    ap.add_argument("-r", "--repo", default=DEFAULT_REPO, help="owner/repo")
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for a reply")
    ap.add_argument("--interval", type=int, default=5, help="polling interval in seconds")
    args = ap.parse_args()

    cfg = {"url": args.url, "user": args.user, "password": args.password}
    me = api(cfg, "GET", "/user")
    if me["login"] != cfg["user"]:
        sys.exit("identity check failed: expected %s, got %s" % (cfg["user"], me["login"]))
    if args.list:
        do_list(cfg, args)
        return
    if not args.new and not args.issue:
        ap.error("usage: ./issue-chat.py --new [-t title]  or  ./issue-chat.py <issue-number>  or  --list")
    do_chat(cfg, args)


if __name__ == "__main__":
    main()
