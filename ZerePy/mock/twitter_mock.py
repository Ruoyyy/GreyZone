"""Twitter API v2 本地模拟器。

实现 ZerePy TwitterConnection 实际调用的端点
(src/connections/twitter_connection.py，配合其可选 base_url 配置):

  GET  /2/users/me                                     -> {"data": {id, username}}
  GET  /2/users/{id}/timelines/reverse_chronological    -> {"data": [...], "includes": {...}}
  GET  /2/tweets/search/recent?query=...                -> {"data": [...]}
  POST /2/tweets                                        -> 创建推文/回复
  POST /2/users/{id}/likes                              -> 点赞
  GET  /2/users/{id}/mentions                           -> 提到该用户 / 回复该用户的推文
  GET  /2/users/{id}/tweets                             -> 该用户的推文（含回复）
  GET  /2/users/by/username/{username}                  -> 按用户名查用户
  GET  /2/tweets/{id}                                   -> 单条推文
  GET/POST /2/tweets/search/stream/rules                -> 过滤流规则（支持 "@username"，其余操作符忽略）
  GET  /2/tweets/search/stream                          -> 过滤流：长连接，新推文匹配规则即推送，空闲时 20 秒一次心跳
  POST /__admin/tweets                                  -> 测试注入：以任意用户身份发帖/回复（默认 ruoyu）
  POST /__admin/likes                                   -> 测试注入：以任意用户身份点赞/取消（默认 ruoyu）
  GET  /__admin/state                                   -> 全量状态（前端用）
  GET  /__admin/wallets/{username}                      -> 环境侧用户的钱包：地址、余额、相关交易
  POST /__admin/wallets/{username}/transfer             -> 以该用户的私钥签名并发送原生币转账
  GET  /v2/api?module=account&action=txlist&address=... -> Etherscan v2 兼容的地址交易记录（agent 以 api.etherscan.io 访问）
  GET  /                                                -> 可视化前端（static/index.html）

鉴权（按调用者身份执行，与真实 API 一样）：
  Bearer <token>  -> 环境侧用户（users.tokens.json，如 ruoyu；外部 agent 经 ruoyu_mcp 使用）
  其他（OAuth1）   -> ZerePy agent 即 admin（签名不校验，凭据来自 .env）
搜索端点支持连接代码会发送的两种 query：
  "from:{username} -is:retweet -is:reply"        （get-latest-tweets）
  "conversation_id:{tweet_id} is:reply"          （get-tweet-replies）
状态存内存，容器重启即重置。启动时从 seed_tweets.json 载入账号、关注关系和预置推文（背景，非 agent 主要信息源），
其中 chain 类推文的金额/时间取自链上真实交易（chain/seed.py 回放的固定历史）。
与真实 API 一致的细节：推文/用户 id 为 snowflake；首页时间线只含自己和关注的人；search/recent 只覆盖最近 7 天。
"""
import itertools
import json
import os
import queue
import threading
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from eth_account import Account
from eth_utils import to_checksum_address

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="twitter-mock")

SEED_FILE = Path(__file__).parent / "seed_tweets.json"
TWITTER_EPOCH_MS = 1288834974657
WORKER_ID = 377
SEARCH_WINDOW_MS = 7 * 24 * 3600 * 1000  # search/recent 只覆盖最近 7 天
_sequence = itertools.count()


def _snowflake(ms: int, sequence: int) -> str:
    """Twitter snowflake：(毫秒时间 - 纪元) << 22 | 机器号 << 12 | 序号"""
    return str(((ms - TWITTER_EPOCH_MS) << 22) | (WORKER_ID << 12) | (sequence % 4096))


def _ms_of(tweet_id: str) -> int:
    return (int(tweet_id) >> 22) + TWITTER_EPOCH_MS


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{_now_ms() % 1000:03d}Z"


def _tweet(text: str, author_id: str, conversation_id: str | None = None, ms: int | None = None) -> dict:
    ms = _now_ms() if ms is None else ms
    return {
        "id": _snowflake(ms, next(_sequence)),
        "text": text,
        "author_id": author_id,
        "created_at": datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z",
        **({"conversation_id": conversation_id} if conversation_id else {}),
    }


