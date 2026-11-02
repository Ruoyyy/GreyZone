"""Twitter API v2 本地模拟器。

实现 ZerePy TwitterConnection 实际调用的端点
(src/connections/twitter_connection.py，配合其可选 base_url 配置):

  GET  /2/users/me                                     -> {"data": {id, username}}
  GET  /2/users/{id}/timelines/reverse_chronological    -> {"data": [...], "includes": {...}}
  GET  /2/tweets/search/recent?query=...                -> {"data": [...]}
  POST /2/tweets                                        -> 创建推文/回复
  POST /2/users/{id}/likes                              -> 点赞
  GET/POST /2/tweets/search/stream/rules                -> 流规则（空实现）
  GET  /2/tweets/search/stream                          -> 一条示例推文后关闭
  POST /__admin/tweets                                  -> 测试注入：以任意用户身份发帖/回复（默认 ruoyu）
  POST /__admin/likes                                   -> 测试注入：以任意用户身份点赞/取消（默认 ruoyu）
  GET  /__admin/state                                   -> 全量状态（前端用）
  GET  /                                                -> 可视化前端（static/index.html）

鉴权：只要求存在 Authorization 头（OAuth1 签名不校验——凭据来自 .env 的 mock 值）。
搜索端点支持连接代码会发送的两种 query：
  "from:{username} -is:retweet -is:reply"        （get-latest-tweets）
  "conversation_id:{tweet_id} is:reply"          （get-tweet-replies）
状态存内存，容器重启即重置。预置了 ruoyu/alice 的推文和一条回复作为交互素材。
"""
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="twitter-mock")

# "我" = ZerePy agent admin（与 .env 的 TWITTER_USER_ID / TWITTER_USERNAME 对应）
ME = {"id": "1001", "username": "admin", "name": "admin"}

