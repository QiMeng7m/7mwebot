"""Phase 2 playability systems.

This module bundles the systems that build on the existing points /
equipment / duel / fishing foundations:

* 套装 — equipment set bonuses
* 技能升级 — skill levels powered by 技能书
* 每日 / 周常任务
* 秘境爬塔
* 装备词条洗练
* 擂台赛
* 门派
* 交易行
* 宠物
* 炼丹
* 天气 / 世界事件
* 转世 / 轮回
* 跨群榜

The heavy lifting lives in :class:`ExpansionService`; pure helpers such as
``combat_bonuses`` and ``world_state`` are used by the duel, boss and
fishing services.
"""

from __future__ import annotations

import math
import random
import time
import uuid

from .equipment import (
    EQUIPMENT_SETS,
    EquipmentCatalog,
    set_by_id,
)
from .models import PointAward
from .skills import (
    SKILL_MAX_LEVEL,
    format_effects as format_skill_effects,
    level_scale,
    loadout_skills,
    skill_by_id,
)
from .util import local_day, local_season, local_week

# ── Definitions ──────────────────────────────────────────────────────

BOOK_ITEM = "skill_book"
BOOK_NAME = "技能书"

TASKS: list[dict] = [
    {"id": "daily_chat_10", "name": "群聊活跃", "period": "daily",
     "metric": "messages", "target": 10, "desc": "发送 10 条消息",
     "reward": {"balance": 30, "points": 10}},
    {"id": "daily_checkin", "name": "每日签到", "period": "daily",
     "metric": "checkin", "target": 1, "desc": "完成 1 次签到",
     "reward": {"balance": 20, "book": 1}},
    {"id": "daily_meditate", "name": "静心打坐", "period": "daily",
     "metric": "meditate", "target": 1, "desc": "打坐 1 次",
     "reward": {"balance": 30}},
    {"id": "daily_duel", "name": "以武会友", "period": "daily",
     "metric": "duel", "target": 1, "desc": "切磋 1 次",
     "reward": {"balance": 30}},
    {"id": "daily_fish_3", "name": "渔获满满", "period": "daily",
     "metric": "fish", "target": 3, "desc": "钓鱼 3 竿",
     "reward": {"balance": 40, "book": 1}},
    {"id": "weekly_chat_100", "name": "周常活跃", "period": "weekly",
     "metric": "messages_week", "target": 100, "desc": "本周发送 100 条消息",
     "reward": {"balance": 300, "book": 2}},
    {"id": "weekly_boss_3", "name": "讨伐巨兽", "period": "weekly",
     "metric": "boss_week", "target": 3, "desc": "本周挑战 Boss 3 次",
     "reward": {"balance": 200, "book": 1}},
    {"id": "weekly_dungeon", "name": "秘境探索", "period": "weekly",
     "metric": "dungeon_week", "target": 1, "desc": "本周通关秘境 1 次",
     "reward": {"balance": 300, "book": 2}},
]

AFFIX_POOL: list[dict] = [
    {"id": "atk", "name": "锋锐", "stat": "attack_pct",
     "min": 0.02, "max": 0.06, "weight": 28},
    {"id": "def", "name": "坚壁", "stat": "defense_pct",
     "min": 0.02, "max": 0.06, "weight": 28},
    {"id": "win", "name": "洞察", "stat": "duel_win",
     "min": 0.01, "max": 0.03, "weight": 22},
    {"id": "boss", "name": "屠戮", "stat": "boss_damage",
     "min": 0.02, "max": 0.05, "weight": 22},
]

DUNGEON_DAILY_ATTEMPTS = 3
DUNGEON_MAX_FLOOR = 50

ARENA_DAILY_ATTEMPTS = 3
ARENA_WIN_POINTS = 10
ARENA_LOSS_POINTS = 3
ARENA_MIN_POINTS_FOR_REWARD = 50
ARENA_REWARDS = [500, 300, 200, 100, 50]

GUILD_CREATE_COST = 500
GUILD_MAX_LEVEL = 10
GUILD_EXP_PER_LEVEL = 1000
GUILD_BONUS_PER_LEVEL = 0.01

PET_SPECIES: list[dict] = [
    {"id": "fox", "name": "灵狐", "price": 300, "attack": 3,
     "defense": 0, "duel_win": 0.0,
     "desc": "敏捷的灵狐，提升攻击"},
    {"id": "turtle", "name": "玄龟", "price": 400, "attack": 0,
     "defense": 3, "duel_win": 0.0,
     "desc": "厚重的玄龟，提升防御"},
    {"id": "crane", "name": "仙鹤", "price": 500, "attack": 2,
     "defense": 2, "duel_win": 0.0,
     "desc": "优雅的仙鹤，攻防均衡"},
    {"id": "qilin", "name": "瑞麟", "price": 800, "attack": 2,
     "defense": 2, "duel_win": 0.01,
     "desc": "祥瑞之兽，提升攻防与切磋胜率"},
]
PET_MAX_LEVEL = 10
PET_EXP_PER_LEVEL = 5

PILLS: list[dict] = [
    {"id": "pill_qi", "name": "聚气丹", "herbs": 3, "price": 50},
    {"id": "pill_guard", "name": "护体丹", "herbs": 4, "price": 80},
    {"id": "pill_break", "name": "破军丹", "herbs": 5, "price": 100},
]
GATHER_COOLDOWN = 1800
GATHER_MAX_HERBS = 99

WEATHERS: list[dict] = [
    {"id": "clear", "name": "晴空万里",
     "desc": "钓鱼收益略微提升", "fishing_value": 0.05},
    {"id": "rain", "name": "细雨绵绵",
     "desc": "钓鱼稀有度提升", "fishing_rare": 0.15},
    {"id": "snow", "name": "瑞雪纷飞",
     "desc": "渔获价值提升", "fishing_value": 0.10},
    {"id": "wind", "name": "长风猎猎",
     "desc": "Boss 伤害提升", "boss_damage": 0.10},
    {"id": "spirit", "name": "灵气潮汐",
     "desc": "Boss 伤害提升", "boss_damage": 0.05},
]

WORLD_EVENTS: list[dict] = [
    {"id": "none", "name": "风平浪静", "desc": "暂无世界事件"},
    {"id": "spirit_tide", "name": "灵气潮汐",
     "desc": "Boss 伤害与渔获价值 +10%", "boss_damage": 0.10,
     "fishing_value": 0.10},
    {"id": "fish_tide", "name": "鱼潮涌动",
     "desc": "钓鱼稀有度 +20%", "fishing_rare": 0.20},
    {"id": "demon", "name": "妖气冲天",
     "desc": "Boss 伤害 +15%", "boss_damage": 0.15},
    {"id": "fortune", "name": "祥瑞降世",
     "desc": "抽奖气运与钓鱼价值 +10%",
     "lottery_luck": 0.10, "fishing_value": 0.10},
]

REBIRTH_REALM_REQUIRED = 10
REBIRTH_LEVEL_REQUIRED = 12
REBIRTH_COST = 10000
REBIRTH_BONUS_PER_CYCLE = 0.05

MARKET_FEE_RATIO = 0.05
MARKET_LIST_DAYS = 7


# ── Pure helpers ─────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())


def _roll(rng) -> float:
    if rng is None:
        return random.random()
    if hasattr(rng, "random"):
        return float(rng.random())
    return float(rng())


def _hash(text: str) -> int:
    return sum((i + 1) * ord(ch) for i, ch in enumerate(text))


def _day_start(day: str) -> int:
    try:
        return int(time.mktime(time.strptime(day, "%Y-%m-%d")))
    except ValueError:
        return _now() - 86400


def _win_probability(attack: int, defense: int) -> float:
    if attack <= 0 or defense <= 0:
        return 0.08
    p = 0.5 + 0.5 * math.tanh(1.15 * math.log(attack / defense))
    return max(0.08, min(0.92, p))


def world_state(chat_id: str, now_ts: int | None = None) -> dict:
    """Deterministic weather + hourly world event for one group."""
    now = _now() if now_ts is None else int(now_ts)
    day = local_day(now)
    hour = now // 3600
    weather = WEATHERS[_hash(f"{chat_id}:{day}") % len(WEATHERS)]
    event = WORLD_EVENTS[
        _hash(f"{chat_id}:{day}:{hour}") % len(WORLD_EVENTS)
    ]
    effects: dict[str, float] = {}
    for key, value in weather.items():
        if isinstance(value, (int, float)) and key not in ("id",):
            effects[key] = effects.get(key, 0.0) + float(value)
    for key, value in event.items():
        if isinstance(value, (int, float)) and key not in ("id",):
            effects[key] = effects.get(key, 0.0) + float(value)
    return {
        "day": day,
        "weather": weather,
        "event": event,
        "effects": effects,
    }


