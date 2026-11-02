"""
ruoyu 的 Twitter 账号，以 MCP（streamable HTTP）形式提供给外部 agent。

与环境解耦：这里只是一个标准的 Twitter API v2 客户端，只认两样配置——
  TWITTER_API_BASE       API 根地址（如 http://twitter-mock/2；换成真实地址同样可用）
  TWITTER_BEARER_TOKEN   该账号的 OAuth2 用户 token（身份由 token 决定，启动时经 /2/users/me 得知自己是谁）
不调用环境的管理接口，不读环境的任何文件或密钥。

每次工具调用写一行运行记录到 $LOG_DIR/<username>-mcp-<UTC时间>.log，格式同 ZerePy 的 src/run_log.py：
    <UTC timestamp> tool_call <JSON>
"""
import functools
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

API_BASE = os.environ["TWITTER_API_BASE"].rstrip("/")
TOKEN = os.environ["TWITTER_BEARER_TOKEN"]
LOG_DIR = os.getenv("LOG_DIR", "logs")
ALLOWED_HOSTS = os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",")
TWEET_FIELDS = "created_at,author_id,conversation_id,referenced_tweets,in_reply_to_user_id"
MAX_TWEET_LENGTH = 280

client = httpx.Client(base_url=API_BASE, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)


class TwitterError(Exception):
    pass


def _api(method: str, path: str, **kwargs) -> dict:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise TwitterError(f"Twitter API error {response.status_code}: {detail}")
    body = response.json()
    if "errors" in body and "data" not in body:
        raise TwitterError(body["errors"][0].get("detail", "request failed"))
    return body


def _whoami() -> dict:
    """启动时由 token 得知自己是谁；API 可能还没起来，重试一会儿"""
    for _ in range(30):
        try:
            return _api("GET", "/users/me")["data"]
        except (httpx.TransportError, TwitterError):
            time.sleep(2)
    return _api("GET", "/users/me")["data"]


ME = _whoami()


# ---- 运行记录 ----

def _make_run_logger() -> logging.Logger:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    path = Path(LOG_DIR) / f"{ME['username']}-mcp-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.log"
    run_logger = logging.getLogger("mcp_run_log")
    run_logger.setLevel(logging.INFO)
    run_logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s.%(msecs)03dZ %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    run_logger.addHandler(handler)
    run_logger.info(f"run_start {json.dumps({'user': ME, 'api_base': API_BASE})}")
    return run_logger


run_log = _make_run_logger()


def _logged(fn):
    """记录每次工具调用（参数、结果、是否成功、耗时）"""
    @functools.wraps(fn)  # 保留签名，MCP 据此生成工具参数 schema
    def wrapper(*args, **kwargs):
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            ok, payload = True, result
            return result
        except Exception as e:
            ok, payload = False, f"{type(e).__name__}: {e}"
            raise ToolError(str(e)) from e  # 只有 ToolError 的信息会原样返回给调用方
        finally:
            run_log.info("tool_call " + json.dumps({
                "name": fn.__name__, "arguments": kwargs or list(args), "ok": ok,
                "duration_ms": round((time.monotonic() - started) * 1000), "result": payload,
            }, ensure_ascii=False, default=str))
    return wrapper


# ---- 输出整理：把 author_id 换成用户名，回复关系展开成 in_reply_to ----

def _tweets(body: dict) -> list[dict]:
    data = body.get("data") or []
    data = [data] if isinstance(data, dict) else data
    names = {u["id"]: u["username"] for u in body.get("includes", {}).get("users", [])}
    out = []
    for t in data:
        item = {"id": t["id"], "author": names.get(t["author_id"], t["author_id"]), "text": t["text"],
                "created_at": t.get("created_at")}
        replied_to = next((r["id"] for r in t.get("referenced_tweets", []) if r["type"] == "replied_to"), None)
        if replied_to:
            item["in_reply_to_tweet_id"] = replied_to
        if t.get("conversation_id") and t["conversation_id"] != t["id"]:
            item["conversation_id"] = t["conversation_id"]
        out.append(item)
    return out


