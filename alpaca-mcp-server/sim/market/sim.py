"""Fake Alpaca REST API: stocks, prices and news are all controlled via /admin/*.

Implements just enough of trading-api + market-data-api for alpaca-mcp-server's
core tools. While the market is open, a 60-second ticker moves every listed
stock along a random walk, prints trades (volume) and refreshes the top-of-book
quote. Listing a stock backfills 30 daily bars and one session of minute bars
so history looks real. /admin/price applies a jump on top of the walk, with a
volume burst that decays afterwards. Market orders fill at the quote; limit
orders fill when the quote crosses the limit.
"""
import asyncio
import math
import random
import re
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from seed import SEED_ASSETS, SEED_CRYPTO, SEED_NEWS, SEED_SOCIAL

START_CASH = 100_000.0
SESSION_SECONDS = 6.5 * 3600
TICK_SECONDS = 60                    # market data stays fixed between ticks
DAILY_VOL = 0.02                     # default daily return stdev for the random walk
EXCHANGES = ["Q", "V", "K", "Z", "P", "N", "X"]

def now() -> str:
    return iso(datetime.now(timezone.utc).timestamp())


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(s: str) -> float:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def reset() -> None:
    global ASSETS, CASH, POSITIONS, ORDERS, NEWS, SOCIAL, MARKET_OPEN
    ASSETS = {}      # symbol -> asset dict, see new_asset()
    CASH = START_CASH
    POSITIONS = {}   # symbol -> {"qty", "avg"}
    ORDERS = []
    NEWS = []
    SOCIAL = []      # StockTwits-shaped messages, see social_msg()
    MARKET_OPEN = True
    for sym, name, exchange, price, adv_m, vol, day_chg in SEED_ASSETS:
        ASSETS[sym] = new_asset(sym, name, price, adv_m * 1e6, vol, exchange, day_chg)
    for sym, name, price, adv, vol, day_chg in SEED_CRYPTO:
        ASSETS[sym] = new_asset(sym, name, price, adv, vol, "CRYPTO", day_chg, asset_class="crypto")
    t_now, nid = datetime.now(timezone.utc).timestamp(), 48_310_000
    for hours_ago, symbols, author, headline, summary, paragraphs in SEED_NEWS:
        nid += random.randint(3, 40)
        NEWS.append(news_item(nid, headline, summary,
                              "".join(f"<p>{x}</p>" for x in paragraphs), author, symbols,
                              t_now - hours_ago * 3600))
    sid = 500_000_000
    for minutes_ago, username, name, body, sentiment, symbols in SEED_SOCIAL:
        sid += random.randint(7, 91)
        SOCIAL.append(social_msg(sid, username, name, body, sentiment, symbols, t_now - minutes_ago * 60))


def err(msg: str, code: int = 422) -> JSONResponse:
    return JSONResponse({"code": int(f"{code}10000"), "message": msg}, status_code=code)


# ---------------- market model ----------------
def is_crypto(a: dict) -> bool:
    return a["class"] == "crypto"


def resolve(sym: str) -> str | None:
    """Accept both BTC/USD and BTCUSD for crypto, as Alpaca does."""
    if sym in ASSETS:
        return sym
    alt = sym[:-3] + "/USD" if sym.endswith("USD") else None
    return alt if alt in ASSETS else None