def world_fishing_bonuses(chat_id: str,
                          now_ts: int | None = None) -> dict:
    effects = world_state(chat_id, now_ts)["effects"]
    return {
        "rare": float(effects.get("fishing_rare", 0) or 0),
        "value": float(effects.get("fishing_value", 0) or 0),
    }


def world_boss_bonus(chat_id: str, now_ts: int | None = None) -> float:
    return float(world_state(chat_id, now_ts)["effects"].get(
        "boss_damage", 0) or 0)


# ── Aggregated combat bonuses ────────────────────────────────────────

def set_bonuses(store, catalog, chat_id: str, sender_id: str) -> dict:
    """Activate set bonuses from the currently equipped items."""
    equipped_sets: dict[str, int] = {}
    for row in store.owned_items(chat_id, sender_id):
        if not row.get("equipped"):
            continue
        item = catalog.by_id(str(row.get("item_id") or ""))
        if item is None:
            continue
        set_id = str(item.get("set_id") or "")
        if set_id:
            equipped_sets[set_id] = equipped_sets.get(set_id, 0) + 1
    totals: dict[str, float] = {}
    for set_id, count in equipped_sets.items():
        definition = set_by_id(set_id)
        if definition is None:
            continue
        for bonus in definition["bonuses"]:
            if count < int(bonus["pieces"]):
                continue
            for key, value in bonus["effects"].items():
                totals[key] = totals.get(key, 0.0) + float(value)
    return totals


def _rows(store, sql: str, params=()) -> list:
    with store.lock:
        return store.conn.execute(sql, params).fetchall()


def affix_bonuses(store, chat_id: str, sender_id: str) -> dict:
    rows = _rows(
        store,
        """SELECT a.stat, a.value FROM equipment_affixes a
           JOIN equipment_owned o
             ON o.chat_id = a.chat_id AND o.sender_id = a.sender_id
            AND o.item_id = a.item_id
           WHERE a.chat_id = ? AND a.sender_id = ? AND o.equipped = 1""",
        (str(chat_id), str(sender_id)),
    )
    totals: dict[str, float] = {}
    for row in rows:
        totals[str(row[0])] = totals.get(str(row[0]), 0.0) + \
            float(row[1] or 0)
    return totals


def guild_bonus(store, chat_id: str, sender_id: str) -> dict:
    rows = _rows(
        store,
        """SELECT g.level FROM guilds g
           JOIN guild_members m ON m.chat_id = g.chat_id
           WHERE g.chat_id = ? AND m.sender_id = ?""",
        (str(chat_id), str(sender_id)),
    )
    if not rows:
        return {}
    level = max(0, min(GUILD_MAX_LEVEL, int(rows[0][0] or 0)))
    bonus = level * GUILD_BONUS_PER_LEVEL
    return {"attack_pct": bonus, "defense_pct": bonus}


def pet_bonus(store, chat_id: str, sender_id: str) -> dict:
    rows = _rows(
        store,
        """SELECT species_id, level FROM pets
           WHERE chat_id = ? AND sender_id = ?""",
        (str(chat_id), str(sender_id)),
    )
    if not rows:
        return {}
    species = _pet_species(str(rows[0][0] or ""))
    if species is None:
        return {}
    level = max(1, int(rows[0][1] or 1))
    return {
        "flat_attack": species["attack"] * level,
        "flat_defense": species["defense"] * level,
        "duel_win": float(species["duel_win"]) * level,
    }


def rebirth_bonus(store, chat_id: str, sender_id: str) -> dict:
    rows = _rows(
        store,
        """SELECT cycle FROM rebirths
           WHERE chat_id = ? AND sender_id = ?""",
        (str(chat_id), str(sender_id)),
    )
    cycle = int(rows[0][0] or 0) if rows else 0
    if cycle <= 0:
        return {}
    bonus = cycle * REBIRTH_BONUS_PER_CYCLE
    return {"attack_pct": bonus, "defense_pct": bonus,
            "boss_damage": bonus, "cycle": cycle}


def combat_bonuses(store, catalog, chat_id: str, sender_id: str,
                   now_ts: int | None = None) -> dict:
    """All non-skill passive bonuses used by duel power.

    World weather/events are applied separately by the combat services so
    that Boss damage does not get counted twice.
    """
    totals: dict[str, float] = {}
    sources = [
        set_bonuses(store, catalog, chat_id, sender_id),
        affix_bonuses(store, chat_id, sender_id),
        guild_bonus(store, chat_id, sender_id),
        pet_bonus(store, chat_id, sender_id),
        rebirth_bonus(store, chat_id, sender_id),
    ]
    for source in sources:
        for key, value in source.items():
            if key in ("cycle",):
                continue
            totals[key] = totals.get(key, 0.0) + float(value)
    return totals


# ── Service ──────────────────────────────────────────────────────────

