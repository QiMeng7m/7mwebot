"""世界 Boss / 群副本 — a weekly cooperative target per group.

The boss is summoned on the first hit of each ISO week.  Its HP scales
with the group's member count, every member gets a few attempts per day,
and damage is based on combat power.  Killing the boss pays a balance
pool split by damage contribution (``category='boss'``, balance only).
"""

from __future__ import annotations

import logging
import random

from .util import (
    days_until_week_end,
    is_group_enabled,
    local_day,
    local_week,
)

logger = logging.getLogger(__name__)

BOSS_NAMES: list[str] = [
    "混沌巨兽", "深渊魔龙", "远古石像", "九尾妖狐",
    "熔岩领主", "寒霜巨人", "暗影刺客", "天灾虫母",
    "雷罚麒麟", "虚空吞噬者",
]

BOSS_CRIT_CHANCE = 0.10
BOSS_CRIT_MULTIPLIER = 2.0
BOSS_REWARD_MIN = 100


def boss_name_for(chat_id: str, season: str) -> str:
    """Stable boss name for one group/week (no hash randomization)."""
    seed = sum(ord(ch) for ch in f"{chat_id}:{season}")
    return BOSS_NAMES[seed % len(BOSS_NAMES)]


def roll_damage(power: int, roll: float,
                crit_roll: float) -> tuple[int, bool]:
    """Damage for one hit: 80%~120% of power, 10% chance to double."""
    power = max(1, int(power))
    roll = min(max(float(roll), 0.0), 0.999999)
    damage = power * (0.8 + 0.4 * roll)
    crit = float(crit_roll) < BOSS_CRIT_CHANCE
    if crit:
        damage *= BOSS_CRIT_MULTIPLIER
    return max(1, int(round(damage))), crit


def boss_hp_for(members: int, config) -> int:
    """HP scales with group size, within configured bounds."""
    per_member = max(1, int(getattr(config, "boss_hp_per_member", 600)))
    minimum = max(1, int(getattr(config, "boss_min_hp", 3000)))
    maximum = max(minimum, int(getattr(config, "boss_max_hp", 30000)))
    return max(minimum, min(maximum, max(1, int(members)) * per_member))


class BossService:
    """Resolves 「挑战Boss」 against the store."""

    def __init__(self, store, config, duels):
        self._store = store
        self._config = config
        self._duels = duels

    # ── Helpers ───────────────────────────────────────────────────

    def is_enabled(self, chat_id: str, group_name: str) -> bool:
        if not getattr(self._config, "boss_enabled", False):
            return False
        return is_group_enabled(
            chat_id, group_name,
            getattr(self._config, "boss_groups", ["*"]),
        )

    def _state(self, chat_id: str, now: int) -> dict:
        season = local_week(now)
        state = self._store.boss_state(chat_id, season)
        if state is not None:
            return state
        hp = boss_hp_for(
            len(self._store.members(chat_id)), self._config)
        return self._store.ensure_boss_state(
            chat_id, season, boss_name_for(chat_id, season), hp, now,
        )

    def _attempts_limit(self) -> int:
        return max(1, int(getattr(self._config, "boss_daily_attempts", 3)))

    # ── Public API ────────────────────────────────────────────────

    def status(self, chat_id: str, group_name: str, sender_id: str = "",
               now_ts: int | None = None) -> dict:
        now = _now() if now_ts is None else int(now_ts)
        if not self.is_enabled(chat_id, group_name):
            return {"ok": False, "code": "disabled",
                    "reply": "群 Boss 玩法未开启。"}
        state = self._state(chat_id, now)
        season = state["season"]
        board = self._store.boss_damage_board(chat_id, season, 10)
        totals = self._store.boss_totals(chat_id, season)
        return {
            "ok": True,
            "code": "ok",
            "season": season,
            "boss_name": state["boss_name"],
            "hp_total": int(state["hp_total"]),
            "hp_left": int(state["hp_left"]),
            "killed": bool(state["killed_at"]),
            "days_left": days_until_week_end(now),
            "attempts_used": self._store.boss_attempts(
                chat_id, season, sender_id, local_day(now)),
            "attempts_limit": self._attempts_limit(),
            "my_damage": self._store.boss_member_damage(
                chat_id, season, sender_id),
            "participants": int(totals.get("participants", 0)),
            "total_damage": int(totals.get("damage", 0)),
            "board": board,
            "reward_pool": self._reward_pool(state),
        }

    def attack(self, msg: dict, now_ts: int | None = None,
               rng=None) -> dict:
        now = _now() if now_ts is None else int(now_ts)
        chat_id = str(msg.get("chat_id") or "")
        group_name = str(msg.get("group_name") or "")
        sender_id = str(msg.get("sender_id") or "")
        sender_name = str(msg.get("sender_name") or "")
        message_id = str(msg.get("message_id") or "")

        if not self.is_enabled(chat_id, group_name):
            return {"ok": False, "code": "disabled",
                    "reply": "群 Boss 玩法未开启。"}

        state = self._state(chat_id, now)
        season = state["season"]
        if state["killed_at"]:
            return {
                "ok": False, "code": "killed",
                "reply": f"本周的 {state['boss_name']} 已经被击败了，"
                         "下周会有新的 Boss。",
                "boss_name": state["boss_name"],
            }

        limit = self._attempts_limit()
        day = local_day(now)
        used = self._store.boss_attempts(chat_id, season, sender_id, day)
        if used >= limit:
            return {
                "ok": False, "code": "attempts",
                "reply": f"今天的挑战次数已用完（{limit} 次），明天再来。",
                "attempts_used": used, "attempts_limit": limit,
            }

        power = self._duels.power(chat_id, sender_id)
        damage, crit = roll_damage(
            power.total, _roll(rng), _roll(rng),
        )
        inserted, hp_left, damage = self._store.boss_attack(
            chat_id, season, message_id or f"boss:{chat_id}:{now}",
            sender_id, sender_name, damage, crit, day, now,
        )
        if not inserted:
            return {"ok": False, "code": "duplicate",
                    "reply": "这次攻击已经记录过了，请稍后再试。"}

        killed = hp_left <= 0
        rewards: list[dict] = []
        if killed and self._store.boss_kill(chat_id, season, now):
            rewards = self._store.boss_distribute_rewards(
                chat_id, season, self._reward_pool(state), now,
            )
        totals = self._store.boss_totals(chat_id, season)
        return {
            "ok": True,
            "code": "ok",
            "season": season,
            "boss_name": state["boss_name"],
            "damage": damage,
            "crit": crit,
            "hp_total": int(state["hp_total"]),
            "hp_left": hp_left,
            "killed": killed,
            "power": power.total,
            "attempts_used": used + 1,
            "attempts_limit": limit,
            "my_damage": self._store.boss_member_damage(
                chat_id, season, sender_id),
            "participants": int(totals.get("participants", 0)),
            "total_damage": int(totals.get("damage", 0)),
            "rewards": rewards,
            "reward_pool": self._reward_pool(state),
        }

    def _reward_pool(self, state: dict) -> int:
        ratio = float(getattr(self._config, "boss_reward_ratio", 0.15))
        return max(BOSS_REWARD_MIN,
                   int(int(state["hp_total"]) * max(0.0, ratio)))


def _now() -> int:
    import time
    return int(time.time())


def _roll(rng) -> float:
    if rng is None:
        return random.random()
    if hasattr(rng, "random"):
        return float(rng.random())
    return float(rng())
