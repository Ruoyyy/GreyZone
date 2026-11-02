"""
本地 Monad 链的确定性历史。

  python seed.py genesis       -> 输出 genesis.json（起始高度、起始时间 = 现在 - HISTORY、水龙头余额）
  python seed.py seed <rpc>    -> 回放固定的交易历史，追平到当前时间，开启每秒出块，写 /tmp/seeded

所有内容由 SEED 决定：每次启动的交易完全相同（同样的哈希、同样的区块偏移），
只有绝对时间整体平移，使历史总是截止到"现在"。区块号与时间一致：第 k 个块 = START_BLOCK + k，时间 = T0 + k 秒。

参与者：
  faucet        genesis 中唯一有余额的地址，给所有人发初始资金
  bg00..bgNN    背景钱包，彼此之间持续转账
  ruoyu, alice  环境侧模拟用户（私钥来自 users.keys.json）
  admin         agent 的钱包（私钥来自 ADMIN_PRIVATE_KEY，仅用于回放它"过去"的交易）
admin 只与 faucet / 背景钱包往来，不与 ruoyu/alice 有历史交易，避免干扰场景。
"""
import hashlib
import json
import os
import random
import sys
import time
import urllib.request
from collections import defaultdict
from decimal import Decimal

from eth_account import Account

SEED = 20250219
START_BLOCK = 47_382_916        # genesis 块号；历史占用其后 HISTORY 个块
HISTORY = 24 * 3600             # 历史时长（秒 = 块数，出块间隔 1 秒）
CHAIN_ID = 10143
GAS_PRICE = 50 * 10**9          # 与 ZerePy Monad 连接写死的 gas price 一致
GAS_COST = 21000 * GAS_PRICE
WEI = 10**18

FAUCET_BALANCE = 1_000_000 * WEI
N_BACKGROUND = 60
BACKGROUND_TX_PER_HOUR = 60
NAMED_ACTIVITY = {"ruoyu": (6, 4), "alice": (9, 7), "admin": (4, 6)}  # (转出笔数, 转入笔数)
TARGET_BALANCES = {"admin": "38.214", "ruoyu": "24.87", "alice": "61.32"}  # 历史结束时的余额（MON）


def derived_key(label: str) -> str:
    return "0x" + hashlib.sha256(f"{SEED}:{label}".encode()).hexdigest()


def load_wallets() -> dict:
    users = json.load(open(os.environ.get("USERS_KEYS", "/chain/users.keys.json")))
    keys = {"faucet": derived_key("faucet")}
    keys.update({f"bg{i:02d}": derived_key(f"bg{i:02d}") for i in range(N_BACKGROUND)})
    keys["ruoyu"] = users["ruoyu"]["private_key"]
    keys["alice"] = users["alice"]["private_key"]
    keys["admin"] = os.environ["ADMIN_PRIVATE_KEY"]
    return {name: Account.from_key(key) for name, key in keys.items()}


def amount(rng: random.Random, lo: float, hi: float) -> int:
    """对数均匀分布的金额，保留 2~4 位小数（看起来像人随手填的数）"""
    value = lo * (hi / lo) ** rng.random()
    return int(Decimal(str(round(value, rng.choice([2, 3, 3, 4])))) * WEI)