class ExpansionService:
    """Commands and persistence for the phase-2 systems."""

    def __init__(self, store, config, levels, catalog: EquipmentCatalog,
                 duels):
        self._store = store
        self._config = config
        self._levels = levels
        self._catalog = catalog
        self._duels = duels

    # ── Low-level helpers ────────────────────────────────────────

    def _balance(self, chat_id: str, sender_id: str) -> int:
        return self._store.balance_of(chat_id, sender_id)

    def _grant(self, chat_id: str, sender_id: str, sender_name: str,
               amount: int, reason: str,
               category: str = "expansion") -> None:
        amount = int(amount)
        if amount <= 0:
            return
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT OR IGNORE INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 f"xp:{uuid.uuid4().hex}", str(reason), str(category),
                 amount, local_day(), local_season(), _now()),
            )

    def _record(self, chat_id: str, sender_id: str, sender_name: str,
                reason: str, category: str = "expansion") -> None:
        """Write a zero-point ledger row used for counting/eligibility."""
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT OR IGNORE INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 f"xp:{uuid.uuid4().hex}", str(reason), str(category),
                 local_day(), local_season(), _now()),
            )

    def _spend(self, chat_id: str, sender_id: str, sender_name: str,
               amount: int, reason: str) -> bool:
        amount = int(amount)
        if amount <= 0:
            return True
        now = _now()
        with self._store.lock, self._store.conn:
            cur = self._store.conn.execute(
                """INSERT OR IGNORE INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   SELECT ?, ?, ?, ?, ?, 'spend', ?, ?, ?, ?
                   WHERE (SELECT COALESCE(SUM(points), 0) FROM point_events
                          WHERE chat_id = ? AND sender_id = ?) >= ?""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 f"xp:{uuid.uuid4().hex}", str(reason), -amount,
                 local_day(now), local_season(now), now,
                 str(chat_id), str(sender_id), amount),
            )
            return cur.rowcount > 0

    def _inventory(self, chat_id: str, sender_id: str) -> list[dict]:
        rows = _rows(
            self._store,
            """SELECT kind, item_id, quantity FROM item_inventory
               WHERE chat_id = ? AND sender_id = ? AND quantity > 0
               ORDER BY kind, item_id""",
            (str(chat_id), str(sender_id)),
        )
        return [dict(row) for row in rows]

    def _item_count(self, chat_id: str, sender_id: str,
                    kind: str, item_id: str) -> int:
        rows = _rows(
            self._store,
            """SELECT quantity FROM item_inventory
               WHERE chat_id = ? AND sender_id = ?
                 AND kind = ? AND item_id = ?""",
            (str(chat_id), str(sender_id), str(kind), str(item_id)),
        )
        return int(rows[0][0] or 0) if rows else 0

    def _add_item(self, chat_id: str, sender_id: str, kind: str,
                  item_id: str, quantity: int = 1) -> None:
        if quantity <= 0:
            return
        now = _now()
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO item_inventory
                   (chat_id, sender_id, kind, item_id, quantity, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id, kind, item_id) DO UPDATE
                     SET quantity = quantity + excluded.quantity,
                         updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), str(kind), str(item_id),
                 int(quantity), now),
            )

    def _consume_item(self, chat_id: str, sender_id: str, kind: str,
                      item_id: str, quantity: int = 1) -> bool:
        with self._store.lock, self._store.conn:
            cur = self._store.conn.execute(
                """UPDATE item_inventory SET quantity = quantity - ?,
                        updated_at = ?
                   WHERE chat_id = ? AND sender_id = ?
                     AND kind = ? AND item_id = ? AND quantity >= ?""",
                (int(quantity), _now(), str(chat_id), str(sender_id),
                 str(kind), str(item_id), int(quantity)),
            )
            return cur.rowcount > 0

    def _books(self, chat_id: str, sender_id: str) -> int:
        return self._item_count(chat_id, sender_id, "book", BOOK_ITEM)

    # ── Sets ─────────────────────────────────────────────────────

    def summary(self, chat_id: str, sender_id: str,
                now_ts: int | None = None) -> dict:
        """Compact snapshot consumed by the web console."""
        now = _now() if now_ts is None else int(now_ts)
        equipped_sets: dict[str, list[str]] = {}
        for row in self._store.owned_items(chat_id, sender_id):
            if not row.get("equipped"):
                continue
            item = self._catalog.by_id(str(row.get("item_id") or ""))
            if item is None or not item.get("set_id"):
                continue
            equipped_sets.setdefault(item["set_id"], []).append(item["name"])
        sets = []
        for definition in EQUIPMENT_SETS:
            names = equipped_sets.get(definition["id"], [])
            sets.append({
                "id": definition["id"], "name": definition["name"],
                "equipped": len(names), "pieces": len(definition["pieces"]),
                "items": names,
                "bonuses": [
                    {**bonus, "active": len(names) >= int(bonus["pieces"])}
                    for bonus in definition["bonuses"]
                ],
            })
        tasks = []
        for task in TASKS:
            state = self._task_state(chat_id, sender_id, task, now)
            tasks.append({
                "id": task["id"], "name": task["name"],
                "period": task["period"], "desc": task["desc"],
                "progress": min(state["progress"], task["target"]),
                "target": task["target"], "claimed": state["claimed"],
                "claimable": (not state["claimed"]
                              and state["progress"] >= task["target"]),
            })
        season = local_week(now)
        day = local_day(now)
        board = self.arena_board(chat_id, season, limit=5)
        rank = next(
            (index for index, row in enumerate(board, 1)
             if row["sender_id"] == sender_id), 0)
        guild = self._guild(chat_id)
        member = self._guild_member(chat_id, sender_id)
        pet_rows = _rows(
            self._store,
            """SELECT species_id, level, exp FROM pets
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        pet = None
        if pet_rows:
            species = _pet_species(str(pet_rows[0][0]))
            if species:
                pet = {"species": species["name"],
                       "level": int(pet_rows[0][1] or 1),
                       "exp": int(pet_rows[0][2] or 0)}
        alchemy = self._alchemy(chat_id, sender_id)
        pills = [
            {"id": pill["id"], "name": pill["name"],
             "count": self._item_count(chat_id, sender_id, "pill",
                                       pill["id"])}
            for pill in PILLS
        ]
        return {
            "sets": sets,
            "tasks": tasks,
            "dungeon": {
                "best": self._dungeon_best(chat_id, sender_id),
                "attempts": self._dungeon_attempts(
                    chat_id, sender_id, day),
                "limit": DUNGEON_DAILY_ATTEMPTS
                + self._rebirth_cycle(chat_id, sender_id),
            },
            "arena": {
                "season": season,
                "entry": self._arena_entry(chat_id, season, sender_id),
                "rank": rank, "board": board,
                "used": self._arena_used_today(chat_id, sender_id, day),
                "limit": ARENA_DAILY_ATTEMPTS
                + self._rebirth_cycle(chat_id, sender_id),
            },
            "guild": {
                "guild": guild, "member": member,
                "bonus": (guild["level"] * GUILD_BONUS_PER_LEVEL
                          if guild else 0),
            },
            "pet": pet,
            "alchemy": {"herbs": alchemy["herbs"], "pills": pills,
                        "books": self._books(chat_id, sender_id)},
            "world": world_state(chat_id, now),
            "rebirth": {
                "cycle": self._rebirth_cycle(chat_id, sender_id),
                "bonus": self._rebirth_cycle(chat_id, sender_id)
                * REBIRTH_BONUS_PER_CYCLE,
            },
        }

    def sets_status(self, chat_id: str, sender_id: str,
                    sender_name: str) -> str:
        equipped: dict[str, list[str]] = {}
        for row in self._store.owned_items(chat_id, sender_id):
            if not row.get("equipped"):
                continue
            item = self._catalog.by_id(str(row.get("item_id") or ""))
            if item is None or not item.get("set_id"):
                continue
            equipped.setdefault(item["set_id"], []).append(item["name"])
        lines = [f"{sender_name} 的套装（已装备 {sum(len(v) for v in equipped.values())} 件）"]
        for definition in EQUIPMENT_SETS:
            owned = equipped.get(definition["id"], [])
            lines.append(
                f"【{definition['name']}】{len(owned)}/"
                f"{len(definition['pieces'])} 件"
                + ("：" + "、".join(owned) if owned else "")
            )
            for bonus in definition["bonuses"]:
                active = len(owned) >= int(bonus["pieces"])
                lines.append(
                    f"  {'✓' if active else '·'} {bonus['desc']}"
                )
        lines.append("套装装备可在「商店」查看；穿戴对应件数自动生效。")
        return "\n".join(lines)

    # ── Skill upgrades ───────────────────────────────────────────

    def upgrade_skill(self, chat_id: str, sender_id: str,
                      sender_name: str, raw: str) -> str:
        name = str(raw or "").strip()
        if not name:
            return "用法：升级技能 技能名（需要技能书与灵石）"
        loadout = loadout_skills(
            self._store, self._catalog, chat_id, sender_id)
        skill = next(
            (item for item in loadout
             if item["name"] == name or item["id"] == name), None)
        if skill is None:
            return f"你当前装备没有技能「{name}」，发「技能」查看。"
        if skill.get("pill"):
            return "丹药不是装备技能，无法升级。"
        level = self._store.skill_level_of(
            chat_id, sender_id, skill["id"])
        if level >= SKILL_MAX_LEVEL:
            return f"「{skill['name']}」已经达到最高 {SKILL_MAX_LEVEL} 级。"
        books = self._books(chat_id, sender_id)
        cost = 200 * level
        if books < 1:
            return f"技能书不足：升级「{skill['name']}」需要 1 本技能书。"
        balance = self._balance(chat_id, sender_id)
        if balance < cost:
            return (
                f"灵石不足：升级「{skill['name']}」需要 {cost} 灵石，"
                f"你只有 {balance}。"
            )
        if not self._consume_item(chat_id, sender_id, "book", BOOK_ITEM):
            return "技能书不足。"
        if not self._spend(chat_id, sender_id, sender_name, cost,
                           f"skill_upgrade:{skill['id']}"):
            # refund the book if the balance changed concurrently
            self._add_item(chat_id, sender_id, "book", BOOK_ITEM)
            return "灵石不足。"
        new_level = level + 1
        self._store.set_skill_level(
            chat_id, sender_id, skill["id"], new_level)
        return (
            f"升级成功：《{skill['name']}》Lv{new_level}"
            f"（效果 ×{level_scale(new_level):.2f}），"
            f"消耗 1 本技能书 + {cost} 灵石。"
        )

    # ── Tasks ────────────────────────────────────────────────────

    def _task_counts(self, chat_id: str, sender_id: str,
                     now: int) -> dict[str, int]:
        day = local_day(now)
        start = _day_start(day)
        week_start = now - 7 * 86400

        def count(sql: str, params) -> int:
            rows = _rows(self._store, sql, params)
            return int(rows[0][0] or 0) if rows else 0

        return {
            "messages": count(
                """SELECT COUNT(*) FROM messages
                   WHERE chat_id = ? AND sender_id = ? AND timestamp >= ?""",
                (str(chat_id), str(sender_id), start)),
            "messages_week": count(
                """SELECT COUNT(*) FROM messages
                   WHERE chat_id = ? AND sender_id = ? AND timestamp >= ?""",
                (str(chat_id), str(sender_id), week_start)),
            "checkin": count(
                """SELECT COUNT(*) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?
                     AND reason = 'daily_first' AND day = ?""",
                (str(chat_id), str(sender_id), day)),
            "meditate": count(
                """SELECT COUNT(*) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?
                     AND reason = 'meditation' AND day = ?""",
                (str(chat_id), str(sender_id), day)),
            "duel": count(
                """SELECT COUNT(*) FROM duels
                   WHERE chat_id = ? AND day = ? AND attacker_id = ?""",
                (str(chat_id), day, str(sender_id))),
            "fish": count(
                """SELECT COUNT(*) FROM fishing_casts
                   WHERE chat_id = ? AND sender_id = ? AND day = ?""",
                (str(chat_id), str(sender_id), day)),
            "boss_week": count(
                """SELECT COUNT(*) FROM boss_attacks
                   WHERE chat_id = ? AND sender_id = ? AND created_at >= ?""",
                (str(chat_id), str(sender_id), week_start)),
            "dungeon_week": count(
                """SELECT COUNT(*) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?
                     AND reason = 'dungeon_clear' AND created_at >= ?""",
                (str(chat_id), str(sender_id), week_start)),
        }

    def _period_key(self, task: dict, now: int) -> str:
        if task["period"] == "weekly":
            return "W:" + local_week(now)
        return local_day(now)

    def _task_state(self, chat_id: str, sender_id: str,
                    task: dict, now: int) -> dict:
        period = self._period_key(task, now)
        progress = self._task_counts(
            chat_id, sender_id, now).get(task["metric"], 0)
        rows = _rows(
            self._store,
            """SELECT claimed FROM task_progress
               WHERE chat_id = ? AND sender_id = ?
                 AND task_id = ? AND period = ?""",
            (str(chat_id), str(sender_id), task["id"], period),
        )
        claimed = bool(rows and rows[0][0])
        return {
            "task": task, "period": period, "progress": progress,
            "claimed": claimed,
        }

    def tasks_status(self, chat_id: str, sender_id: str,
                     sender_name: str, period: str = "daily") -> str:
        now = _now()
        lines = [f"{sender_name} 的{'每日' if period == 'daily' else '周常'}任务"]
        for task in TASKS:
            if task["period"] != period:
                continue
            state = self._task_state(chat_id, sender_id, task, now)
            mark = "✓已领取" if state["claimed"] else (
                "可领取" if state["progress"] >= task["target"] else "进行中")
            lines.append(
                f"· {task['name']}：{min(state['progress'], task['target'])}"
                f"/{task['target']}（{task['desc']}）· {mark}"
            )
        lines.append("发送「领取任务奖励 任务名」领取；每日/每周自动刷新。")
        return "\n".join(lines)

    def claim_task(self, chat_id: str, sender_id: str,
                   sender_name: str, raw: str) -> str:
        name = str(raw or "").strip()
        now = _now()
        tasks = [task for task in TASKS if task["name"] == name
                 or task["id"] == name]
        if not tasks:
            return "没有找到这个任务，发「任务」查看。"
        task = tasks[0]
        state = self._task_state(chat_id, sender_id, task, now)
        if state["claimed"]:
            return f"「{task['name']}」本期奖励已经领取过了。"
        progress = state["progress"]
        if progress < int(task["target"]):
            return (
                f"「{task['name']}」进度不足："
                f"{progress}/{task['target']}。"
            )
        period = state["period"]
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO task_progress
                   (chat_id, sender_id, task_id, period, progress,
                    claimed, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(chat_id, sender_id, task_id, period)
                   DO UPDATE SET progress = excluded.progress,
                                 claimed = 1,
                                 updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), task["id"], period,
                 int(progress), now),
            )
        reward = task["reward"]
        if reward.get("balance"):
            self._grant(chat_id, sender_id, sender_name,
                        int(reward["balance"]), f"task:{task['id']}",
                        "task")
        if reward.get("points"):
            self._store.record_events(
                chat_id, f"task:{task['id']}:{period}",
                [PointAward(sender_id, sender_name,
                            f"task:{task['id']}",
                            int(reward["points"]), "earn")],
            )
        books = int(reward.get("book", 0) or 0)
        if books:
            self._add_item(chat_id, sender_id, "book", BOOK_ITEM, books)
        parts = []
        if reward.get("balance"):
            parts.append(f"{reward['balance']} 灵石")
        if reward.get("points"):
            parts.append(f"{reward['points']} 修炼值")
        if books:
            parts.append(f"{books} 本技能书")
        return f"领取成功：{task['name']} → " + "、".join(parts) + "。"

    # ── Dungeon ──────────────────────────────────────────────────

    def _dungeon_best(self, chat_id: str, sender_id: str) -> int:
        rows = _rows(
            self._store,
            """SELECT best_floor FROM dungeon_progress
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        return int(rows[0][0] or 0) if rows else 0

    def _dungeon_attempts(self, chat_id: str, sender_id: str,
                          day: str) -> int:
        rows = _rows(
            self._store,
            """SELECT attempts FROM dungeon_state
               WHERE chat_id = ? AND sender_id = ? AND day = ?""",
            (str(chat_id), str(sender_id), day),
        )
        return int(rows[0][0] or 0) if rows else 0

    def dungeon_status(self, chat_id: str, sender_id: str,
                       sender_name: str) -> str:
        now = _now()
        day = local_day(now)
        best = self._dungeon_best(chat_id, sender_id)
        attempts = self._dungeon_attempts(chat_id, sender_id, day)
        rebirth = self._rebirth_cycle(chat_id, sender_id)
        limit = DUNGEON_DAILY_ATTEMPTS + rebirth
        lines = [
            f"{sender_name} 的秘境进度：最高第 {best} 层",
            f"今日挑战 {attempts}/{limit} 次",
            f"下一层：第 {best + 1} 层（发送「挑战秘境」）",
            "秘境产出灵石与技能书，层数越高奖励越多。",
        ]
        return "\n".join(lines)

    def challenge_dungeon(self, chat_id: str, sender_id: str,
                          sender_name: str, raw: str = "",
                          rng=None) -> str:
        now = _now()
        day = local_day(now)
        best = self._dungeon_best(chat_id, sender_id)
        rebirth = self._rebirth_cycle(chat_id, sender_id)
        limit = DUNGEON_DAILY_ATTEMPTS + rebirth
        attempts = self._dungeon_attempts(chat_id, sender_id, day)
        if attempts >= limit:
            return f"今日秘境次数已用完（{limit} 次），明天再来。"
        try:
            requested = int(str(raw or "").strip() or (best + 1))
        except ValueError:
            return "用法：挑战秘境 [层数]"
        floor = max(1, min(DUNGEON_MAX_FLOOR, requested))
        if floor > best + 1:
            return f"只能挑战第 {best + 1} 层或已通关的层数。"
        power = self._duels.power(chat_id, sender_id)
        required = 30 + floor * 12
        monster_def = 20 + floor * 8
        chance = _win_probability(power.attack, monster_def)
        roll = _roll(rng)
        win = roll < chance
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO dungeon_state
                   (chat_id, sender_id, day, attempts, updated_at)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(chat_id, sender_id, day) DO UPDATE SET
                     attempts = attempts + 1,
                     updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), day, now),
            )
        attempts += 1
        if not win:
            return (
                f"第 {floor} 层挑战失败（胜率 {round(chance * 100)}%），"
                f"剩余 {max(0, limit - attempts)} 次。"
            )
        reward = floor * 20
        books = 1 if floor % 5 == 0 else 0
        self._grant(chat_id, sender_id, sender_name, reward,
                    "dungeon_reward", "dungeon")
        self._record(chat_id, sender_id, sender_name,
                     "dungeon_clear", "dungeon")
        if books:
            self._add_item(chat_id, sender_id, "book", BOOK_ITEM, books)
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO dungeon_progress
                   (chat_id, sender_id, best_floor, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     best_floor = MAX(best_floor, excluded.best_floor),
                     updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), floor, now),
            )
        lines = [
            f"第 {floor} 层通关！（胜率 {round(chance * 100)}%）",
            f"获得 {reward} 灵石"
            + (f" + {books} 本技能书" if books else ""),
            f"剩余 {max(0, limit - attempts)} 次挑战。",
        ]
        return "\n".join(lines)

    # ── Affixes ──────────────────────────────────────────────────

    def affixes_of(self, chat_id: str, sender_id: str,
                   item_id: str) -> list[dict]:
        rows = _rows(
            self._store,
            """SELECT slot, affix_id, value FROM equipment_affixes
               WHERE chat_id = ? AND sender_id = ? AND item_id = ?
               ORDER BY slot""",
            (str(chat_id), str(sender_id), str(item_id)),
        )
        result = []
        for row in rows:
            affix = next(
                (a for a in AFFIX_POOL if a["id"] == str(row[1])), None)
            if affix is None:
                continue
            result.append({
                "slot": int(row[0]), "id": str(row[1]),
                "name": affix["name"], "stat": affix["stat"],
                "value": float(row[2] or 0),
            })
        return result

    def roll_affixes(self, chat_id: str, sender_id: str,
                     sender_name: str, raw: str, rng=None) -> str:
        name = str(raw or "").strip()
        if not name:
            return "用法：洗练 装备名"
        item = self._catalog.find(name)
        if item is None:
            return f"没有找到「{name}」。"
        if not self._store.owns(chat_id, sender_id, item["id"]):
            return f"你还没有 {item['name']}。"
        cost = max(50, int(item["price"]) // 5)
        if self._balance(chat_id, sender_id) < cost:
            return f"灵石不足：洗练需要 {cost} 灵石。"
        if not self._spend(chat_id, sender_id, sender_name, cost,
                           f"affix:{item['id']}"):
            return "灵石不足。"
        count = 1 + int(_roll(rng) * 3)
        count = max(1, min(3, count))
        pool = list(AFFIX_POOL)
        rolls: list[dict] = []
        for slot in range(count):
            if not pool:
                break
            total = sum(a["weight"] for a in pool)
            pick = _roll(rng) * total
            chosen = pool[-1]
            for candidate in pool:
                pick -= candidate["weight"]
                if pick <= 0:
                    chosen = candidate
                    break
            pool.remove(chosen)
            value = chosen["min"] + (chosen["max"] - chosen["min"]) * \
                _roll(rng)
            rolls.append({**chosen, "value": round(value, 4)})
        now = _now()
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """DELETE FROM equipment_affixes
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), item["id"]),
            )
            for slot, roll in enumerate(rolls):
                self._store.conn.execute(
                    """INSERT INTO equipment_affixes
                       (chat_id, sender_id, item_id, slot, affix_id,
                        value, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (str(chat_id), str(sender_id), item["id"], slot,
                     roll["id"], float(roll["value"]), now),
                )
        detail = "、".join(
            f"{roll['name']} {roll['stat']} +{round(roll['value'] * 100, 1)}%"
            for roll in rolls
        )
        return (
            f"洗练完成：{item['name']} 获得 {len(rolls)} 条词条"
            f"（{detail}），花费 {cost} 灵石。"
        )

    def affix_status(self, chat_id: str, sender_id: str,
                     sender_name: str, raw: str) -> str:
        name = str(raw or "").strip()
        if not name:
            return "用法：词条 装备名"
        item = self._catalog.find(name)
        if item is None:
            return f"没有找到「{name}」。"
        affixes = self.affixes_of(chat_id, sender_id, item["id"])
        if not affixes:
            return f"{item['name']} 还没有词条，发「洗练 {item['name']}」获取。"
        lines = [f"{item['name']} 的词条"]
        for affix in affixes:
            lines.append(
                f"· {affix['name']}：{affix['stat']} "
                f"+{round(affix['value'] * 100, 1)}%"
            )
        return "\n".join(lines)

    # ── Arena ────────────────────────────────────────────────────

    def _arena_entry(self, chat_id: str, season: str,
                     sender_id: str) -> dict:
        rows = _rows(
            self._store,
            """SELECT points, wins, losses FROM arena_entries
               WHERE chat_id = ? AND season = ? AND sender_id = ?""",
            (str(chat_id), str(season), str(sender_id)),
        )
        if not rows:
            return {"points": 0, "wins": 0, "losses": 0}
        return {"points": int(rows[0][0] or 0),
                "wins": int(rows[0][1] or 0),
                "losses": int(rows[0][2] or 0)}

    def _arena_used_today(self, chat_id: str, sender_id: str,
                          day: str) -> int:
        rows = _rows(
            self._store,
            """SELECT COUNT(*) FROM point_events
               WHERE chat_id = ? AND sender_id = ?
                 AND reason = 'arena' AND day = ?""",
            (str(chat_id), str(sender_id), day),
        )
        return int(rows[0][0] or 0) if rows else 0

    def arena_status(self, chat_id: str, sender_id: str,
                     sender_name: str) -> str:
        now = _now()
        season = local_week(now)
        entry = self._arena_entry(chat_id, season, sender_id)
        used = self._arena_used_today(chat_id, sender_id, local_day(now))
        rebirth = self._rebirth_cycle(chat_id, sender_id)
        limit = ARENA_DAILY_ATTEMPTS + rebirth
        board = self.arena_board(chat_id, limit=5)
        lines = [
            f"{sender_name} 的擂台战绩（{season}）："
            f"{entry['points']} 分 · {entry['wins']} 胜 "
            f"{entry['losses']} 负",
            f"今日挑战 {used}/{limit} 次 · 发送「擂台挑战」匹配对手",
        ]
        if board:
            lines.append("本群前五：")
            for index, row in enumerate(board, 1):
                lines.append(
                    f"  {index}. {row['sender_name']} {row['points']} 分"
                )
        return "\n".join(lines)

    def arena_board(self, chat_id: str, season: str | None = None,
                    limit: int = 10) -> list[dict]:
        season = season or local_week(_now())
        rows = _rows(
            self._store,
            """SELECT sender_id, sender_name, points, wins, losses
               FROM arena_entries
               WHERE chat_id = ? AND season = ?
               ORDER BY points DESC, wins DESC, sender_id ASC LIMIT ?""",
            (str(chat_id), str(season), int(limit)),
        )
        return [dict(row) for row in rows]

    def arena_challenge(self, chat_id: str, sender_id: str,
                        sender_name: str, rng=None) -> str:
        now = _now()
        season = local_week(now)
        day = local_day(now)
        rebirth = self._rebirth_cycle(chat_id, sender_id)
        limit = ARENA_DAILY_ATTEMPTS + rebirth
        used = self._arena_used_today(chat_id, sender_id, day)
        if used >= limit:
            return f"今日擂台次数已用完（{limit} 次），明天再来。"
        opponents = [
            member for member in self._store.members(chat_id)
            if member["sender_id"] != sender_id
        ]
        if not opponents:
            return "本群还没有可匹配的对手。"
        opponent = opponents[int(_roll(rng) * len(opponents)) % len(opponents)]
        attacker = self._duels.power(chat_id, sender_id)
        defender = self._duels.power(chat_id, opponent["sender_id"])
        chance = _win_probability(attacker.attack, defender.defense)
        win = _roll(rng) < chance
        delta = ARENA_WIN_POINTS if win else -ARENA_LOSS_POINTS
        self._record(chat_id, sender_id, sender_name, "arena", "arena")
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO arena_entries
                   (chat_id, season, sender_id, sender_name, points,
                    wins, losses, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, season, sender_id) DO UPDATE SET
                     points = MAX(0, points + excluded.points),
                     wins = wins + excluded.wins,
                     losses = losses + excluded.losses,
                     sender_name = excluded.sender_name,
                     updated_at = excluded.updated_at""",
                (str(chat_id), season, str(sender_id),
                 str(sender_name or ""), delta,
                 1 if win else 0, 0 if win else 1, now),
            )
        entry = self._arena_entry(chat_id, season, sender_id)
        result = "胜利" if win else "落败"
        reward = ARENA_WIN_POINTS if win else 0
        if win:
            self._grant(chat_id, sender_id, sender_name, 10,
                        "arena_win", "arena")
        return (
            f"擂台挑战 {sender_name} vs {opponent['sender_name']}："
            f"{result}（胜率 {round(chance * 100)}%，"
            f"{'+' if delta >= 0 else ''}{delta} 分）\n"
            f"当前 {entry['points']} 分 · 剩余 "
            f"{max(0, limit - used - 1)} 次。"
        )

    def arena_claim(self, chat_id: str, sender_id: str,
                    sender_name: str) -> str:
        now = _now()
        season = local_week(now)
        board = self.arena_board(chat_id, season, limit=100)
        rank = next(
            (index for index, row in enumerate(board, 1)
             if row["sender_id"] == sender_id), 0)
        entry = self._arena_entry(chat_id, season, sender_id)
        if rank == 0 or rank > len(ARENA_REWARDS) or \
                entry["points"] < ARENA_MIN_POINTS_FOR_REWARD:
            return (
                f"擂台奖励需要进入前 {len(ARENA_REWARDS)} 名且至少 "
                f"{ARENA_MIN_POINTS_FOR_REWARD} 分。"
            )
        rows = _rows(
            self._store,
            """SELECT 1 FROM arena_claims
               WHERE chat_id = ? AND season = ? AND sender_id = ?""",
            (str(chat_id), str(season), str(sender_id)),
        )
        if rows:
            return "本赛季擂台奖励已经领取过了。"
        reward = ARENA_REWARDS[min(rank - 1, len(ARENA_REWARDS) - 1)]
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO arena_claims
                   (chat_id, season, sender_id, rank, reward, claimed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(season), str(sender_id), rank,
                 reward, now),
            )
        self._grant(chat_id, sender_id, sender_name, reward,
                    "arena_reward", "arena")
        return (
            f"领取成功：本周擂台第 {rank} 名，获得 {reward} 灵石。"
        )

    # ── Guild ────────────────────────────────────────────────────

    def _guild(self, chat_id: str) -> dict | None:
        rows = _rows(
            self._store,
            """SELECT name, level, exp, funds FROM guilds
               WHERE chat_id = ?""",
            (str(chat_id),),
        )
        if not rows:
            return None
        return {"name": str(rows[0][0]), "level": int(rows[0][1] or 1),
                "exp": int(rows[0][2] or 0), "funds": int(rows[0][3] or 0)}

    def _guild_member(self, chat_id: str, sender_id: str) -> dict | None:
        rows = _rows(
            self._store,
            """SELECT role, contribution FROM guild_members
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        if not rows:
            return None
        return {"role": str(rows[0][0]), "contribution": int(rows[0][1] or 0)}

    def guild_status(self, chat_id: str, sender_id: str,
                     sender_name: str) -> str:
        guild = self._guild(chat_id)
        if guild is None:
            return "本群还没有门派。发送「创建门派 名称」创建（500 灵石）。"
        member = self._guild_member(chat_id, sender_id)
        members = _rows(
            self._store,
            """SELECT COUNT(*), COALESCE(SUM(contribution), 0)
               FROM guild_members WHERE chat_id = ?""",
            (str(chat_id),),
        )
        count = int(members[0][0] or 0) if members else 0
        contribution = int(members[0][1] or 0) if members else 0
        bonus = guild["level"] * GUILD_BONUS_PER_LEVEL
        lines = [
            f"【{guild['name']}】Lv{guild['level']} · "
            f"门派资金 {guild['funds']}",
            f"成员 {count} 人 · 总贡献 {contribution} · "
            f"帮派加成 攻防 +{round(bonus * 100)}%",
        ]
        if member:
            lines.append(
                f"你的身份：{member['role']} · 贡献 {member['contribution']}"
            )
        else:
            lines.append("你还没有加入门派，发送「加入门派」加入。")
        lines.append("发送「捐献门派 数量」提升门派等级。")
        return "\n".join(lines)

    def guild_create(self, chat_id: str, sender_id: str,
                     sender_name: str, raw: str) -> str:
        name = str(raw or "").strip()
        if not name:
            return "用法：创建门派 名称"
        if self._guild(chat_id) is not None:
            return "本群已经有门派了，发送「加入门派」加入。"
        if self._guild_member(chat_id, sender_id) is not None:
            return "你已经加入了门派。"
        if self._balance(chat_id, sender_id) < GUILD_CREATE_COST:
            return f"创建门派需要 {GUILD_CREATE_COST} 灵石。"
        if not self._spend(chat_id, sender_id, sender_name,
                           GUILD_CREATE_COST, "guild_create"):
            return "灵石不足。"
        now = _now()
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO guilds
                   (chat_id, name, level, exp, funds, created_at)
                   VALUES (?, ?, 1, 0, 0, ?)""",
                (str(chat_id), name[:12], now),
            )
            self._store.conn.execute(
                """INSERT INTO guild_members
                   (chat_id, sender_id, role, contribution, joined_at)
                   VALUES (?, ?, 'leader', 0, ?)""",
                (str(chat_id), str(sender_id), now),
            )
        return f"门派「{name}」创建成功，你成为掌门。"

    def guild_join(self, chat_id: str, sender_id: str,
                   sender_name: str) -> str:
        guild = self._guild(chat_id)
        if guild is None:
            return "本群还没有门派，先「创建门派 名称」。"
        if self._guild_member(chat_id, sender_id) is not None:
            return "你已经加入了门派。"
        now = _now()
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO guild_members
                   (chat_id, sender_id, role, contribution, joined_at)
                   VALUES (?, ?, 'member', 0, ?)""",
                (str(chat_id), str(sender_id), now),
            )
        return f"已加入门派「{guild['name']}」。"

    def guild_leave(self, chat_id: str, sender_id: str,
                    sender_name: str) -> str:
        member = self._guild_member(chat_id, sender_id)
        if member is None:
            return "你还没有加入门派。"
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """DELETE FROM guild_members
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            )
            if member["role"] == "leader":
                rows = self._store.conn.execute(
                    """SELECT sender_id FROM guild_members
                       WHERE chat_id = ? ORDER BY joined_at ASC LIMIT 1""",
                    (str(chat_id),),
                ).fetchone()
                if rows:
                    self._store.conn.execute(
                        """UPDATE guild_members SET role = 'leader'
                           WHERE chat_id = ? AND sender_id = ?""",
                        (str(chat_id), str(rows[0])),
                    )
                else:
                    self._store.conn.execute(
                        "DELETE FROM guilds WHERE chat_id = ?",
                        (str(chat_id),),
                    )
        return "已退出门派。"

    def guild_donate(self, chat_id: str, sender_id: str,
                     sender_name: str, raw: str) -> str:
        try:
            amount = int(str(raw or "").strip())
        except ValueError:
            return "用法：捐献门派 数量"
        if amount <= 0 or amount > 100000:
            return "捐献数量必须在 1~100000 之间。"
        if self._guild_member(chat_id, sender_id) is None:
            return "你先加入门派才能捐献。"
        if not self._spend(chat_id, sender_id, sender_name, amount,
                           "guild_donate"):
            return "灵石不足。"
        now = _now()
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """UPDATE guilds SET funds = funds + ?, exp = exp + ?
                   WHERE chat_id = ?""",
                (amount, amount, str(chat_id)),
            )
            self._store.conn.execute(
                """UPDATE guild_members
                   SET contribution = contribution + ?
                   WHERE chat_id = ? AND sender_id = ?""",
                (amount, str(chat_id), str(sender_id)),
            )
            row = self._store.conn.execute(
                """SELECT level, exp FROM guilds WHERE chat_id = ?""",
                (str(chat_id),),
            ).fetchone()
            level = int(row[0] or 1)
            exp = int(row[1] or 0)
            while level < GUILD_MAX_LEVEL and \
                    exp >= GUILD_EXP_PER_LEVEL * level:
                exp -= GUILD_EXP_PER_LEVEL * level
                level += 1
            self._store.conn.execute(
                """UPDATE guilds SET level = ?, exp = ?
                   WHERE chat_id = ?""",
                (level, exp, str(chat_id)),
            )
        return (
            f"捐献 {amount} 灵石，门派当前 Lv{level}，"
            f"你的贡献 +{amount}。"
        )

    # ── Market ───────────────────────────────────────────────────

    def _expire_listings(self, chat_id: str) -> None:
        now = _now()
        rows = _rows(
            self._store,
            """SELECT id, seller_id, item_id, enhance_level
               FROM market_listings
               WHERE chat_id = ? AND status = 'open' AND expires_at <= ?""",
            (str(chat_id), now),
        )
        if not rows:
            return
        with self._store.lock, self._store.conn:
            for listing_id, seller_id, item_id, enhance_level in rows:
                self._store.conn.execute(
                    """UPDATE market_listings SET status = 'expired'
                       WHERE id = ?""",
                    (int(listing_id),),
                )
                self._store.conn.execute(
                    """INSERT OR IGNORE INTO equipment_owned
                       (chat_id, sender_id, item_id, purchased_at, equipped,
                        enhance_level)
                       VALUES (?, ?, ?, ?, 0, ?)""",
                    (str(chat_id), str(seller_id), str(item_id), now,
                     int(enhance_level or 0)),
                )

    def market_status(self, chat_id: str, sender_id: str,
                      sender_name: str) -> str:
        self._expire_listings(chat_id)
        rows = _rows(
            self._store,
            """SELECT id, seller_name, item_id, price, expires_at
               FROM market_listings
               WHERE chat_id = ? AND status = 'open'
               ORDER BY price ASC, id ASC LIMIT 30""",
            (str(chat_id),),
        )
        if not rows:
            return "交易行暂时没有挂单。发送「上架 装备名 价格」出售装备。"
        lines = [f"交易行 · 已过期自动退回（手续费 {int(MARKET_FEE_RATIO*100)}%）"]
        for row in rows:
            item = self._catalog.by_id(str(row[2]))
            name = item["name"] if item else str(row[2])
            lines.append(
                f"#{row[0]} {name} · {row[3]} 灵石 · "
                f"卖家 {row[1] or '未知'}"
            )
        lines.append("发送「购买挂单 编号」购买；「下架挂单 编号」撤回。")
        return "\n".join(lines)

    def market_sell(self, chat_id: str, sender_id: str,
                    sender_name: str, raw: str) -> str:
        parts = str(raw or "").split()
        if len(parts) != 2:
            return "用法：上架 装备名 价格"
        item = self._catalog.find(parts[0])
        if item is None:
            return f"没有找到「{parts[0]}」。"
        try:
            price = int(parts[1])
        except ValueError:
            return "价格必须是整数。"
        if price <= 0 or price > 1000000:
            return "价格必须在 1~1000000 之间。"
        if not self._store.owns(chat_id, sender_id, item["id"]):
            return f"你还没有 {item['name']}。"
        owned = next(
            (row for row in self._store.owned_items(chat_id, sender_id)
             if row["item_id"] == item["id"]), None)
        if owned and owned.get("equipped"):
            return f"{item['name']} 正在装备中，请先「卸下 {item['name']}」。"
        now = _now()
        with self._store.lock, self._store.conn:
            cur = self._store.conn.execute(
                """DELETE FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?
                     AND equipped = 0""",
                (str(chat_id), str(sender_id), item["id"]),
            )
            if cur.rowcount == 0:
                return "上架失败：装备状态已变化。"
            self._store.conn.execute(
                """DELETE FROM equipment_affixes
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), item["id"]),
            )
            self._store.conn.execute(
                """INSERT INTO market_listings
                   (chat_id, seller_id, seller_name, item_id, price,
                    status, enhance_level, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 item["id"], price,
                 int(owned.get("enhance_level", 0) or 0) if owned else 0,
                 now, now + MARKET_LIST_DAYS * 86400),
            )
        return f"已上架 {item['name']}，价格 {price} 灵石。"

    def market_buy(self, chat_id: str, sender_id: str,
                   sender_name: str, raw: str) -> str:
        try:
            listing_id = int(str(raw or "").strip())
        except ValueError:
            return "用法：购买挂单 编号"
        self._expire_listings(chat_id)
        rows = _rows(
            self._store,
            """SELECT seller_id, seller_name, item_id, price, enhance_level
               FROM market_listings
               WHERE id = ? AND chat_id = ? AND status = 'open'""",
            (listing_id, str(chat_id)),
        )
        if not rows:
            return "挂单不存在或已售出。"
        seller_id, seller_name, item_id, price = (
            str(rows[0][0]), str(rows[0][1]), str(rows[0][2]),
            int(rows[0][3]))
        enhance_level = int(rows[0][4] or 0)
        if seller_id == sender_id:
            return "不能购买自己的挂单。"
        if self._store.owns(chat_id, sender_id, item_id):
            return "你已经拥有这件装备了。"
        if self._balance(chat_id, sender_id) < price:
            return f"灵石不足：需要 {price} 灵石。"
        net = int(price * (1 - MARKET_FEE_RATIO))
        with self._store.lock, self._store.conn:
            cur = self._store.conn.execute(
                """UPDATE market_listings SET status = 'sold',
                        buyer_id = ?
                   WHERE id = ? AND status = 'open'""",
                (str(sender_id), listing_id),
            )
            if cur.rowcount == 0:
                return "挂单已被购买。"
            paid = self._store.conn.execute(
                """INSERT OR IGNORE INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   SELECT ?, ?, ?, ?, 'market_buy', 'spend', ?, ?, ?, ?
                   WHERE (SELECT COALESCE(SUM(points), 0) FROM point_events
                          WHERE chat_id = ? AND sender_id = ?) >= ?""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 f"market:{listing_id}", -price, local_day(),
                 local_season(), _now(), str(chat_id), str(sender_id),
                 price),
            )
            if paid.rowcount == 0:
                self._store.conn.execute(
                    """UPDATE market_listings SET status = 'open',
                            buyer_id = '' WHERE id = ?""",
                    (listing_id,),
                )
                return "灵石不足。"
            self._store.conn.execute(
                """INSERT INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   VALUES (?, ?, ?, ?, 'market_sell', 'market', ?, ?, ?, ?)""",
                (str(chat_id), seller_id, seller_name or "",
                 f"market:{listing_id}:seller", net, local_day(),
                 local_season(), _now()),
            )
            self._store.conn.execute(
                """INSERT INTO equipment_owned
                   (chat_id, sender_id, item_id, purchased_at, equipped,
                    enhance_level)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                (str(chat_id), str(sender_id), item_id, _now(),
                 enhance_level),
            )
        item = self._catalog.by_id(item_id)
        name = item["name"] if item else item_id
        return (
            f"购买成功：{name}，花费 {price} 灵石"
            f"（卖家实收 {net}，手续费 {price - net}）。"
            f"已放入背包，可发「装备 {name}」。"
        )

    def market_cancel(self, chat_id: str, sender_id: str,
                      sender_name: str, raw: str) -> str:
        try:
            listing_id = int(str(raw or "").strip())
        except ValueError:
            return "用法：下架挂单 编号"
        rows = _rows(
            self._store,
            """SELECT item_id, enhance_level FROM market_listings
               WHERE id = ? AND chat_id = ? AND seller_id = ?
                 AND status = 'open'""",
            (listing_id, str(chat_id), str(sender_id)),
        )
        if not rows:
            return "没有找到你的这个挂单。"
        item_id = str(rows[0][0])
        enhance_level = int(rows[0][1] or 0)
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """UPDATE market_listings SET status = 'cancelled'
                   WHERE id = ?""", (listing_id,))
            self._store.conn.execute(
                """INSERT OR IGNORE INTO equipment_owned
                   (chat_id, sender_id, item_id, purchased_at, equipped,
                    enhance_level)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                (str(chat_id), str(sender_id), item_id, _now(),
                 enhance_level),
            )
        item = self._catalog.by_id(item_id)
        return f"已下架 {item['name'] if item else item_id}，装备已退回背包。"

    # ── Pets ─────────────────────────────────────────────────────

    def pet_status(self, chat_id: str, sender_id: str,
                   sender_name: str) -> str:
        rows = _rows(
            self._store,
            """SELECT species_id, level, exp FROM pets
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        if not rows:
            names = "、".join(
                f"{p['name']}({p['price']}灵石)" for p in PET_SPECIES)
            return (
                f"{sender_name} 还没有宠物。可领养：{names}。"
                "发送「领养宠物 名称」。"
            )
        species = _pet_species(str(rows[0][0]))
        level = int(rows[0][1] or 1)
        exp = int(rows[0][2] or 0)
        if species is None:
            return "宠物数据异常，发送「放生宠物」后重新领养。"
        return (
            f"{sender_name} 的宠物：{species['name']} Lv{level}"
            f"（经验 {exp}/{PET_EXP_PER_LEVEL}）\n"
            f"加成：攻击 +{species['attack'] * level} · "
            f"防御 +{species['defense'] * level} · "
            f"切磋胜率 +{round(species['duel_win'] * level * 100, 1)}%\n"
            f"发送「培养宠物」提升等级（费用随等级增加）。"
        )

    def pet_adopt(self, chat_id: str, sender_id: str,
                  sender_name: str, raw: str) -> str:
        name = str(raw or "").strip()
        species = next(
            (p for p in PET_SPECIES
             if p["name"] == name or p["id"] == name), None)
        if species is None:
            names = "、".join(p["name"] for p in PET_SPECIES)
            return f"没有这种宠物。可领养：{names}。"
        if self._pet_exists(chat_id, sender_id):
            return "你已经有一只宠物了，先「放生宠物」。"
        if not self._spend(chat_id, sender_id, sender_name,
                           species["price"], "pet_adopt"):
            return f"灵石不足：领养 {species['name']} 需要 {species['price']} 灵石。"
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO pets
                   (chat_id, sender_id, species_id, level, exp, updated_at)
                   VALUES (?, ?, ?, 1, 0, ?)""",
                (str(chat_id), str(sender_id), species["id"], _now()),
            )
        return f"领养成功：{species['name']}（{species['desc']}）。"

    def pet_train(self, chat_id: str, sender_id: str,
                  sender_name: str, rng=None) -> str:
        rows = _rows(
            self._store,
            """SELECT species_id, level, exp FROM pets
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        if not rows:
            return "你还没有宠物，发送「领养宠物 名称」。"
        species = _pet_species(str(rows[0][0]))
        level = int(rows[0][1] or 1)
        exp = int(rows[0][2] or 0)
        if species is None:
            return "宠物数据异常。"
        if level >= PET_MAX_LEVEL:
            return f"{species['name']} 已经达到最高 Lv{PET_MAX_LEVEL}。"
        cost = 50 * level
        if not self._spend(chat_id, sender_id, sender_name, cost,
                           "pet_train"):
            return f"灵石不足：培养需要 {cost} 灵石。"
        gain = 1 + int(_roll(rng) * 2)
        exp += gain
        while exp >= PET_EXP_PER_LEVEL and level < PET_MAX_LEVEL:
            exp -= PET_EXP_PER_LEVEL
            level += 1
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """UPDATE pets SET level = ?, exp = ?, updated_at = ?
                   WHERE chat_id = ? AND sender_id = ?""",
                (level, exp, _now(), str(chat_id), str(sender_id)),
            )
        return (
            f"培养成功：{species['name']} 经验 +{gain}，"
            f"当前 Lv{level}（{exp}/{PET_EXP_PER_LEVEL}），"
            f"花费 {cost} 灵石。"
        )

    def pet_release(self, chat_id: str, sender_id: str,
                    sender_name: str) -> str:
        if not self._pet_exists(chat_id, sender_id):
            return "你还没有宠物。"
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """DELETE FROM pets
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            )
        return "已放生宠物。"

    def _pet_exists(self, chat_id: str, sender_id: str) -> bool:
        rows = _rows(
            self._store,
            """SELECT 1 FROM pets WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        return bool(rows)

    # ── Alchemy ──────────────────────────────────────────────────

    def _alchemy(self, chat_id: str, sender_id: str) -> dict:
        rows = _rows(
            self._store,
            """SELECT herbs, last_gather FROM alchemy_state
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        if not rows:
            return {"herbs": 0, "last_gather": 0}
        return {"herbs": int(rows[0][0] or 0),
                "last_gather": int(rows[0][1] or 0)}

    def alchemy_status(self, chat_id: str, sender_id: str,
                       sender_name: str) -> str:
        state = self._alchemy(chat_id, sender_id)
        now = _now()
        wait = max(0, GATHER_COOLDOWN - (now - state["last_gather"]))
        lines = [
            f"{sender_name} 的炼丹炉：草药 {state['herbs']} 株",
            ("可采集" if wait == 0 else f"{wait // 60} 分钟后可采集"),
        ]
        for pill in PILLS:
            definition = skill_by_id(pill["id"])
            count = self._item_count(chat_id, sender_id, "pill", pill["id"])
            lines.append(
                f"· {pill['name']} ×{count}："
                f"{definition['desc'] if definition else ''} "
                f"（炼丹需 {pill['herbs']} 草药 + {pill['price']} 灵石）"
            )
        lines.append("发送「采集」「炼丹 丹药名」「服用 丹药名」。")
        return "\n".join(lines)

    def gather(self, chat_id: str, sender_id: str,
               sender_name: str, rng=None) -> str:
        state = self._alchemy(chat_id, sender_id)
        now = _now()
        wait = GATHER_COOLDOWN - (now - state["last_gather"])
        if state["last_gather"] and wait > 0:
            return f"采集冷却中，{wait // 60} 分钟后可再次采集。"
        amount = 1 + int(_roll(rng) * 3)
        herbs = min(GATHER_MAX_HERBS, state["herbs"] + amount)
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO alchemy_state
                   (chat_id, sender_id, herbs, last_gather, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     herbs = excluded.herbs,
                     last_gather = excluded.last_gather,
                     updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), herbs, now, now),
            )
        return f"采集到 {amount} 株草药，当前 {herbs} 株。"

    def craft_pill(self, chat_id: str, sender_id: str,
                   sender_name: str, raw: str) -> str:
        name = str(raw or "").strip()
        pill = next(
            (p for p in PILLS if p["name"] == name or p["id"] == name),
            None)
        if pill is None:
            return "没有这种丹药，发「丹药」查看配方。"
        state = self._alchemy(chat_id, sender_id)
        if state["herbs"] < pill["herbs"]:
            return (
                f"草药不足：炼制 {pill['name']} 需要 {pill['herbs']} 株，"
                f"你有 {state['herbs']} 株。"
            )
        if self._balance(chat_id, sender_id) < pill["price"]:
            return f"灵石不足：炼制 {pill['name']} 需要 {pill['price']} 灵石。"
        with self._store.lock, self._store.conn:
            cur = self._store.conn.execute(
                """UPDATE alchemy_state SET herbs = herbs - ?,
                        updated_at = ?
                   WHERE chat_id = ? AND sender_id = ? AND herbs >= ?""",
                (pill["herbs"], _now(), str(chat_id), str(sender_id),
                 pill["herbs"]),
            )
            if cur.rowcount == 0:
                return "草药不足。"
        if not self._spend(chat_id, sender_id, sender_name, pill["price"],
                           f"craft:{pill['id']}"):
            with self._store.lock, self._store.conn:
                self._store.conn.execute(
                    """UPDATE alchemy_state SET herbs = herbs + ?,
                            updated_at = ?
                       WHERE chat_id = ? AND sender_id = ?""",
                    (pill["herbs"], _now(), str(chat_id),
                     str(sender_id)),
                )
            return "灵石不足。"
        self._add_item(chat_id, sender_id, "pill", pill["id"], 1)
        return f"炼制成功：{pill['name']} ×1。"

    def use_pill(self, chat_id: str, sender_id: str,
                 sender_name: str, raw: str) -> str:
        name = str(raw or "").strip()
        pill = next(
            (p for p in PILLS if p["name"] == name or p["id"] == name),
            None)
        if pill is None:
            return "没有这种丹药，发「丹药」查看。"
        if not self._consume_item(chat_id, sender_id, "pill", pill["id"]):
            return f"你没有 {pill['name']}，先发「炼丹 {pill['name']}」。"
        definition = skill_by_id(pill["id"])
        now = _now()
        ok, code, cooldown_until, _ = self._store.cast_skill(
            chat_id, sender_id, pill["id"],
            int(definition["cooldown"]), int(definition["duration"]), now)
        if not ok:
            self._add_item(chat_id, sender_id, "pill", pill["id"], 1)
            wait = max(0, cooldown_until - now)
            return f"{pill['name']} 还在冷却中，{wait // 60} 分钟后可再服用。"
        detail = format_skill_effects(definition["effects"])
        return f"服用 {pill['name']}：{detail}，持续 10 分钟。"

    # ── Rebirth ──────────────────────────────────────────────────

    def _rebirth_cycle(self, chat_id: str, sender_id: str) -> int:
        rows = _rows(
            self._store,
            """SELECT cycle FROM rebirths
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        )
        return int(rows[0][0] or 0) if rows else 0

    def rebirth_status(self, chat_id: str, sender_id: str,
                       sender_name: str) -> str:
        cycle = self._rebirth_cycle(chat_id, sender_id)
        realm = self._store.realm_of(chat_id, sender_id)
        earned = self._store.earned_of(chat_id, sender_id)
        level = self._levels.for_points(earned)
        bonus = cycle * REBIRTH_BONUS_PER_CYCLE
        lines = [
            f"{sender_name} 的轮回：第 {cycle} 世",
            f"当前境界 {realm} · Lv{level['level']} {level['title']}",
            f"转世加成：攻防/Boss 伤害 +{round(bonus * 100)}%，"
            f"每日秘境/擂台次数 +{cycle}",
        ]
        if realm >= REBIRTH_REALM_REQUIRED and \
                int(level["level"]) >= REBIRTH_LEVEL_REQUIRED:
            lines.append(
                f"可以转世：消耗 {REBIRTH_COST} 灵石，境界重置为凡人，"
                "获得永久轮回加成。发送「转世确认」。"
            )
        else:
            lines.append(
                f"转世条件：境界 ≥ {REBIRTH_REALM_REQUIRED}（散仙）且 "
                f"Lv{REBIRTH_LEVEL_REQUIRED}。"
            )
        return "\n".join(lines)

    def rebirth(self, chat_id: str, sender_id: str,
                sender_name: str) -> str:
        realm = self._store.realm_of(chat_id, sender_id)
        earned = self._store.earned_of(chat_id, sender_id)
        level = self._levels.for_points(earned)
        if realm < REBIRTH_REALM_REQUIRED or \
                int(level["level"]) < REBIRTH_LEVEL_REQUIRED:
            return (
                f"转世条件不足：需要境界 ≥ {REBIRTH_REALM_REQUIRED} 且 "
                f"Lv{REBIRTH_LEVEL_REQUIRED}。"
            )
        if not self._spend(chat_id, sender_id, sender_name,
                           REBIRTH_COST, "rebirth"):
            return f"灵石不足：转世需要 {REBIRTH_COST} 灵石。"
        cycle = self._rebirth_cycle(chat_id, sender_id) + 1
        now = _now()
        with self._store.lock, self._store.conn:
            self._store.conn.execute(
                """INSERT INTO rebirths
                   (chat_id, sender_id, cycle, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     cycle = excluded.cycle,
                     updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), cycle, now),
            )
            self._store.conn.execute(
                """INSERT INTO member_realms
                   (chat_id, sender_id, realm, updated_at)
                   VALUES (?, ?, 0, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     realm = 0, updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), now),
            )
        return (
            f"转世成功！进入第 {cycle} 世，境界重置为凡人，"
            f"永久加成提升到 +{round(cycle * REBIRTH_BONUS_PER_CYCLE * 100)}%。"
        )

    # ── World & cross-board ──────────────────────────────────────

    def world_status(self, chat_id: str, sender_name: str) -> str:
        state = world_state(chat_id)
        weather = state["weather"]
        event = state["event"]
        lines = [
            f"今日天象：{weather['name']} — {weather['desc']}",
            f"当前世界事件：{event['name']} — {event['desc']}",
        ]
        keys = {"fishing_rare": "钓鱼稀有", "fishing_value": "渔获价值",
                "attack_pct": "攻击", "defense_pct": "防御",
                "boss_damage": "Boss 伤害", "lottery_luck": "抽奖气运"}
        effects = state["effects"]
        if effects:
            lines.append("当前加成：" + "、".join(
                f"{keys.get(key, key)} +{round(value * 100)}%"
                for key, value in effects.items() if value
            ))
        return "\n".join(lines)

    def cross_board(self, sender_name: str, limit: int = 10) -> str:
        rows = _rows(
            self._store,
            """SELECT sender_id,
                      (SELECT p2.sender_name FROM point_events p2
                       WHERE p2.sender_id = p.sender_id
                         AND p2.sender_name != ''
                       ORDER BY p2.id DESC LIMIT 1) AS sender_name,
                      SUM(points) AS pts
               FROM point_events p
               WHERE category IN ('earn', 'adjust')
               GROUP BY sender_id
               ORDER BY pts DESC LIMIT ?""",
            (int(limit),),
        )
        if not rows:
            return "还没有跨群修炼数据。"
        lines = ["跨群修炼榜"]
        for index, row in enumerate(rows, 1):
            lines.append(
                f"{index}. {row[1] or row[0]} {int(row[2] or 0)} 修炼值"
            )
        return "\n".join(lines)


def _pet_species(species_id: str) -> dict | None:
    return next(
        (item for item in PET_SPECIES if item["id"] == species_id), None)
