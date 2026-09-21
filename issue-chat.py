#!/usr/bin/env python3
"""issue-chat — 终端与 Gitea issue 里的 @letta-code 机器人对话（零配置）。

用法:
  ./issue-chat.py --new [-t 标题]      新建 issue 并开始对话
  ./issue-chat.py 1                    在 issue #1 下继续对话
  ./issue-chat.py --list               列出 issue

零配置: 不需要 tea、token、配置文件或密钥——克隆即用。
内置默认身份为 Gitea 的协作账号 ruoyu, 所有请求走 HTTP basic 认证,
Gitea 始终收到的是 ruoyu 的操作。仅依赖 Python 3 标准库。

对话中: 直接输入回车发送(自动补 @letta-code); 消息以 [--new] 开头 =
机器人开全新会话; /quit 退出。等待回复时 Ctrl-C 只取消等待,
后台任务继续, 下次进入自动补收未读回复。
"""

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request

# 内置默认身份: 本项目 docker compose 部署的 Gitea 与 ruoyu 测试账号。
DEFAULT_URL = "http://localhost:3000"
DEFAULT_USER = "ruoyu"
DEFAULT_PASSWORD = "ruoyu"
DEFAULT_REPO = "letta-admin/AgentThreatDataset"
BOT = "gitea-actions"
DONE_MARK = "Letta Code finished"
FAIL_MARK = "Letta Code failed"
QUIT = ("/quit", "/q", "quit", "exit")


def api(cfg, method, path, data=None):
    """以 ruoyu 的 basic 认证调用 Gitea API。"""
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
        print("（%s 无 %s 状态 issue）" % (args.repo, args.state))
        return
    print("%-5s %-7s %-10s %-16s %s" % ("#", "状态", "作者", "更新", "标题"))
    for it in sorted(issues, key=lambda x: x["number"]):
        updated = (it.get("updated_at") or "")[:16].replace("T", " ")
        title = (it.get("title") or "")[:44]
        print("%-5s %-7s %-10s %-16s %s" % (it["number"], it["state"],
              it["user"]["login"], updated, title))


def bot_comments(cfg, repo, num):
    cs = api(cfg, "GET", "/repos/%s/issues/%s/comments?limit=50" % (repo, num)) or []
    return [c for c in cs if c["user"]["login"] == BOT]


def clean_body(body):
    """去掉工作状态头、统计脚手架脚注和隐藏元数据, 只留回复正文。"""
    body = re.sub(r"<!-- letta-metadata.*?-->", "", body, flags=re.S)
    lines = body.splitlines()
    if lines and lines[0].startswith("**Letta Code"):
        lines = lines[1:]
    body = "\n".join(lines)
    body = body.split("\n🤖")[0]
    return body.strip().strip("-").strip()


def print_reply(c):
    tag = "!! 失败" if FAIL_MARK in c["body"] else "bot>"
    print("\n%s %s\n" % (tag, clean_body(c["body"]).replace("\n", "\n   ")))


def do_chat(cfg, args):
    num = None
    if args.new:
        title = args.title or input("新 issue 标题: ").strip()
        if not title:
            sys.exit("标题不能为空")
    else:
        num = args.issue
        it = api(cfg, "GET", "/repos/%s/issues/%s" % (args.repo, num))
        print("issue #%s %s (%s)" % (num, it["title"], it["state"]))

    last_id = max((c["id"] for c in bot_comments(cfg, args.repo, num)), default=0) if num else 0
    print("身份: %s @ %s | 输入消息回车发送; /quit 退出; [--new] 开头=机器人开新会话\n"
          % (cfg["user"], cfg["url"]))

    while True:
        # 先收上一轮未读回复(如上次等待被 Ctrl-C 中断)
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
            print("已创建 issue #%s: %s" % (num, title))
        else:
            api(cfg, "POST", "/repos/%s/issues/%s/comments" % (args.repo, num), {"body": body})

        print("  已发送, 等待回复…(Ctrl-C 取消等待, 任务继续)")
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
                print("\r  等待中 %ds" % int(time.time() - start), end="", flush=True)
        except KeyboardInterrupt:
            print("\n  已取消等待(后台任务继续); 回车后可查看未读回复")
            continue
        print("\r" + " " * 24 + "\r", end="")
        if got:
            last_id = got["id"]
            print_reply(got)
        else:
            print("  超时未等到回复; 可到 Actions 页面查看或稍后重进本程序收取。")


def main():
    ap = argparse.ArgumentParser(description="终端与 Gitea issue 里的 @letta-code 对话(零配置, 身份恒为 ruoyu)")
    ap.add_argument("issue", nargs="?", type=int, help="issue 编号(续聊)")
    ap.add_argument("--new", action="store_true", help="新建 issue 并开始对话")
    ap.add_argument("-t", "--title", help="新 issue 标题(--new 时免交互)")
    ap.add_argument("--list", action="store_true", help="列出 issue 后退出")
    ap.add_argument("--state", default="open", choices=["open", "closed", "all"], help="--list 的状态过滤")
    ap.add_argument("--url", default=DEFAULT_URL, help="Gitea 地址, 默认 %s" % DEFAULT_URL)
    ap.add_argument("--user", default=DEFAULT_USER, help="Gitea 用户, 默认 %s" % DEFAULT_USER)
    ap.add_argument("--password", default=DEFAULT_PASSWORD, help="密码, 默认同项目测试账号")
    ap.add_argument("-r", "--repo", default=DEFAULT_REPO, help="owner/repo")
    ap.add_argument("--timeout", type=int, default=600, help="等回复超时秒数")
    ap.add_argument("--interval", type=int, default=5, help="轮询间隔秒数")
    args = ap.parse_args()

    cfg = {"url": args.url, "user": args.user, "password": args.password}
    me = api(cfg, "GET", "/user")
    if me["login"] != cfg["user"]:
        sys.exit("身份校验异常: 期望 %s, 实际 %s" % (cfg["user"], me["login"]))
    if args.list:
        do_list(cfg, args)
        return
    if not args.new and not args.issue:
        ap.error("用法: ./issue-chat.py --new [-t 标题]  或  ./issue-chat.py <issue编号>  或  --list")
    do_chat(cfg, args)


if __name__ == "__main__":
    main()