# 账号与关注关系（用户 id 由注册日期生成 snowflake，固定不变；admin 的 id 需与 .env 的 TWITTER_USER_ID 一致）
seed_doc = json.load(open(SEED_FILE))
users: dict[str, dict] = {}
for i, acct in enumerate(seed_doc["accounts"]):
    joined_ms = int(datetime.fromisoformat(acct["joined"]).replace(tzinfo=timezone.utc).timestamp() * 1000)
    uid = _snowflake(joined_ms, i)
    users[uid] = {"id": uid, "username": acct["username"], "name": acct["name"]}
_by_name = {u["username"]: uid for uid, u in users.items()}
ME = users[_by_name["admin"]]  # "我" = ZerePy agent（与 .env 的 TWITTER_USER_ID / TWITTER_USERNAME 对应）
follows: dict[str, set[str]] = {_by_name[u]: {_by_name[f] for f in fs} for u, fs in seed_doc["follows"].items()}

tweets: list[dict] = []  # 时间正序；返回时倒序（最新在前）。预置推文在文件末尾载入
likes: dict[str, set[str]] = {}  # tweet_id -> 点赞的 user_id 集合
parent_of: dict[str, str] = {}  # 回复 -> 直接父推文；只在请求 referenced_tweets / in_reply_to_user_id 时输出

# 环境侧用户的 OAuth2 用户 token（ruoyu_mcp 等外部客户端用 Bearer 鉴权）
USERS_TOKENS = os.getenv("USERS_TOKENS", "/app/users.tokens.json")
token_users: dict[str, str] = (
    {token: _by_name[name] for name, token in json.load(open(USERS_TOKENS)).items()} if Path(USERS_TOKENS).exists() else {}
)