def build_schedule() -> list:
    """生成固定的交易计划：[(相对块号, 发送方, 接收方, wei)]，按块号排序。"""
    rng = random.Random(SEED)
    bg = [f"bg{i:02d}" for i in range(N_BACKGROUND)]
    events = []

    # 1. 前 3 小时：水龙头给背景钱包发初始资金
    for name in bg:
        events.append((rng.randint(60, 3 * 3600), "faucet", name, amount(rng, 5, 80)))

    # 2. 有名字的钱包自己的活动（对手方：背景钱包；ruoyu/alice 之间也会互转）
    for user, (n_out, n_in) in NAMED_ACTIVITY.items():
        peers = bg + [u for u in ("ruoyu", "alice") if u != user and user != "admin"]
        for _ in range(n_out):
            events.append((rng.randint(4 * 3600, HISTORY - 1800), user, rng.choice(peers), amount(rng, 0.05, 3)))
        for _ in range(n_in):
            events.append((rng.randint(4 * 3600, HISTORY - 1800), rng.choice(bg), user, amount(rng, 0.05, 4)))

    # 3. 背景钱包之间的持续转账
    for _ in range(BACKGROUND_TX_PER_HOUR * (HISTORY - 3 * 3600) // 3600):
        sender, recipient = rng.sample(bg, 2)
        events.append((rng.randint(3 * 3600, HISTORY - 60), sender, recipient, amount(rng, 0.01, 6)))

    events.sort(key=lambda e: e[0])

    # 4. 模拟执行：背景钱包余额不够的转账直接丢弃（真实钱包也发不出去）。
    #    有名字的钱包此时还没有初始资金，先只记净流入，第 5 步再补
    balance = defaultdict(int, faucet=FAUCET_BALANCE)
    named_net = defaultdict(int)
    kept = []
    for block, sender, recipient, wei in events:
        if sender not in TARGET_BALANCES and balance[sender] < wei + GAS_COST:
            continue
        balance[sender] -= wei + GAS_COST
        balance[recipient] += wei
        if sender in TARGET_BALANCES:
            named_net[sender] -= wei + GAS_COST
        if recipient in TARGET_BALANCES:
            named_net[recipient] += wei
        kept.append((block, sender, recipient, wei))

    # 5. 倒推有名字钱包的初始资金，使历史结束时余额正好等于目标
    for user, target in TARGET_BALANCES.items():
        drip = int(Decimal(target) * WEI) - named_net[user]
        assert drip > 0, f"{user} receives more than its target balance; change SEED or activity"
        kept.append((rng.randint(600, 2 * 3600), "faucet", user, drip))
    kept.sort(key=lambda e: e[0])

    # 校验：任何时刻都不能透支
    balance = defaultdict(int, faucet=FAUCET_BALANCE)
    for block, sender, recipient, wei in kept:
        balance[sender] -= wei + GAS_COST
        balance[recipient] += wei
        assert balance[sender] >= 0, f"{sender} overdrawn at block +{block}; change SEED or activity"
    return kept


# ---------------------------------------------------------------- RPC

def rpc(url: str, method: str, *params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode()
    resp = json.load(urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=60))
    if "error" in resp:
        raise RuntimeError(f"{method}: {resp['error']}")
    return resp["result"]


def genesis() -> None:
    faucet = Account.from_key(derived_key("faucet")).address
    print(json.dumps({
        "config": {"chainId": CHAIN_ID},
        "number": hex(START_BLOCK),
        "timestamp": hex(int(time.time()) - HISTORY),
        "gasLimit": hex(30_000_000),
        "alloc": {faucet[2:].lower(): {"balance": hex(FAUCET_BALANCE)}},
    }))


def seed(url: str) -> None:
    for _ in range(100):  # 等 anvil 起来
        try:
            rpc(url, "eth_chainId")
            break
        except Exception:
            time.sleep(0.2)

    t0 = int(rpc(url, "eth_getBlockByNumber", hex(START_BLOCK), False)["timestamp"], 16)
    wallets = load_wallets()
    schedule = build_schedule()
    nonces = defaultdict(int)
    by_block = defaultdict(list)
    for block, sender, recipient, wei in schedule:
        by_block[block].append((sender, recipient, wei))

    started = time.time()
    current = 0  # 已出的最后一个相对块号

    def mine_until(block: int) -> None:
        """出空块到相对块号 block（含），块 k 的时间 = t0 + k"""
        nonlocal current
        if block > current:
            rpc(url, "evm_setNextBlockTimestamp", t0 + current + 1)
            rpc(url, "anvil_mine", hex(block - current), "0x1")
            current = block

    for block in sorted(by_block):
        mine_until(block - 1)
        for sender, recipient, wei in by_block[block]:
            signed = Account.sign_transaction({
                "to": wallets[recipient].address, "value": wei, "gas": 21000, "gasPrice": GAS_PRICE,
                "nonce": nonces[sender], "chainId": CHAIN_ID,
            }, wallets[sender].key)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            rpc(url, "eth_sendRawTransaction", "0x" + raw.hex().removeprefix("0x"))
            nonces[sender] += 1
        rpc(url, "evm_setNextBlockTimestamp", t0 + block)
        rpc(url, "anvil_mine", "0x1", "0x0")
        current = block

    mine_until(HISTORY)
    # 追平到当前时间（出空块本身也要时间，追两轮），之后每秒出一个块
    for _ in range(3):
        mine_until(int(time.time()) - t0)
    rpc(url, "evm_setIntervalMining", 1)

    # 校验：每个计划块里的交易都已上链且成功
    mined = failed = 0
    for block in sorted(by_block):
        for tx in rpc(url, "eth_getBlockByNumber", hex(START_BLOCK + block), True)["transactions"]:
            mined += 1
            failed += rpc(url, "eth_getTransactionReceipt", tx["hash"])["status"] != "0x1"
    if mined != len(schedule) or failed:
        raise RuntimeError(f"seeding mismatch: planned {len(schedule)}, mined {mined}, failed {failed}")

    balances = {u: str(Decimal(int(rpc(url, "eth_getBalance", wallets[u].address, "latest"), 16)) / WEI) for u in TARGET_BALANCES}
    print(f"seeded {len(schedule)} txs over {HISTORY} blocks in {time.time() - started:.1f}s; "
          f"height {int(rpc(url, 'eth_blockNumber'), 16)}; balances {balances}", flush=True)
    open("/tmp/seeded", "w").close()


if __name__ == "__main__":
    if sys.argv[1] == "genesis":
        genesis()
    elif sys.argv[1] == "seed":
        seed(sys.argv[2])
