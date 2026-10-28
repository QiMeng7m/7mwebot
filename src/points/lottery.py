"""每日抽奖 — one paid draw from a mixed reward pool.

The pool is intentionally broad: balance, earned points, equipment,
fishing rods, bait and shields.  Within every category the weight is
inversely related to the reward's value, so a 5000-balance prize is far
rarer than a 20-balance one.  Category budgets keep equipment from
drowning out the currency rewards.

Draws cost balance and are limited per local day; the store applies the
charge, the reward and the draw row in one transaction.
"""

from __future__ import annotations

import logging
import random

from .util import is_group_enabled, local_day

logger = logging.getLogger(__name__)

# Percentage budget per category (sums to 100).
CATEGORY_WEIGHTS: dict[str, float] = {
    "balance": 35,
    "points": 20,
    "equipment": 20,
    "rod": 8,
    "bait": 12,
    "shield": 5,
}

# (amount, relative weight) — bigger amounts are rarer.
BALANCE_TIERS: list[tuple[int, float]] = [
    (20, 300), (50, 150), (100, 80), (200, 40),
    (500, 15), (1000, 5), (5000, 1),
]
POINTS_TIERS: list[tuple[int, float]] = [
    (10, 200), (20, 100), (50, 50), (100, 20),
    (200, 6), (500, 1),
]
# (duration seconds, relative weight)
SHIELD_TIERS: list[tuple[int, float]] = [
    (2 * 3600, 50), (4 * 3600, 30),
    (8 * 3600, 12), (24 * 3600, 3),
]
# (quantity, relative weight) — bigger stacks are rarer.
BAIT_QUANTITIES: list[tuple[int, float]] = [
    (5, 50), (10, 30), (20, 12), (50, 4),
]

_SLOT_EMOJI = {
    "weapon": "⚔️",
    "armor": "🛡️",
    "charm": "💍",
    "mount": "🐎",
}


def _spread(budget: float, rows: list[tuple[dict, float]]) -> list[dict]:
    """Scale relative weights so they consume *budget* in total."""
    total = sum(max(0.0, weight) for _, weight in rows)
    if total <= 0:
        return []
    result = []
    for entry, weight in rows:
        result.append({**entry, "weight": budget * max(0.0, weight) / total})
    return result


def build_pool(catalog, fishing_catalog, config) -> list[dict]:
    """Build the weighted reward pool from the live catalogs."""
    pool: list[dict] = []

    pool += _spread(CATEGORY_WEIGHTS["balance"], [
        ({"kind": "balance", "emoji": "💰",
          "name": f"余额 +{amount}", "amount": amount}, weight)
        for amount, weight in BALANCE_TIERS
    ])

    pool += _spread(CATEGORY_WEIGHTS["points"], [
        ({"kind": "points", "emoji": "⭐",
          "name": f"积分 +{amount}", "amount": amount}, weight)
        for amount, weight in POINTS_TIERS
    ])

    equipment_rows = []
    items = catalog.items() if catalog is not None else []
    for item in items:
        price = max(1, int(item.get("price", 0) or 0))
        equipment_rows.append((
            {"kind": "equipment", "emoji": _SLOT_EMOJI.get(
                item.get("slot", ""), "🎁"),
             "name": f"装备 {item['name']}",
             "item_id": item["id"], "slot": item.get("slot", ""),
             "power": int(item.get("power", 0) or 0), "price": price,
             "same_slot_ids": [
                 i["id"] for i in items
                 if i.get("slot") == item.get("slot")
             ]},
            1.0 / price,
        ))
    pool += _spread(CATEGORY_WEIGHTS["equipment"], equipment_rows)

    rod_rows = []
    rods = fishing_catalog.rods() if fishing_catalog is not None else []
    for rod in rods:
        if rod.get("default"):
            continue
        price = max(1, int(rod.get("price", 0) or 0))
        rod_rows.append((
            {"kind": "rod", "emoji": "🎣",
             "name": f"鱼竿 {rod['name']}",
             "rod_id": rod["id"], "price": price},
            1.0 / price,
        ))
    pool += _spread(CATEGORY_WEIGHTS["rod"], rod_rows)

    bait_rows = []
    baits = fishing_catalog.baits() if fishing_catalog is not None else []
    for bait in baits:
        price = max(1, int(bait.get("price", 0) or 0))
        for quantity, quantity_weight in BAIT_QUANTITIES:
            bait_rows.append((
                {"kind": "bait", "emoji": "🪱",
                 "name": f"鱼饵 {bait['name']}×{quantity}",
                 "bait_id": bait["id"], "quantity": quantity,
                 "price": price},
                quantity_weight / price,
            ))
    pool += _spread(CATEGORY_WEIGHTS["bait"], bait_rows)

    shield_max = max(3600, int(getattr(config, "shield_max_sec", 86400)))
    shield_rows = []
    for duration, weight in SHIELD_TIERS:
        hours = duration / 3600
        shield_rows.append((
            {"kind": "shield", "emoji": "🔰",
             "name": f"免战券 {hours:g} 小时",
             "duration_sec": duration, "max_sec": shield_max},
            weight,
        ))
    pool += _spread(CATEGORY_WEIGHTS["shield"], shield_rows)
    return pool