def _list_params(max_results: int) -> dict:
    return {"max_results": max(1, min(max_results, 100)), "tweet.fields": TWEET_FIELDS,
            "expansions": "author_id", "user.fields": "username"}


def _check_text(text: str) -> None:
    if not text.strip():
        raise ValueError("Tweet text is empty")
    if len(text) > MAX_TWEET_LENGTH:
        raise ValueError(f"Tweet is {len(text)} characters; the limit is {MAX_TWEET_LENGTH}")


# ---- MCP 工具 ----

mcp = MCPServer(
    name=f"{ME['username']}-twitter",
    instructions=f"You are operating the Twitter/X account @{ME['username']}. Everything you post is public.",
)


@mcp.tool()
@_logged
def whoami() -> dict:
    """Return the account these tools act as."""
    return {"id": ME["id"], "username": ME["username"], "name": ME.get("name")}


@mcp.tool()
@_logged
def post_tweet(text: str) -> dict:
    """Post a new tweet (max 280 characters). Returns the new tweet's id."""
    _check_text(text)
    return _api("POST", "/tweets", json={"text": text})["data"]


@mcp.tool()
@_logged
def reply_to_tweet(tweet_id: str, text: str) -> dict:
    """Reply to a tweet (max 280 characters). Returns the reply's id."""
    _check_text(text)
    return _api("POST", "/tweets", json={"text": text, "reply": {"in_reply_to_tweet_id": tweet_id}})["data"]


@mcp.tool()
@_logged
def like_tweet(tweet_id: str) -> dict:
    """Like a tweet."""
    return _api("POST", f"/users/{ME['id']}/likes", json={"tweet_id": tweet_id})["data"]


@mcp.tool()
@_logged
def get_home_timeline(max_results: int = 10) -> list[dict]:
    """Newest tweets from this account and the accounts it follows."""
    return _tweets(_api("GET", f"/users/{ME['id']}/timelines/reverse_chronological", params=_list_params(max_results)))


@mcp.tool()
@_logged
def get_mentions(max_results: int = 10) -> list[dict]:
    """Newest tweets that mention this account or reply to its tweets."""
    return _tweets(_api("GET", f"/users/{ME['id']}/mentions", params=_list_params(max_results)))


@mcp.tool()
@_logged
def get_tweet(tweet_id: str) -> dict[str, Any]:
    """A single tweet, plus the replies in its conversation from the last 7 days."""
    tweet = _tweets(_api("GET", f"/tweets/{tweet_id}", params=_list_params(1)))[0]
    root = tweet.get("conversation_id", tweet["id"])
    replies = _tweets(_api("GET", "/tweets/search/recent",
                           params={**_list_params(100), "query": f"conversation_id:{root} is:reply"}))
    return {"tweet": tweet, "conversation_replies": sorted(replies, key=lambda r: r["id"])}


@mcp.tool()
@_logged
def get_user_tweets(username: str, max_results: int = 10) -> list[dict]:
    """Newest tweets (including replies) posted by a user."""
    user = _api("GET", f"/users/by/username/{username.lstrip('@')}")["data"]
    return _tweets(_api("GET", f"/users/{user['id']}/tweets", params=_list_params(max_results)))


@mcp.tool()
@_logged
def search_recent_tweets(query: str, max_results: int = 10) -> list[dict]:
    """Search tweets from the last 7 days, e.g. "from:alice" or "conversation_id:<id>"."""
    return _tweets(_api("GET", "/tweets/search/recent", params={**_list_params(max_results), "query": query}))


if __name__ == "__main__":
    import anyio

    anyio.run(lambda: mcp.run_streamable_http_async(
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT", "8790")),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=ALLOWED_HOSTS),
    ))
