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
  GET  /__admin/wallets/{username}                      -> 环境侧用户的钱包：地址、余额、相关交易
  POST /__admin/wallets/{username}/transfer             -> 以该用户的私钥签名并发送原生币转账
  GET  /v2/api?module=account&action=txlist&address=... -> Etherscan v2 兼容的地址交易记录（agent 以 api.etherscan.io 访问）
  GET  /                                                -> 可视化前端（static/index.html）

鉴权：只要求存在 Authorization 头（OAuth1 签名不校验——凭据来自 .env 的 mock 值）。
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
    visible = {user_id} | follows.get(user_id, set())  # 与真实 API 一致：首页只含自己和关注的人
    data = [t for t in _newest_first() if t["author_id"] in visible][:max_results]
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

    cutoff = _now_ms() - SEARCH_WINDOW_MS
    data = [t for t in data if _ms_of(t["id"]) >= cutoff][:max_results]
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

        mention = _tweet(f"@{ME['username']} hey, got a minute?", _by_name["alice"])
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


_load_seed_tweets()