def pick_reward(pool: list[dict], roll: float) -> dict | None:
    """Pick one reward by weighted roll in ``[0, 1)``."""
    total = sum(max(0.0, float(entry.get("weight", 0))) for entry in pool)
    if total <= 0:
        return None
    target = min(max(float(roll), 0.0), 0.999999) * total
    cursor = 0.0
    for entry in pool:
        cursor += max(0.0, float(entry.get("weight", 0)))
        if target < cursor:
            return entry
    return pool[-1]


class LotteryService:
    """Resolves 「抽奖」 against the store and reward pool."""

    def __init__(self, store, config, catalog, fishing_catalog):
        self._store = store
        self._config = config
        self._catalog = catalog
        self._fishing_catalog = fishing_catalog

    # ── Helpers ───────────────────────────────────────────────────

    def is_enabled(self, chat_id: str, group_name: str) -> bool:
        if not getattr(self._config, "lottery_enabled", False):
            return False
        return is_group_enabled(
            chat_id, group_name,
            getattr(self._config, "lottery_groups", ["*"]),
        )

    def cost(self) -> int:
        return max(0, int(getattr(self._config, "lottery_cost", 100)))

    def daily_limit(self) -> int:
        return max(1, int(getattr(
            self._config, "lottery_daily_attempts", 3)))

    def pool(self) -> list[dict]:
        return build_pool(
            self._catalog, self._fishing_catalog, self._config)

    def category_chances(self) -> dict[str, float]:
        total = sum(CATEGORY_WEIGHTS.values())
        return {
            key: round(value * 100 / total, 1)
            for key, value in CATEGORY_WEIGHTS.items()
        }

    # ── Public API ────────────────────────────────────────────────

    def status(self, chat_id: str, group_name: str, sender_id: str,
               now_ts: int | None = None) -> dict:
        now = _now() if now_ts is None else int(now_ts)
        if not self.is_enabled(chat_id, group_name):
            return {"ok": False, "code": "disabled",
                    "reply": "每日抽奖未开启。"}
        day = local_day(now)
        used = self._store.lottery_draws_today(chat_id, sender_id, day)
        return {
            "ok": True,
            "used": used,
            "limit": self.daily_limit(),
            "cost": self.cost(),
            "balance": self._store.balance_of(chat_id, sender_id),
            "chances": self.category_chances(),
            "balance_tiers": [amount for amount, _ in BALANCE_TIERS],
            "points_tiers": [amount for amount, _ in POINTS_TIERS],
        }

    def draw(self, msg: dict, now_ts: int | None = None,
             rng=None) -> dict:
        now = _now() if now_ts is None else int(now_ts)
        chat_id = str(msg.get("chat_id") or "")
        group_name = str(msg.get("group_name") or "")
        sender_id = str(msg.get("sender_id") or "")
        sender_name = str(msg.get("sender_name") or "")
        message_id = str(msg.get("message_id") or "")

        if not self.is_enabled(chat_id, group_name):
            return {"ok": False, "code": "disabled",
                    "reply": "每日抽奖未开启。"}

        pool = self.pool()
        reward = pick_reward(pool, _roll(rng))
        if reward is None:
            return {"ok": False, "code": "empty",
                    "reply": "奖池暂时是空的，请稍后再试。"}

        day = local_day(now)
        result = self._store.commit_lottery_draw(
            chat_id=chat_id, sender_id=sender_id,
            sender_name=sender_name,
            message_id=message_id or f"lottery:{chat_id}:{now}",
            day=day, now_ts=now, cost=self.cost(),
            limit=self.daily_limit(), reward=reward,
        )
        if not result.get("ok"):
            code = result.get("code")
            if code == "limit":
                return {
                    "ok": False, "code": "limit",
                    "reply": f"今天的抽奖次数已用完"
                             f"（{result['limit']} 次），明天再来。",
                    "used": result.get("used", 0),
                    "limit": result.get("limit"),
                }
            if code == "insufficient":
                return {
                    "ok": False, "code": "insufficient",
                    "reply": f"余额不足：抽奖需要 {self.cost()} 余额，"
                             f"你只有 {result.get('balance', 0)}。",
                    "balance": result.get("balance", 0),
                }
            return {"ok": False, "code": code or "error",
                    "reply": "这次抽奖没有成功，请稍后再试。"}

        return {
            "ok": True,
            "code": "ok",
            "reward": reward,
            "detail": result.get("detail", ""),
            "used": result.get("used", 0),
            "limit": result.get("limit", self.daily_limit()),
            "cost": self.cost(),
            "balance": result.get("balance", 0),
        }

    def history(self, chat_id: str, sender_id: str,
                limit: int = 10) -> list[dict]:
        return self._store.lottery_history(chat_id, sender_id, limit)


def _now() -> int:
    import time
    return int(time.time())


def _roll(rng) -> float:
    if rng is None:
        return random.random()
    if hasattr(rng, "random"):
        return float(rng.random())
    return float(rng())