users = {
    "1001": ME,
    "1002": {"id": "1002", "username": "ruoyu", "name": "Ruoyu"},  # 模拟的人类用户（前端默认身份）
    "1003": {"id": "1003", "username": "alice", "name": "Alice"},
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _tweet(text: str, author_id: str, conversation_id: str | None = None) -> dict:
    return {
        "id": uuid.uuid4().hex[: 19],  # Twitter id 是 19 位数字，长度接近即可
        "text": text,
        "author_id": author_id,
        "created_at": _now(),
        **({"conversation_id": conversation_id} if conversation_id else {}),
    }


t1 = _tweet("gm. anyone else watching the mempool this morning?", "1002")
t2 = _tweet("what is everyone working on this week?", "1003")
t3 = _tweet("always. coffee first though", "1003", conversation_id=t1["id"])

tweets: list[dict] = [t1, t2, t3]  # 时间正序；返回时倒序（最新在前）
likes: dict[str, set[str]] = {}  # tweet_id -> 点赞的 user_id 集合


def _check_auth(authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing Authorization header")


def _newest_first() -> list[dict]:
    return list(reversed(tweets))


def _includes(data: list[dict]) -> dict:
    """author_id expansion：附带推文作者的用户信息。"""
    author_ids = {t["author_id"] for t in data}
    return {
        "users": [
            {"id": users[uid]["id"], "name": users[uid]["name"], "username": users[uid]["username"]}
            for uid in author_ids
        ]
    }


def _uid_of(username: str) -> str:
    uid = next((u["id"] for u in users.values() if u["username"].lower() == username.lower()), None)
    if uid is None:
        known = [u["username"] for u in users.values()]
        raise HTTPException(status_code=404, detail=f"unknown username {username}, try: {known}")
    return uid


def _conversation_of(parent_id: str) -> str:
    """Twitter 语义：回复挂到根帖的 conversation_id。"""
    parent = next((t for t in tweets if t["id"] == parent_id), None)
    if parent is None:
        raise HTTPException(status_code=404, detail="parent tweet not found")
    return parent.get("conversation_id", parent["id"])


class TweetIn(BaseModel):
    text: str
    reply: dict | None = None


class LikeIn(BaseModel):
    tweet_id: str


class AdminTweetIn(BaseModel):
    text: str
    username: str = "ruoyu"  # 以哪个用户身份发帖（模拟"别人"）
    in_reply_to_tweet_id: str | None = None


class AdminLikeIn(BaseModel):
    tweet_id: str
    username: str = "ruoyu"


@app.get("/2/users/me")
def users_me(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    return {"data": {"id": ME["id"], "username": ME["username"]}}


@app.get("/2/users/{user_id}/timelines/reverse_chronological")
def timeline(user_id: str, request: Request, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    try:
        max_results = int(request.query_params.get("max_results", "10"))
    except ValueError:
        max_results = 10
    data = _newest_first()[:max_results]
    return {"data": [dict(t) for t in data], "includes": _includes(data)}


@app.get("/2/tweets/search/recent")
def search_recent(request: Request, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    query = request.query_params.get("query", "")
    try:
        max_results = int(request.query_params.get("max_results", "10"))
    except ValueError:
        max_results = 10

    if "from:" in query:
        username = query.split("from:", 1)[1].split()[0].strip()
        uid = next((u["id"] for u in users.values() if u["username"].lower() == username.lower()), None)
        data = [t for t in _newest_first() if t["author_id"] == uid and "conversation_id" not in t]
    elif "conversation_id:" in query:
        root_id = query.split("conversation_id:", 1)[1].split()[0].strip()
        data = [t for t in _newest_first() if t.get("conversation_id") == root_id]
    else:
        data = _newest_first()

    data = data[:max_results]
    resp = {"data": [dict(t) for t in data]}
    if "author_id" in request.query_params.get("expansions", ""):
        resp["includes"] = _includes(data)  # 与真实 API 一致：请求了 expansions 才返回
    return resp


@app.post("/2/tweets", status_code=201)
def post_tweet(body: TweetIn, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    conversation_id = None
    if body.reply and body.reply.get("in_reply_to_tweet_id"):
        conversation_id = _conversation_of(body.reply["in_reply_to_tweet_id"])
    tweet = _tweet(body.text, ME["id"], conversation_id=conversation_id)
    tweets.append(tweet)
    return {"data": {"id": tweet["id"], "text": tweet["text"]}}


@app.post("/2/users/{user_id}/likes", status_code=201)
def like(user_id: str, body: LikeIn, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    if not any(t["id"] == body.tweet_id for t in tweets):
        raise HTTPException(status_code=404, detail="tweet not found")
    likes.setdefault(body.tweet_id, set()).add(user_id if user_id in users else ME["id"])
    return {"data": {"liked": True}}


# ---- 流式端点（respond-to-mentions 任务用；本地最小实现） ----

@app.get("/2/tweets/search/stream/rules")
def get_rules(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    return {"data": []}


@app.post("/2/tweets/search/stream/rules")
async def post_rules(request: Request, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    body = await request.json()
    return {"data": [{"id": str(i + 1), "value": r.get("value", "")} for i, r in enumerate(body.get("add", []))]}


@app.get("/2/tweets/search/stream")
def stream(authorization: str | None = Header(default=None)):
    _check_auth(authorization)

    def generate():
        import json

        mention = _tweet(f"@{ME['username']} hey, got a minute?", "1003")
        tweets.append(mention)
        yield json.dumps({"data": mention}) + "\r\n"

    return StreamingResponse(generate(), media_type="text/plain")


# ---- 测试注入 / 前端：以任意用户身份操作（默认 ruoyu） ----

@app.post("/__admin/tweets", status_code=201)
def admin_post(body: AdminTweetIn):
    uid = _uid_of(body.username)
    conversation_id = _conversation_of(body.in_reply_to_tweet_id) if body.in_reply_to_tweet_id else None
    tweet = _tweet(body.text, uid, conversation_id=conversation_id)
    tweets.append(tweet)
    return tweet


@app.post("/__admin/likes")
def admin_like(body: AdminLikeIn):
    """切换点赞状态。"""
    uid = _uid_of(body.username)
    if not any(t["id"] == body.tweet_id for t in tweets):
        raise HTTPException(status_code=404, detail="tweet not found")
    likers = likes.setdefault(body.tweet_id, set())
    likers.symmetric_difference_update({uid})
    return {"liked": uid in likers}


@app.get("/__admin/state")
def admin_state():
    return {
        "me": ME["id"],
        "users": users,
        "tweets": tweets,  # 时间正序
        "likes": {tid: sorted(uids) for tid, uids in likes.items() if uids},
    }


# ---- 可视化前端 ----

@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