def _check_auth(authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing Authorization header")


def _caller(authorization: str | None) -> str:
    """调用者的 user id：Bearer token 对应环境侧用户，其余（OAuth1）为 agent 即 admin"""
    _check_auth(authorization)
    if authorization.startswith("Bearer "):
        uid = token_users.get(authorization.removeprefix("Bearer ").strip())
        if uid is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return uid
    return ME["id"]


def _require_self(user_id: str, caller: str) -> None:
    if user_id != caller:
        raise HTTPException(status_code=403, detail="You are not permitted to perform this action on behalf of another user.")


def _render(tweet: dict, request: Request) -> dict:
    """按 tweet.fields 附加回复关系字段（与真实 API 一致：不请求就不返回）"""
    out = dict(tweet)
    fields = request.query_params.get("tweet.fields", "")
    parent = parent_of.get(tweet["id"])
    if parent and "referenced_tweets" in fields:
        out["referenced_tweets"] = [{"type": "replied_to", "id": parent}]
    if parent and "in_reply_to_user_id" in fields:
        out["in_reply_to_user_id"] = next(t["author_id"] for t in tweets if t["id"] == parent)
    return out


def _page(data: list[dict], request: Request) -> dict:
    resp = {"data": [_render(t, request) for t in data]}
    if "author_id" in request.query_params.get("expansions", ""):
        resp["includes"] = _includes(data)
    return resp


def _max_results(request: Request) -> int:
    try:
        return int(request.query_params.get("max_results", "10"))
    except ValueError:
        return 10


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
    me = users[_caller(authorization)]
    return {"data": {"id": me["id"], "username": me["username"], "name": me["name"]}}


@app.get("/2/users/{user_id}/timelines/reverse_chronological")
def timeline(user_id: str, request: Request, authorization: str | None = Header(default=None)):
    _require_self(user_id, _caller(authorization))
    try:
        max_results = int(request.query_params.get("max_results", "10"))
    except ValueError:
        max_results = 10
    visible = {user_id} | follows.get(user_id, set())  # 与真实 API 一致：首页只含自己和关注的人
    data = [t for t in _newest_first() if t["author_id"] in visible][:max_results]
    return {"data": [_render(t, request) for t in data], "includes": _includes(data)}


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

    cutoff = _now_ms() - SEARCH_WINDOW_MS
    data = [t for t in data if _ms_of(t["id"]) >= cutoff][:max_results]
    return _page(data, request)  # 与真实 API 一致：请求了 expansions 才返回 includes


@app.post("/2/tweets", status_code=201)
def post_tweet(body: TweetIn, authorization: str | None = Header(default=None)):
    author = _caller(authorization)
    parent = (body.reply or {}).get("in_reply_to_tweet_id")
    tweet = _tweet(body.text, author, conversation_id=_conversation_of(parent) if parent else None)
    _add_tweet(tweet, parent)
    return {"data": {"id": tweet["id"], "text": tweet["text"]}}


@app.post("/2/users/{user_id}/likes", status_code=201)
def like(user_id: str, body: LikeIn, authorization: str | None = Header(default=None)):
    _require_self(user_id, _caller(authorization))
    if not any(t["id"] == body.tweet_id for t in tweets):
        raise HTTPException(status_code=404, detail="tweet not found")
    likes.setdefault(body.tweet_id, set()).add(user_id)
    return {"data": {"liked": True}}


@app.get("/2/users/by/username/{username}")
def user_by_username(username: str, authorization: str | None = Header(default=None)):
    _caller(authorization)
    u = next((u for u in users.values() if u["username"].lower() == username.lower()), None)
    if u is None:  # 真实 API：200 + errors
        return {"errors": [{"value": username, "detail": f"Could not find user with username: [{username}].",
                            "title": "Not Found Error", "resource_type": "user", "parameter": "username",
                            "type": "https://api.twitter.com/2/problems/resource-not-found"}]}
    return {"data": dict(u)}


@app.get("/2/users/{user_id}/tweets")
def user_tweets(user_id: str, request: Request, authorization: str | None = Header(default=None)):
    _caller(authorization)
    return _page([t for t in _newest_first() if t["author_id"] == user_id][:_max_results(request)], request)


@app.get("/2/users/{user_id}/mentions")
def mentions(user_id: str, request: Request, authorization: str | None = Header(default=None)):
    """提到 @username 的推文，以及对该用户推文的回复（真实回复会自动带上 @ 被回复者）"""
    _caller(authorization)
    if user_id not in users:
        raise HTTPException(status_code=404, detail="user not found")
    handle = "@" + users[user_id]["username"].lower()
    authors = {t["id"]: t["author_id"] for t in tweets}
    data = [t for t in _newest_first() if t["author_id"] != user_id
            and (handle in t["text"].lower() or authors.get(parent_of.get(t["id"])) == user_id)]
    return _page(data[:_max_results(request)], request)


# ---- 过滤流（与真实 API 一致：规则 + 长连接推送；ZerePy 的 stream_tweets / respond-to-mentions 使用） ----

stream_rules: list[dict] = []  # [{"id", "value"}]
stream_subscribers: list[queue.Queue] = []
stream_lock = threading.Lock()
_rule_ids = itertools.count(1)
HEARTBEAT_SECONDS = 20


def _rule_matches(rule: str, tweet: dict) -> bool:
    """只实现 "@username"：推文文本提到该用户，或回复该用户的推文（真实回复自动带 @ 被回复者；
    回复自己时不带，故排除作者本人，否则 agent 回复自己的推文会唤醒自己）"""
    handles = [w[1:].lower() for w in rule.split() if w.startswith("@")]
    if not handles:
        return False
    author = users[tweet["author_id"]]["username"].lower()
    parent = parent_of.get(tweet["id"])
    parent_author = next((users[t["author_id"]]["username"].lower() for t in tweets if t["id"] == parent), None)
    return all(f"@{h}" in tweet["text"].lower() or (parent_author == h and author != h) for h in handles)


def _add_tweet(tweet: dict, parent_id: str | None = None) -> None:
    """新推文入库，并推送给匹配规则的过滤流连接"""
    tweets.append(tweet)
    if parent_id:
        parent_of[tweet["id"]] = parent_id
    with stream_lock:
        matched = [{"id": r["id"], "tag": r["value"]} for r in stream_rules if _rule_matches(r["value"], tweet)]
        if matched:
            event = json.dumps({"data": {"id": tweet["id"], "text": tweet["text"], "edit_history_tweet_ids": [tweet["id"]]},
                                "matching_rules": matched})
            for q in stream_subscribers:
                q.put(event)


@app.get("/2/tweets/search/stream/rules")
def get_rules(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    with stream_lock:
        if not stream_rules:
            return {"meta": {"sent": _now_iso(), "result_count": 0}}
        return {"data": list(stream_rules), "meta": {"sent": _now_iso(), "result_count": len(stream_rules)}}


@app.post("/2/tweets/search/stream/rules")
async def post_rules(request: Request, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    body = await request.json()
    with stream_lock:
        if "delete" in body:
            ids = set(body["delete"].get("ids", []))
            stream_rules[:] = [r for r in stream_rules if r["id"] not in ids]
            return {"meta": {"sent": _now_iso(), "summary": {"deleted": len(ids), "not_deleted": 0}}}
        added = [{"id": str(next(_rule_ids)), "value": r.get("value", "")} for r in body.get("add", [])]
        stream_rules.extend(added)
    return {"data": added, "meta": {"sent": _now_iso(), "summary": {"created": len(added), "not_created": 0}}}


@app.get("/2/tweets/search/stream")
def stream(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    q: queue.Queue = queue.Queue()
    with stream_lock:
        stream_subscribers.append(q)

    def generate():
        try:
            while True:
                try:
                    yield q.get(timeout=HEARTBEAT_SECONDS) + "\r\n"
                except queue.Empty:
                    yield "\r\n"  # keep-alive
        finally:
            with stream_lock:
                stream_subscribers.remove(q)

    return StreamingResponse(generate(), media_type="application/json")


@app.get("/2/tweets/{tweet_id}")
def get_tweet(tweet_id: str, request: Request, authorization: str | None = Header(default=None)):
    _caller(authorization)
    tweet = next((t for t in tweets if t["id"] == tweet_id), None)
    if tweet is None:  # 真实 API：200 + errors
        return {"errors": [{"value": tweet_id, "detail": f"Could not find tweet with id: [{tweet_id}].",
                            "title": "Not Found Error", "resource_type": "tweet", "parameter": "id",
                            "type": "https://api.twitter.com/2/problems/resource-not-found"}]}
    resp = _page([tweet], request)
    resp["data"] = resp["data"][0]
    return resp


# ---- 测试注入 / 前端：以任意用户身份操作（默认 ruoyu） ----

@app.post("/__admin/tweets", status_code=201)
def admin_post(body: AdminTweetIn):
    uid = _uid_of(body.username)
    conversation_id = _conversation_of(body.in_reply_to_tweet_id) if body.in_reply_to_tweet_id else None
    tweet = _tweet(body.text, uid, conversation_id=conversation_id)
    _add_tweet(tweet, body.in_reply_to_tweet_id)
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


# ---- 环境侧用户钱包（ruoyu/alice 的私钥只在这里，agent 容器里没有） ----

RPC_URL = os.getenv("RPC_URL", "http://chain:80")
USERS_KEYS = os.getenv("USERS_KEYS", "/app/users.keys.json")
wallet_keys: dict[str, dict] = json.load(open(USERS_KEYS)) if Path(USERS_KEYS).exists() else {}


def _rpc(method: str, *params):
    req = urllib.request.Request(RPC_URL, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=10))
    if "error" in resp:
        raise HTTPException(status_code=400, detail=resp["error"].get("message", str(resp["error"])))
    return resp["result"]


def _mon(wei_hex: str) -> str:
    return str(Decimal(int(wei_hex, 16)) / Decimal(10**18))


def _address_txs(address: str, limit: int) -> list[dict]:
    """某地址的交易（新到旧，含失败的）。链从高块号开始、历史有数万块，不能逐块扫描；
    用 anvil 实现的 Otterscan 地址索引接口（ots_searchTransactionsBefore）分页取"""
    out, before, blocks = [], 0, {}
    while len(out) < limit:
        page = _rpc("ots_searchTransactionsBefore", address, before, 25)
        for tx, receipt in zip(page["txs"], page["receipts"]):
            n = int(tx["blockNumber"], 16)
            if "timestamp" in receipt:
                ts = int(receipt["timestamp"], 16) if isinstance(receipt["timestamp"], str) else receipt["timestamp"]
            else:
                ts = blocks.setdefault(n, int(_rpc("eth_getBlockByNumber", hex(n), False)["timestamp"], 16))
            out.append({
                "hash": tx["hash"], "from": tx["from"].lower(), "to": (tx["to"] or "").lower(),
                # anvil 会把余额不足的交易打包为失败（status 0），而不是在提交时拒绝；按回执区分
                "value": int(tx["value"], 16), "amount": _mon(tx["value"]), "ok": receipt["status"] == "0x1",
                "block": n, "block_hash": tx["blockHash"], "timestamp": ts,
                "nonce": int(tx["nonce"], 16), "gas": int(tx["gas"], 16), "gas_price": int(tx["gasPrice"], 16),
                "gas_used": int(receipt["gasUsed"], 16), "input": tx["input"],
            })
        if page["lastPage"] or not page["txs"]:
            break
        before = int(page["txs"][-1]["blockNumber"], 16)
    return out[:limit]


def _wallet_of(username: str) -> dict:
    if username not in wallet_keys:
        raise HTTPException(status_code=404, detail=f"no wallet for {username}, have: {list(wallet_keys)}")
    return wallet_keys[username]


class TransferIn(BaseModel):
    to: str
    amount: str  # MON，字符串避免浮点误差


@app.get("/__admin/wallets/{username}")
def wallet(username: str):
    address = _wallet_of(username)["address"]
    return {
        "username": username,
        "address": address,
        "balance": _mon(_rpc("eth_getBalance", address, "latest")),
        "transfers": [t for t in _address_txs(address, 50) if t["value"] > 0],
    }


@app.post("/__admin/wallets/{username}/transfer")
def transfer(username: str, body: TransferIn):
    key = _wallet_of(username)
    try:
        value = int(Decimal(body.amount) * 10**18)
    except InvalidOperation:
        raise HTTPException(status_code=400, detail=f"invalid amount: {body.amount}")
    if value <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    try:
        to = to_checksum_address(body.to)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid address: {body.to}")

    gas_price = int(_rpc("eth_gasPrice"), 16)
    # 与真实钱包一致：余额不够 value + gas 时不发送
    balance = int(_rpc("eth_getBalance", key["address"], "latest"), 16)
    if balance < value + 21000 * gas_price:
        raise HTTPException(status_code=400, detail=f"insufficient funds: balance {_mon(hex(balance))} MON")

    tx = {
        "to": to,
        "value": value,
        "gas": 21000,
        "gasPrice": gas_price,
        "nonce": int(_rpc("eth_getTransactionCount", key["address"], "pending"), 16),
        "chainId": int(_rpc("eth_chainId"), 16),
    }
    signed = Account.sign_transaction(tx, key["private_key"])
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = _rpc("eth_sendRawTransaction", "0x" + raw.hex().removeprefix("0x"))

    receipt = None
    for _ in range(50):  # 最多等 5 秒出块
        receipt = _rpc("eth_getTransactionReceipt", tx_hash)
        if receipt:
            break
        time.sleep(0.1)
    if receipt is None:
        raise HTTPException(status_code=504, detail=f"transaction {tx_hash} not mined yet")
    if receipt["status"] != "0x1":
        raise HTTPException(status_code=400, detail=f"transaction {tx_hash} failed on chain")
    return {"hash": tx_hash, "from": key["address"], "to": to, "amount": body.amount, "block": int(receipt["blockNumber"], 16)}


# ---- 区块浏览器 API（Etherscan v2 兼容，仅 txlist） ----
# 标准 RPC 不支持"按地址列交易"，真实世界靠浏览器的索引 API；这里由 anvil 的 Otterscan 索引提供数据

def _etherscan_error(message: str, result):
    return {"status": "0", "message": message, "result": result}


@app.get("/v2/api")
def etherscan(request: Request):
    q = request.query_params
    if not q.get("apikey"):
        return _etherscan_error("NOTOK", "Missing/Invalid API Key")
    if q.get("chainid") != "10143":
        return _etherscan_error("NOTOK", "Missing or unsupported chainid parameter")
    if (q.get("module"), q.get("action")) != ("account", "txlist"):
        return _etherscan_error("NOTOK", "Error! Missing Or invalid Module name")
    try:
        address = to_checksum_address(q.get("address", "")).lower()
    except ValueError:
        return _etherscan_error("NOTOK", "Error! Invalid address format")

    latest = int(_rpc("eth_blockNumber"), 16)
    page, offset = int(q.get("page", 1)), int(q.get("offset", 10000))
    txs = _address_txs(address, 10000)  # 新到旧
    if q.get("sort", "asc") != "desc":
        txs.reverse()
    txs = txs[(page - 1) * offset: page * offset]
    if not txs:
        return _etherscan_error("No transactions found", [])
    return {"status": "1", "message": "OK", "result": [{
        "blockNumber": str(t["block"]), "timeStamp": str(t["timestamp"]), "hash": t["hash"], "nonce": str(t["nonce"]),
        "blockHash": t["block_hash"], "from": t["from"], "to": t["to"], "value": str(t["value"]),
        "gas": str(t["gas"]), "gasPrice": str(t["gas_price"]), "isError": "0" if t["ok"] else "1",
        "txreceipt_status": "1" if t["ok"] else "0", "input": t["input"], "contractAddress": "",
        "gasUsed": str(t["gas_used"]), "confirmations": str(latest - t["block"] + 1),
    } for t in txs]}


# ---- 可视化前端 ----

@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# ---- 预置推文（启动时载入；依赖上面的链查询，故放在文件末尾） ----

def _load_seed_tweets() -> None:
    """按 seed_tweets.json 生成预置推文：时间相对启动时刻；chain 类推文的金额与时间取自链上真实交易。"""
    import hashlib
    import chain_seed  # chain/seed.py，挂载进来以复用确定性的钱包派生

    now = _now_ms()
    specs = {t["key"]: t for t in seed_doc["tweets"]}
    when: dict[str, int] = {}
    texts: dict[str, str] = {}

    def outgoing(wallet: str) -> list[dict]:
        address = Account.from_key(chain_seed.derived_key(wallet)).address.lower()
        return sorted((t for t in _address_txs(address, 500) if t["from"] == address), key=lambda t: t["block"])

    def jitter(key: str, span_ms: int) -> int:
        """按 key 固定的抖动：否则所有推文与启动时刻的分/秒/毫秒完全对齐，形成指纹"""
        return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % span_ms

    def resolve(key: str) -> int:
        if key in when:
            return when[key]
        spec = specs[key]
        texts[key] = spec["text"]
        if "hours_ago" in spec:
            ms = now - int(spec["hours_ago"] * 3600_000) - jitter(key, 600_000)
        elif "days_ago" in spec:
            ms = now - int(spec["days_ago"] * 86400_000) - jitter(key, 6 * 3600_000)
        elif "chain" in spec:
            tx = outgoing(spec["chain"]["wallet"])[spec["chain"]["out"]]
            ms = tx["timestamp"] * 1000 + spec["chain"]["after_minutes"] * 60_000 + jitter(key, 60_000)
            texts[key] = spec["text"].format(amount=tx["amount"])
        else:
            ms = resolve(spec["reply_to"]) + spec["after_minutes"] * 60_000 + jitter(key, 60_000)
        when[key] = ms
        return ms

    for key in specs:
        resolve(key)

    ids: dict[str, str] = {}
    for key in sorted(specs, key=lambda k: when[k]):  # 父推文一定更早，按时间顺序创建即可拿到父 id
        spec = specs[key]
        conversation_id = _conversation_of(ids[spec["reply_to"]]) if "reply_to" in spec else None
        tweet = _tweet(texts[key], _by_name[spec["author"]], conversation_id=conversation_id, ms=when[key])
        tweets.append(tweet)
        ids[key] = tweet["id"]
        if "reply_to" in spec:
            parent_of[tweet["id"]] = ids[spec["reply_to"]]


_load_seed_tweets()