def new_asset(symbol: str, name: str, price: float, adv: float | None = None, vol: float = DAILY_VOL,
              exchange: str = "NASDAQ", day_chg: float | None = None, asset_class: str = "us_equity",
              fresh: bool = False) -> dict:
    adv = adv or random.choice([0.3, 0.8, 1.5, 3, 6]) * 1e6 * random.uniform(0.7, 1.3)   # average daily volume
    crypto = asset_class == "crypto"
    a = {"id": str(uuid.uuid4()), "class": asset_class, "name": name, "exchange": exchange, "price": price,
         "adv": adv, "vol": vol, "heat": 0.0,
         "session": 86400 if crypto else SESSION_SECONDS,             # crypto trades around the clock
         "dp": (2 if price >= 10 else 4) if crypto else (2 if price >= 1 else 4),   # price decimals
         "daily": [], "minute": [], "trades": deque(maxlen=5000), "quotes": deque(maxlen=5000)}
    t_now = datetime.now(timezone.utc).timestamp()

    if fresh:
        # mid-run listing (attack console / panel): a newly deployed asset with no
        # backfilled history and near-zero volume — the tape can't be faked
        a["adv"] = 1000
        m = int(t_now // 60 * 60)
        for k in range(5, 0, -1):
            a["minute"].append(make_bar(a, m - k * 60, price, price, 0.002, a["adv"] / 500))
        a["daily"].append(make_bar(a, day_start(a, date.today()), price, price, 0.002, a["adv"]))
        refresh_quote(a)
        record_trade(a, price, trade_size(a, max(a["adv"] / 100, 1)))
        return a

    # one session (crypto: 24h) of minute bars ending at the listing price (walked backwards)
    # a fixed day change (news-driven move) comes with proportionally heavier volume
    n_min = int(a["session"] // 60)
    sigma_min = vol / math.sqrt(n_min)
    day_vol = adv * (1 + 20 * abs(day_chg or 0))
    c, minutes = price, []
    for k in range(1, n_min + 1):
        o = c * math.exp(-sigma_min * random.gauss(0, 1))
        minutes.append(make_bar(a, int(t_now // 60 * 60 - k * 60), o, c, sigma_min, day_vol / n_min))
        c = o
    a["minute"] = minutes[::-1]

    # 30 prior trading days (crypto: calendar days) ending at yesterday's close (fixed by day_chg, else near today's open)
    prev_close = price / (1 + day_chg) if day_chg is not None else a["minute"][0]["o"] * math.exp(0.3 * vol * random.gauss(0, 1))
    c, days, d = prev_close, [], date.today()
    while len(days) < 30:
        d -= timedelta(days=1)
        if d.weekday() >= 5 and not crypto:
            continue
        o = c * math.exp(-vol * random.gauss(0, 1))
        days.append(make_bar(a, day_start(a, d), o, c, vol, adv))
        c = o
    a["daily"] = days[::-1]
    refresh_quote(a)
    record_trade(a, price, trade_size(a, adv / n_min / 20))
    return a


def day_start(a: dict, d: date) -> int:
    """Daily bar timestamp: crypto days start at UTC midnight, US equity bars are stamped 04:00 UTC."""
    return int(datetime.combine(d, time(0 if is_crypto(a) else 4), timezone.utc).timestamp())


def trade_size(a: dict, size: float) -> float:
    """Stocks print whole shares; crypto prints fractional units."""
    return round(size, 6) if is_crypto(a) else max(1, int(size))


def make_bar(a: dict, t: int, o: float, c: float, sigma: float, avg_vol: float) -> dict:
    dp = a["dp"]
    h = max(o, c) * (1 + abs(random.gauss(0, sigma / 2)))
    l = min(o, c) * (1 - abs(random.gauss(0, sigma / 2)))
    v = avg_vol * random.lognormvariate(0, 0.4)
    n = v * c / random.uniform(500, 5000) if is_crypto(a) else v / random.randint(80, 200)   # avg print size
    return {"t": t, "o": round(o, dp), "h": round(h, dp), "l": round(l, dp), "c": round(c, dp),
            "v": round(v, 4) if is_crypto(a) else int(v), "n": max(1, int(n)), "vw": round((h + l + c) / 3, dp + 2)}


def record_trade(a: dict, price: float, size: float, t: float | None = None) -> None:
    t = t or datetime.now(timezone.utc).timestamp()
    px = round(price, a["dp"])
    if is_crypto(a):
        a["trades"].append({"t": t, "p": px, "s": size, "tks": random.choice("BS"), "i": random.randint(1, 2**40)})
    else:
        a["trades"].append({"t": t, "x": random.choice(EXCHANGES), "p": px, "s": size, "c": ["@"],
                            "i": random.randint(1, 2**40), "z": "C"})
    m = int(t // 60 * 60)
    bar = a["minute"][-1] if a["minute"] else None
    if bar is None or bar["t"] != m:
        bar = {"t": m, "o": px, "h": px, "l": px, "c": px, "v": 0, "n": 0, "vw": px, "_pv": 0.0}
        a["minute"].append(bar)
    bar["_pv"] = bar.get("_pv", bar["vw"] * bar["v"]) + px * size
    bar["h"], bar["l"], bar["c"] = max(bar["h"], px), min(bar["l"], px), px
    bar["v"] += size
    bar["n"] += 1
    bar["vw"] = round(bar["_pv"] / bar["v"], a["dp"] + 2)


def refresh_quote(a: dict) -> None:
    p, dp = a["price"], a["dp"]
    spread = max(10 ** -dp, round(p * random.uniform(0.0002, 0.0012), dp))
    bid = round(p - spread / 2, dp)
    q = {"t": datetime.now(timezone.utc).timestamp(), "bp": bid, "ap": round(bid + spread, dp)}
    if is_crypto(a):     # top-of-book depth worth roughly $10k-$150k per side
        q.update({"bs": round(random.uniform(1e4, 1.5e5) / p, 4), "as": round(random.uniform(1e4, 1.5e5) / p, 4)})
    else:
        q.update({"bs": 100 * random.randint(1, 12), "as": 100 * random.randint(1, 12),
                  "bx": random.choice(EXCHANGES), "ax": random.choice(EXCHANGES), "c": ["R"], "z": "C"})
    a["quotes"].append(q)


def tick(a: dict) -> None:
    # Prints for the whole interval, spread over it so minute bars get a real OHLC path.
    k = random.randint(8, 30)
    sigma = a["vol"] * math.sqrt(TICK_SECONDS / a["session"] / k) * (1 + a["heat"])
    vol = a["adv"] * TICK_SECONDS / a["session"] * (1 + 4 * a["heat"])
    t_now = datetime.now(timezone.utc).timestamp()
    t0 = max(a["trades"][-1]["t"], t_now - TICK_SECONDS)
    for i in range(1, k + 1):
        a["price"] *= math.exp(sigma * random.gauss(0, 1))
        record_trade(a, a["price"], trade_size(a, vol / k * random.lognormvariate(0, 0.8)), t0 + (t_now - t0) * i / k)
    refresh_quote(a)
    a["heat"] *= 0.995 ** TICK_SECONDS              # post-jump excitement decays (~2.5 min half-life)


def jump(symbol: str, price: float) -> None:
    a = ASSETS[symbol]
    change = abs(math.log(price / a["price"]))
    a["price"] = price
    a["heat"] += change * 20
    burst = a["adv"] * change * random.uniform(0.5, 1.5)
    for _ in range(random.randint(5, 15)):
        record_trade(a, price * (1 + random.gauss(0, 0.002)), trade_size(a, burst / 10))
    refresh_quote(a)
    fill_open_orders(symbol)


def today_bar(a: dict) -> dict:
    ms = a["minute"]
    v = sum(b["v"] for b in ms)
    return {"t": day_start(a, date.today()),
            "o": ms[0]["o"], "h": max(b["h"] for b in ms), "l": min(b["l"] for b in ms), "c": ms[-1]["c"],
            "v": round(v, 4), "n": sum(b["n"] for b in ms),
            "vw": round(sum(b["vw"] * b["v"] for b in ms) / max(v, 1e-9), a["dp"] + 2)}


async def ticker() -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        for sym, a in list(ASSETS.items()):
            if not MARKET_OPEN and not is_crypto(a):    # crypto trades 24/7
                continue
            tick(a)
            fill_open_orders(sym)


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(ticker())
    yield
    task.cancel()


# ---------------- orders / positions ----------------
OPEN_STATUSES = ("new", "held")     # held = bracket exit leg waiting for its entry to fill


def new_order(sym: str, side: str, qty, otype: str, tif: str, limit_price=None, stop_price=None,
              client_order_id=None, order_class: str = "simple") -> dict:
    t = now()
    return {"id": str(uuid.uuid4()), "client_order_id": client_order_id or str(uuid.uuid4()),
            "created_at": t, "updated_at": t, "submitted_at": t, "filled_at": None, "canceled_at": None,
            "asset_id": ASSETS[sym]["id"], "symbol": sym, "asset_class": ASSETS[sym]["class"], "qty": str(qty),
            "notional": None,
            "filled_qty": "0", "filled_avg_price": None, "order_class": order_class, "order_type": otype,
            "type": otype, "side": side, "time_in_force": tif, "limit_price": limit_price,
            "stop_price": stop_price, "status": "new", "extended_hours": False, "legs": None,
            "replaced_at": None, "replaced_by": None, "replaces": None}


def order_json(o: dict) -> dict:
    out = {k: v for k, v in o.items() if not k.startswith("_")}
    if o["legs"]:
        out["legs"] = [order_json(leg) for leg in o["legs"]]
    return out


def cancel(o: dict) -> None:
    for x in [o] + (o["legs"] or []):
        if x["status"] in OPEN_STATUSES:
            x.update(status="canceled", canceled_at=now(), updated_at=now())


def fill_open_orders(symbol: str) -> None:
    if not MARKET_OPEN and not is_crypto(ASSETS[symbol]):
        return
    for o in ORDERS:
        if o["symbol"] == symbol and o["status"] == "new":
            try_fill(o)


def try_fill(o: dict) -> None:
    global CASH
    a = ASSETS[o["symbol"]]
    q = a["quotes"][-1]
    px = q["ap"] if o["side"] == "buy" else q["bp"]
    if o["type"] in ("stop", "stop_limit") and not o.get("_triggered"):
        last, stop = a["trades"][-1]["p"], float(o["stop_price"])
        if (o["side"] == "buy" and last < stop) or (o["side"] == "sell" and last > stop):
            return
        o["_triggered"] = True
    if o["type"] in ("limit", "stop_limit"):
        lim = float(o["limit_price"])
        if (o["side"] == "buy" and px > lim) or (o["side"] == "sell" and px < lim):
            return
    qty = float(o["qty"])
    signed = qty if o["side"] == "buy" else -qty
    pos = POSITIONS.setdefault(o["symbol"], {"qty": 0.0, "avg": 0.0})
    if pos["qty"] + signed != 0 and o["side"] == "buy":
        pos["avg"] = (pos["qty"] * pos["avg"] + qty * px) / (pos["qty"] + qty)
    pos["qty"] += signed
    if pos["qty"] == 0:
        del POSITIONS[o["symbol"]]
    CASH -= signed * px
    record_trade(a, px, trade_size(a, qty))
    o.update(status="filled", filled_qty=o["qty"], filled_avg_price=str(px), filled_at=now(), updated_at=now())
    for leg in o["legs"] or []:                     # bracket entry filled: arm the exit legs
        leg["status"] = "new"
    for leg in o["legs"] or []:
        if leg["status"] == "new":
            try_fill(leg)
    if o.get("_parent"):                            # one exit leg filled: cancel the other (OCO)
        cancel(next(x for x in ORDERS if x["id"] == o["_parent"]))


def position_json(sym: str) -> dict:
    a = ASSETS[sym]
    p, px = POSITIONS[sym], round(a["price"], a["dp"])
    return {"asset_id": a["id"], "symbol": sym, "exchange": a["exchange"], "asset_class": a["class"],
            "qty": str(p["qty"]), "qty_available": str(p["qty"]), "side": "long" if p["qty"] > 0 else "short",
            "avg_entry_price": str(round(p["avg"], 4)), "current_price": str(px),
            "market_value": str(round(p["qty"] * px, 2)), "cost_basis": str(round(p["qty"] * p["avg"], 2)),
            "unrealized_pl": str(round(p["qty"] * (px - p["avg"]), 2))}


def asset_json(sym: str) -> dict:
    a = ASSETS[sym]
    out = {"id": a["id"], "class": a["class"], "exchange": a["exchange"], "symbol": sym, "name": a["name"],
           "status": "active", "tradable": True, "marginable": True, "shortable": True, "easy_to_borrow": True,
           "fractionable": True}
    if is_crypto(a):     # crypto is cash-only, long-only on Alpaca
        out.update(marginable=False, shortable=False, easy_to_borrow=False,
                   min_order_size=f"{1 / a['price']:.9f}", min_trade_increment="0.000000001",
                   price_increment=f"{10 ** -a['dp']:.{a['dp']}f}")
    return out


# ---------------- admin (you control the world) ----------------
async def admin_asset(req):
    b = await req.json()
    ASSETS[b["symbol"]] = new_asset(b["symbol"], b.get("name", b["symbol"]), float(b["price"]),
                                    exchange=b.get("exchange", "CRYPTO" if "/" in b["symbol"] else "NASDAQ"),
                                    asset_class=b.get("class", "crypto" if "/" in b["symbol"] else "us_equity"),
                                    fresh=True)
    return JSONResponse(asset_json(b["symbol"]))


async def admin_price(req):
    b = await req.json()
    sym = b["symbol"]
    if sym not in ASSETS:
        return err(f"unknown symbol {sym}", 404)
    price = float(b["price"]) if "price" in b else ASSETS[sym]["price"] * (1 + float(b["pct"]) / 100)
    jump(sym, price)
    return JSONResponse({"symbol": sym, "price": round(ASSETS[sym]["price"], ASSETS[sym]["dp"])})


def news_item(nid: int, headline: str, summary: str, content: str, author: str, symbols: list[str],
              created: float, source: str = "benzinga", url: str | None = None) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")
    return {"id": nid, "headline": headline, "summary": summary, "content": content, "author": author,
            "source": source, "symbols": symbols, "images": [], "created_at": iso(created), "updated_at": iso(created),
            "url": url or f"https://www.benzinga.com/news/{datetime.fromtimestamp(created):%y/%m}/{nid}/{slug}"}


async def admin_news(req):
    b = await req.json()
    nid = max((n["id"] for n in NEWS), default=48_310_000) + random.randint(3, 40)
    item = news_item(nid, b["headline"], b.get("summary", ""), b.get("content", ""),
                     b.get("author", "Benzinga Newsdesk"), b.get("symbols", []),
                     datetime.now(timezone.utc).timestamp(), b.get("source", "benzinga"), b.get("url"))
    NEWS.append(item)
    return JSONResponse(item)


# ---------------- social feed (StockTwits-shaped) ----------------
def social_msg(mid: int, username: str, name: str, body: str, sentiment: str | None,
               symbols: list[str], created: float) -> dict:
    tags = list(dict.fromkeys(symbols + re.findall(r"\$([A-Za-z0-9/]{2,})", body)))
    return {"id": mid, "body": body, "created_at": iso(created), "updated_at": iso(created),
            "user": {"id": 700_000 + mid % 100_000, "username": username, "name": name,
                     "avatar_url": f"https://avatars.stocktwits.com/production/2026/{mid % 99999:05d}/thumb.png",
                     "identity": "User"},
            "entities": {"symbols": [{"symbol": s, "id": 10_000 + i} for i, s in enumerate(tags)]},
            "sentiment": ({"id": 1 if sentiment == "Bullish" else 2, "name": sentiment} if sentiment else None),
            "source": {"id": 1, "title": "StockTwits Web", "url": "https://stocktwits.com"}}


async def admin_social(req):
    b = await req.json()
    mid = max((m["id"] for m in SOCIAL), default=500_000_000) + random.randint(7, 91)
    created = datetime.now(timezone.utc).timestamp() - float(b.get("minutes_ago", 0)) * 60
    item = social_msg(mid, b.get("username", "guest_" + str(mid % 9999)), b.get("name", b.get("username", "")),
                      b["body"], b.get("sentiment"), b.get("symbols", []), created)
    SOCIAL.append(item)
    return JSONResponse({"response": {"status": 200}, "message": item})


async def social_feed(req):
    q = req.query_params
    syms = set(filter(None, q.get("symbols", "").split(",")))
    limit = int(q.get("limit", 30))
    items = [m for m in SOCIAL if not syms or syms & {t["symbol"] for t in m["entities"]["symbols"]}]
    items.sort(key=lambda m: m["created_at"], reverse=True)
    return JSONResponse({"response": {"status": 200}, "messages": items[:limit]})


async def admin_market(req):
    global MARKET_OPEN
    MARKET_OPEN = bool((await req.json())["open"])
    return JSONResponse({"is_open": MARKET_OPEN})


async def admin_reset(req):
    reset()
    return JSONResponse({"ok": True})


async def admin_state(req):
    assets = {}
    for s, a in ASSETS.items():
        q, d = a["quotes"][-1], today_bar(a)
        assets[s] = {"name": a["name"], "class": a["class"], "price": round(a["price"], a["dp"]), "volume": d["v"],
                     "change_pct": _day_change(a)["percent_change"],
                     "bid": q["bp"], "bs": q["bs"], "ask": q["ap"], "as": q["as"]}
    return JSONResponse({"cash": CASH, "assets": assets, "positions": POSITIONS, "orders": ORDERS,
                         "news": NEWS, "social": sorted(SOCIAL, key=lambda m: m["created_at"], reverse=True)})


async def ui(req):
    return FileResponse(Path(__file__).with_name("ui.html"))


# ---------------- trading API ----------------
async def account(req):
    equity = CASH + sum(p["qty"] * ASSETS[s]["price"] for s, p in POSITIONS.items())
    return JSONResponse({"id": "sim-account", "account_number": "PA3SIM0001", "status": "ACTIVE", "currency": "USD",
                         "cash": str(round(CASH, 2)), "buying_power": str(round(CASH, 2)),
                         "equity": str(round(equity, 2)), "last_equity": str(START_CASH),
                         "portfolio_value": str(round(equity, 2)),
                         "pattern_day_trader": False, "trading_blocked": False, "account_blocked": False})


async def orders(req):
    if req.method == "GET":
        q = req.query_params
        status = q.get("status", "open")
        nested = q.get("nested") == "true"
        out = [o for o in ORDERS if (status == "all" or (o["status"] in OPEN_STATUSES) == (status == "open"))
               and not (nested and o.get("_parent"))]
        return JSONResponse([order_json(o) if nested else {**order_json(o), "legs": None} for o in out[::-1]])
    if req.method == "DELETE":
        out = []
        for o in ORDERS:
            if o["status"] in OPEN_STATUSES and not o.get("_parent"):
                cancel(o)
                out.append({"id": o["id"], "status": 200})
        return JSONResponse(out, status_code=207)
    b = await req.json()
    sym, otype, oc = resolve(b["symbol"]), b["type"], b.get("order_class") or "simple"
    if sym is None:
        return err(f"asset \"{b['symbol']}\" not found")
    a = ASSETS[sym]
    if ("qty" in b) == ("notional" in b):
        return err("qty or notional is required" if "qty" not in b else "only one of qty or notional may be specified")
    if otype not in ("market", "limit", "stop", "stop_limit"):
        return err("invalid order type")
    if is_crypto(a):     # Alpaca crypto: market/limit/stop_limit, gtc/ioc, no bracket, no shorting
        if otype == "stop":
            return err("invalid order type for crypto order")
        if b["time_in_force"] not in ("gtc", "ioc"):
            return err("invalid crypto time_in_force")
        if oc != "simple":
            return err("crypto orders only support simple order class")
    if otype in ("limit", "stop_limit") and not b.get("limit_price"):
        return err("limit_price is required")
    if otype in ("stop", "stop_limit") and not b.get("stop_price"):
        return err("stop_price is required")
    if oc not in ("simple", "bracket"):
        return err("invalid order class")
    tp, sl = b.get("take_profit") or {}, b.get("stop_loss") or {}
    if oc == "bracket" and not (tp.get("limit_price") and sl.get("stop_price")):
        return err("bracket orders require take_profit.limit_price and stop_loss.stop_price")
    q = a["quotes"][-1]
    qty = b.get("qty") or round(float(b["notional"]) / (q["ap"] if b["side"] == "buy" else q["bp"]), 9)
    if is_crypto(a) and b["side"] == "sell":
        held = POSITIONS.get(sym, {}).get("qty", 0.0)
        if float(qty) > held + 1e-12:
            base = sym.split("/")[0]
            return err(f"insufficient balance for {base} (requested: {qty}, available: {held})", 403)
    o = new_order(sym, b["side"], qty, otype, b["time_in_force"], b.get("limit_price"), b.get("stop_price"),
                  b.get("client_order_id"), oc)
    o["notional"] = b.get("notional")
    ORDERS.append(o)
    if oc == "bracket":
        exit_side = "sell" if b["side"] == "buy" else "buy"
        legs = [new_order(sym, exit_side, b["qty"], "limit", b["time_in_force"], tp["limit_price"], order_class=oc),
                new_order(sym, exit_side, b["qty"], "stop_limit" if sl.get("limit_price") else "stop",
                          b["time_in_force"], sl.get("limit_price"), sl["stop_price"], order_class=oc)]
        for leg in legs:
            leg.update(status="held", _parent=o["id"])
        o["legs"] = legs
        ORDERS.extend(legs)
    if MARKET_OPEN or is_crypto(a):
        try_fill(o)
    return JSONResponse(order_json(o))


async def order_by_id(req):
    o = next((o for o in ORDERS if o["id"] == req.path_params["order_id"]), None)
    if o is None:
        return err("order not found", 404)
    if req.method == "DELETE":
        if o["status"] not in OPEN_STATUSES:
            return err(f"order is already in \"{o['status']}\" state")
        cancel(o)
        return Response(status_code=204)
    if req.method == "PATCH":
        return replace(o, await req.json())
    return JSONResponse(order_json(o))


def replace(o: dict, b: dict) -> JSONResponse:
    """Alpaca replace: the open order becomes "replaced" and a new order carries the changes."""
    if o["status"] not in OPEN_STATUSES:
        return err(f"order is already in \"{o['status']}\" state")
    a = ASSETS[o["symbol"]]
    if b.get("trail") is not None:
        return err("trail is only valid for trailing_stop orders")
    if b.get("limit_price") is not None and o["type"] not in ("limit", "stop_limit"):
        return err("limit_price is only valid for limit and stop_limit orders")
    if b.get("stop_price") is not None and o["type"] not in ("stop", "stop_limit"):
        return err("stop_price is only valid for stop and stop_limit orders")
    qty = b.get("qty") or o["qty"]
    if float(qty) <= 0 or (not is_crypto(a) and float(qty) % 1):
        return err("qty must be a positive whole number of shares" if not is_crypto(a) else "qty must be positive")
    tif = b.get("time_in_force") or o["time_in_force"]
    if is_crypto(a) and tif not in ("gtc", "ioc"):
        return err("invalid crypto time_in_force")
    if is_crypto(a) and o["side"] == "sell" and float(qty) > POSITIONS.get(o["symbol"], {}).get("qty", 0.0) + 1e-12:
        return err(f"insufficient balance for {o['symbol'].split('/')[0]}", 403)
    r = new_order(o["symbol"], o["side"], qty, o["type"], tif, b.get("limit_price") or o["limit_price"],
                  b.get("stop_price") or o["stop_price"], b.get("client_order_id"), o["order_class"])
    r.update(status=o["status"], replaces=o["id"])          # a held bracket leg stays held
    if o.get("_parent"):                                      # replacing a bracket exit leg
        parent = next(x for x in ORDERS if x["id"] == o["_parent"])
        r["_parent"] = parent["id"]
        parent["legs"] = [r if leg is o else leg for leg in parent["legs"]]
    if o["legs"]:                                             # replacing a bracket entry: legs follow
        r["legs"], o["legs"] = o["legs"], None
        for leg in r["legs"]:
            leg["_parent"] = r["id"]
    o.update(status="replaced", replaced_by=r["id"], replaced_at=now(), updated_at=now())
    ORDERS.append(r)
    if r["status"] == "new" and (MARKET_OPEN or is_crypto(a)):
        try_fill(r)
    return JSONResponse(order_json(r))


async def positions(req):
    if req.method == "DELETE":
        out = []
        for sym in list(POSITIONS):
            o = await _close(sym)
            out.append({"symbol": sym, "status": 200, "body": o})
        return JSONResponse(out, status_code=207)
    return JSONResponse([position_json(s) for s in POSITIONS])


async def _close(sym: str) -> dict:
    p = POSITIONS[sym]
    o = new_order(sym, "sell" if p["qty"] > 0 else "buy", abs(p["qty"]), "market",
                  "gtc" if is_crypto(ASSETS[sym]) else "day", client_order_id="auto-liquidation")
    ORDERS.append(o)
    try_fill(o)
    return order_json(o)


async def position_by_symbol(req):
    sym = resolve(req.path_params["symbol"])
    if sym not in POSITIONS:
        return err("position does not exist", 404)
    if req.method == "DELETE":
        return JSONResponse(await _close(sym))
    return JSONResponse(position_json(sym))


async def clock(req):
    t = datetime.now(timezone.utc)
    nxt = datetime.combine(t.date() + timedelta(days=1), time(13, 30), timezone.utc)
    return JSONResponse({"timestamp": now(), "is_open": MARKET_OPEN,
                         "next_open": iso(nxt.timestamp()), "next_close": iso((t + timedelta(hours=3)).timestamp())})


async def assets(req):
    return JSONResponse([asset_json(s) for s in ASSETS])


async def asset_by_symbol(req):
    sym = resolve(req.path_params["symbol"])
    return JSONResponse(asset_json(sym)) if sym else err("asset not found", 404)


# ---------------- market data API ----------------
def _symbols(req) -> list[str]:
    return [s for s in req.query_params.get("symbols", "").split(",") if s in ASSETS]


def _out(x: dict) -> dict:
    return {k: (iso(v) if k == "t" else v) for k, v in x.items() if not k.startswith("_")}


def _window(req, items, limit_default=1000) -> list[dict]:
    q = req.query_params
    lo = parse_ts(q["start"]) if q.get("start") else -math.inf
    hi = parse_ts(q["end"]) if q.get("end") else math.inf
    out = [x for x in items if lo <= x["t"] <= hi]
    if q.get("sort") == "desc":
        out = out[::-1]
    return [_out(x) for x in out[: int(q.get("limit", limit_default))]]


def _aggregate(bars: list[dict], key) -> list[dict]:
    groups: dict = {}
    for b in bars:
        groups.setdefault(key(b), []).append(b)
    out = []
    for g in groups.values():
        v = sum(b["v"] for b in g)
        out.append({"t": g[0]["t"], "o": g[0]["o"], "h": max(b["h"] for b in g), "l": min(b["l"] for b in g),
                    "c": g[-1]["c"], "v": v, "n": sum(b["n"] for b in g),
                    "vw": round(sum(b["vw"] * b["v"] for b in g) / max(v, 1), 4)})
    return out


def _bars_for(a: dict, timeframe: str) -> list[dict]:
    m = re.fullmatch(r"(\d+)(Min|T|Hour|H|Day|D|Week|W|Month|M)", timeframe or "1Day")
    n, unit = (int(m.group(1)), m.group(2)) if m else (1, "Day")
    if unit in ("Min", "T", "Hour", "H"):
        secs = n * (60 if unit in ("Min", "T") else 3600)
        return _aggregate(a["minute"], lambda b: b["t"] // secs)
    daily = a["daily"] + [today_bar(a)]
    if unit in ("Week", "W"):
        return _aggregate(daily, lambda b: datetime.fromtimestamp(b["t"], timezone.utc).isocalendar()[:2])
    if unit in ("Month", "M"):
        return _aggregate(daily, lambda b: datetime.fromtimestamp(b["t"], timezone.utc).strftime("%Y%m"))
    return _aggregate(daily, lambda b: b["t"] // (n * 86400)) if n > 1 else daily


async def latest_trades(req): return JSONResponse({"trades": {s: _out(ASSETS[s]["trades"][-1]) for s in _symbols(req)}})
async def latest_quotes(req): return JSONResponse({"quotes": {s: _out(ASSETS[s]["quotes"][-1]) for s in _symbols(req)}})
async def latest_bars(req): return JSONResponse({"bars": {s: _out(ASSETS[s]["minute"][-1]) for s in _symbols(req)}})


async def bars(req):
    tf = req.query_params.get("timeframe", "1Day")
    return JSONResponse({"bars": {s: _window(req, _bars_for(ASSETS[s], tf)) for s in _symbols(req)},
                         "next_page_token": None})


async def trades(req):
    return JSONResponse({"trades": {s: _window(req, list(ASSETS[s]["trades"])) for s in _symbols(req)},
                         "next_page_token": None})


async def quotes(req):
    return JSONResponse({"quotes": {s: _window(req, list(ASSETS[s]["quotes"])) for s in _symbols(req)},
                         "next_page_token": None})


async def snapshots(req):
    out = {}
    for s in _symbols(req):
        a = ASSETS[s]
        out[s] = {"latestTrade": _out(a["trades"][-1]), "latestQuote": _out(a["quotes"][-1]),
                  "minuteBar": _out(a["minute"][-1]), "dailyBar": _out(today_bar(a)),
                  "prevDailyBar": _out(a["daily"][-1])}
    return JSONResponse(out)


# ---------------- crypto market data API (v1beta3) ----------------
async def crypto_bars(req):
    tf = req.query_params.get("timeframe", "1Day")
    return JSONResponse({"bars": {s: _window(req, _bars_for(ASSETS[s], tf)) for s in _symbols(req)},
                         "next_page_token": None})


async def crypto_trades(req):
    return JSONResponse({"trades": {s: _window(req, list(ASSETS[s]["trades"])) for s in _symbols(req)},
                         "next_page_token": None})


async def crypto_quotes(req):
    return JSONResponse({"quotes": {s: _window(req, list(ASSETS[s]["quotes"])) for s in _symbols(req)},
                         "next_page_token": None})


async def crypto_latest_bars(req):
    return JSONResponse({"bars": {s: _out(ASSETS[s]["minute"][-1]) for s in _symbols(req)}})


async def crypto_latest_trades(req):
    return JSONResponse({"trades": {s: _out(ASSETS[s]["trades"][-1]) for s in _symbols(req)}})


async def crypto_latest_quotes(req):
    return JSONResponse({"quotes": {s: _out(ASSETS[s]["quotes"][-1]) for s in _symbols(req)}})


async def crypto_orderbooks(req):
    out = {}
    for s in _symbols(req):
        a = ASSETS[s]
        q = a["quotes"][-1]
        step = max(10 ** -a["dp"], round((q["ap"] - q["bp"]) / 2, a["dp"]))
        depth = lambda i: round(random.uniform(1e4, 1.5e5) * (1 + i) / a["price"], 4)   # deeper levels hold more
        out[s] = {"t": now(),
                  "b": [{"p": round(q["bp"] - step * i, a["dp"]), "s": q["bs"] if i == 0 else depth(i)} for i in range(10)],
                  "a": [{"p": round(q["ap"] + step * i, a["dp"]), "s": q["as"] if i == 0 else depth(i)} for i in range(10)]}
    return JSONResponse({"orderbooks": out})


async def crypto_snapshots(req):
    out = {}
    for s in _symbols(req):
        a = ASSETS[s]
        out[s] = {"latestTrade": _out(a["trades"][-1]), "latestQuote": _out(a["quotes"][-1]),
                  "minuteBar": _out(a["minute"][-1]), "dailyBar": _out(today_bar(a)),
                  "prevDailyBar": _out(a["daily"][-1])}
    return JSONResponse(out)


def _day_change(a: dict) -> dict:
    prev, p = a["daily"][-1]["c"], round(a["price"], a["dp"])
    return {"price": p, "change": round(p - prev, a["dp"]), "percent_change": round((p / prev - 1) * 100, 2)}


async def movers(req):
    market = req.path_params["market_type"]
    cls = {"stocks": "us_equity", "crypto": "crypto"}.get(market)
    if cls is None:
        return err("invalid market_type", 400)
    top = int(req.query_params.get("top", 10))
    rows = sorted(({"symbol": s, **_day_change(a)} for s, a in ASSETS.items() if a["class"] == cls),
                  key=lambda r: r["percent_change"])
    return JSONResponse({"gainers": [r for r in rows[::-1] if r["percent_change"] > 0][:top],
                         "losers": [r for r in rows if r["percent_change"] < 0][:top],
                         "market_type": market, "last_updated": now()})


async def most_actives(req):
    q = req.query_params
    key = "trade_count" if q.get("by") == "trades" else "volume"
    rows = [{"symbol": s, "volume": d["v"], "trade_count": d["n"]}
            for s, d in ((s, today_bar(a)) for s, a in ASSETS.items() if a["class"] == "us_equity")]
    rows.sort(key=lambda r: r[key], reverse=True)
    return JSONResponse({"most_actives": rows[: int(q.get("top", 10))], "last_updated": now()})


async def news(req):
    q = req.query_params
    syms = set(filter(None, q.get("symbols", "").split(",")))
    lo = parse_ts(q["start"]) if q.get("start") else -math.inf
    hi = parse_ts(q["end"]) if q.get("end") else math.inf
    items = [n for n in NEWS if (not syms or syms & set(n["symbols"])) and lo <= parse_ts(n["created_at"]) <= hi]
    items.sort(key=lambda n: n["created_at"], reverse=q.get("sort", "desc") == "desc")
    if q.get("include_content") != "true":            # real API omits article bodies unless asked
        items = [{**n, "content": ""} for n in items]
    return JSONResponse({"news": items[: int(q.get("limit", 10))], "next_page_token": None})


async def calendar(req):
    q = req.query_params
    d0 = date.fromisoformat(q["start"][:10]) if q.get("start") else date.today() - timedelta(days=30)
    d1 = date.fromisoformat(q["end"][:10]) if q.get("end") else date.today() + timedelta(days=30)
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    return JSONResponse([{"date": d.isoformat(), "open": "09:30", "close": "16:00", "session_open": "0400",
                          "session_close": "2000",
                          "settlement_date": (d + timedelta(days=3 if d.weekday() == 4 else 1)).isoformat()}
                         for d in days if d.weekday() < 5])


reset()

app = Starlette(lifespan=lifespan, routes=[
    Route("/", ui),
    Route("/admin/asset", admin_asset, methods=["POST"]),
    Route("/admin/price", admin_price, methods=["POST"]),
    Route("/admin/news", admin_news, methods=["POST"]),
    Route("/admin/social", admin_social, methods=["POST"]),
    Route("/admin/market", admin_market, methods=["POST"]),
    Route("/admin/reset", admin_reset, methods=["POST"]),
    Route("/admin/state", admin_state),
    Route("/v2/account", account),
    Route("/v2/orders", orders, methods=["GET", "POST", "DELETE"]),
    Route("/v2/orders/{order_id}", order_by_id, methods=["GET", "DELETE", "PATCH"]),
    Route("/v2/positions", positions, methods=["GET", "DELETE"]),
    Route("/v2/positions/{symbol:path}", position_by_symbol, methods=["GET", "DELETE"]),
    Route("/v2/clock", clock),
    Route("/v2/calendar", calendar),
    Route("/v2/assets", assets),
    Route("/v2/assets/{symbol:path}", asset_by_symbol),
    Route("/v2/stocks/trades/latest", latest_trades),
    Route("/v2/stocks/quotes/latest", latest_quotes),
    Route("/v2/stocks/bars/latest", latest_bars),
    Route("/v2/stocks/bars", bars),
    Route("/v2/stocks/trades", trades),
    Route("/v2/stocks/quotes", quotes),
    Route("/v2/stocks/snapshots", snapshots),
    Route("/v1beta1/screener/{market_type}/movers", movers),
    Route("/v1beta1/screener/stocks/most-actives", most_actives),
    Route("/v1beta1/news", news),
    Route("/api/2/streams/feed", social_feed),
    Route("/v1beta3/crypto/{loc}/bars", crypto_bars),
    Route("/v1beta3/crypto/{loc}/trades", crypto_trades),
    Route("/v1beta3/crypto/{loc}/quotes", crypto_quotes),
    Route("/v1beta3/crypto/{loc}/latest/bars", crypto_latest_bars),
    Route("/v1beta3/crypto/{loc}/latest/trades", crypto_latest_trades),
    Route("/v1beta3/crypto/{loc}/latest/quotes", crypto_latest_quotes),
    Route("/v1beta3/crypto/{loc}/latest/orderbooks", crypto_orderbooks),
    Route("/v1beta3/crypto/{loc}/snapshots", crypto_snapshots),
])
