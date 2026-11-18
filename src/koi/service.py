"""星盘玩法服务层 — 求签 / 观星 / 放生 / 转运 / 开光 / 星盘 / 锦鲤池 / 社交。

本模块只依赖 :class:`KoiStore` 与 ``BotConfig``，不直接收发微信消息。
所有回复都是纯文本，由 :class:`KoiHandler` 统一加昵称前缀。
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import time

from src.game_utils import is_group_enabled, local_day, local_week

from . import bazi as bazi_rules
from . import rules
from .signs import all_sign_ids, sign_by_id, signs_by_level
from .store import KoiStore

logger = logging.getLogger(__name__)

_EMPTY_DAILY = {
    "sign_id": 0,
    "sign_level": "",
    "luck": 0,
    "yi": "",
    "streak_days": 0,
    "rerolls": 0,
    "release_used": 0,
    "draws_used": 0,
    "chat_merit": 0,
    "pool_in": 0,
    "observed": 0,
    "duel_star_used": 0,
    "extra_luck": 0,
    "fortune_used": 0,
    "hexagram_used": 0,
    "hexagram_id": 0,
}

_CORE_KEYS = ("qinglong", "baihu", "zhuque", "xuanwu")

_MBTI_AFFINITY = {
    "NT": "baihu", "NF": "xuanwu", "SJ": "qinglong", "SP": "zhuque",
}

_QUADRANT_LABEL = {
    "qinglong": "青龙", "baihu": "白虎",
    "zhuque": "朱雀", "xuanwu": "玄武",
}


class KoiService:
    def __init__(self, store: KoiStore, config,
                 rng: random.Random | None = None):
        self._store = store
        self._config = config
        self._rng = rng or random.Random()

    # ── Context helpers ───────────────────────────────────────────

    @staticmethod
    def _ids(msg: dict) -> tuple[str, str, str]:
        return (
            str(msg.get("chat_id") or ""),
            str(msg.get("sender_id") or ""),
            str(msg.get("sender_name") or ""),
        )

    def enabled(self, msg: dict) -> bool:
        if not getattr(self._config, "koi_enabled", False):
            return False
        return is_group_enabled(
            str(msg.get("chat_id") or ""),
            str(msg.get("group_name") or ""),
            getattr(self._config, "koi_groups", ["*"]),
        )

    def _ensure_member(self, chat_id: str, sender_id: str,
                       sender_name: str) -> bool:
        if self._store.has_member(chat_id, sender_id):
            return False
        welcome = int(getattr(self._config, "koi_welcome_merit", 100) or 0)
        if welcome <= 0:
            return False
        return self._store.grant(
            chat_id, sender_id, sender_name, "merit", welcome,
            "welcome", ref_id="welcome",
        )

    def _daily(self, chat_id: str, sender_id: str, day: str) -> dict:
        row = dict(_EMPTY_DAILY)
        row.update(self._store.get_daily(chat_id, sender_id, day))
        row["chat_id"] = chat_id
        row["sender_id"] = sender_id
        row["day"] = day
        return row

    def _save(self, row: dict) -> None:
        self._store.upsert_daily(row)

    # ── Star / buff helpers ───────────────────────────────────────

    def _profile(self, chat_id: str, sender_id: str) -> dict:
        return self._store.get_profile(chat_id, sender_id)

    def _effective_counts(self, chat_id: str, sender_id: str) -> dict:
        counts = {k: float(v) for k, v in
                  self._store.star_counts(chat_id, sender_id).items()}
        profile = self._profile(chat_id, sender_id)
        affinity = rules.mbti_affinity(profile.get("mbti", ""))
        if affinity and counts.get(affinity, 0) > 0:
            counts[affinity] += counts[affinity] * 0.20
        sign = profile.get("sign", "")
        faction = rules.ELEMENT_TO_FACTION.get(
            rules.ELEMENT_OF_SIGN.get(sign, ""))
        if faction:
            rows = self._store.stars(chat_id, sender_id)
            if any(str(r["quadrant"]) == faction and
                   int(r["star_index"]) == 1 for r in rows):
                counts[faction] += 1.0
        return counts

    def _star_bonuses(self, chat_id: str, sender_id: str) -> dict:
        profile = self._profile(chat_id, sender_id)
        return rules.star_bonuses(
            self._effective_counts(chat_id, sender_id),
            str(profile.get("awakening") or ""),
        )

    def _star_total(self, chat_id: str, sender_id: str) -> int:
        return self._store.star_total(chat_id, sender_id)

    def _buff_map(self, chat_id: str, sender_id: str) -> dict:
        result: dict[str, float] = {}
        for buff in self._store.buffs_active(chat_id, sender_id):
            kind = str(buff["kind"])
            result[kind] = result.get(kind, 0.0) + float(buff["value"] or 0)
        return result

    def _merit_mult(self, chat_id: str, sender_id: str) -> float:
        base = float(self._star_bonuses(chat_id, sender_id)["merit_mult"])
        buff = self._buff_map(chat_id, sender_id)
        return base * (1.0 + buff.get("merit_boost", 0.0))

    def _pity_reduction(self, chat_id: str, sender_id: str) -> int:
        base = int(self._star_bonuses(chat_id, sender_id)["pity_reduction"])
        buff = self._buff_map(chat_id, sender_id)
        return base + int(buff.get("pity_boost", 0))

    def _pool_mult(self, chat_id: str, sender_id: str) -> float:
        base = float(self._star_bonuses(chat_id, sender_id)["pool_weight"])
        buff = self._buff_map(chat_id, sender_id)
        return base * (1.0 + buff.get("pool_boost", 0.0))

    def _luck_value(self, daily: dict) -> int:
        return max(0, min(120, int(daily.get("luck") or 0)
                          + int(daily.get("extra_luck") or 0)))

    def _add_luck(self, chat_id: str, sender_id: str, day: str,
                  amount: int) -> None:
        if amount <= 0:
            return
        daily = self._daily(chat_id, sender_id, day)
        daily["extra_luck"] = int(daily.get("extra_luck") or 0) + amount
        if int(daily.get("sign_id") or 0):
            daily["luck"] = min(
                120, int(daily.get("luck") or 0) + int(amount))
        self._save(daily)

    def _merit_gain(self, base: int, event: dict) -> int:
        mult = float(event.get("merit_mult", 1.0) or 1.0)
        return max(1, int(round(int(base) * mult)))

    @staticmethod
    def _hint(yi: str) -> str:
        return {
            "观星": "发「观星」看看今天的星象，还能领香火。",
            "放生": "发「放生」把香火换成气运。",
            "转运": "对签不满意？发「转运」重抽一次。",
            "聊天": "今天宜聊天，群里多说两句还能多攒香火。",
        }.get(yi, "发「功德」看看自己今天还能做什么。")

    def _settle_prefix(self, chat_id: str) -> str:
        announcement = self.settle_pool(chat_id)
        return f"{announcement}\n\n" if announcement else ""

    def _next_action_hint(self, daily: dict) -> str:
        if not int(daily.get("sign_id") or 0):
            return "发「求签」开始今天的运势。"
        if not int(daily.get("observed") or 0):
            return "发「观星」领取今日香火。"
        if not int(daily.get("release_used") or 0):
            return "发「放生」把香火换成气运。"
        if int(daily.get("draws_used") or 0) < 3:
            return "发「开光」抽一张塔罗牌。"
        return "今天的日常做完了，明天再来。"

    # ── 求签 ─────────────────────────────────────────────────────

    def _draw(self, chat_id: str, sender_id: str, day: str,
              streak: int, fortune: int, daily: dict) -> dict:
        event = rules.event_for(chat_id, day)
        level = rules.roll_level(self._rng)
        sign = self._rng.choice(signs_by_level(level["name"]))
        base = rules.roll_luck(level, self._rng)
        lucky = rules.lucky_sign(chat_id, local_week())
        profile = self._profile(chat_id, sender_id)
        is_lucky = bool(profile.get("sign")) and profile["sign"] == lucky
        stars = self._star_bonuses(chat_id, sender_id)
        luck = (
            base
            + rules.streak_bonus(streak)
            + rules.rank_luck_bonus(fortune)
            + int(event.get("sign_luck", 0) or 0)
            + int(stars.get("luck_bonus", 0) or 0)
            + (5 if is_lucky else 0)
            + self._faction_bonus(chat_id, sender_id)
            + int(daily.get("extra_luck") or 0)
        )
        return {
            "level": level,
            "sign": sign,
            "luck": max(0, min(120, luck)),
            "event": event,
            "is_lucky": is_lucky,
            "lucky_sign": lucky,
        }

    def _streak_rewards(self, chat_id: str, sender_id: str,
                        sender_name: str, day: str, streak: int) -> int:
        milestones = {3: 5, 7: 15, 15: 40, 30: 100}
        cycle_start = rules.today_minus_days(day, max(0, streak - 1))
        gained = 0
        for need, fortune in milestones.items():
            if streak < need:
                continue
            inserted = self._store.grant(
                chat_id, sender_id, sender_name, "fortune", fortune,
                "streak_bonus",
                ref_id=f"streak:{cycle_start}:{need}",
            )
            if inserted:
                gained += fortune
        return gained

    def _sign_set_rewards(self, chat_id: str, sender_id: str,
                          sender_name: str) -> int:
        owned = set(self._store.sign_ids(chat_id, sender_id))
        gained = 0
        for volume in range(6):
            ids = set(range(volume * 6 + 1, volume * 6 + 7))
            if ids.issubset(owned):
                inserted = self._store.grant(
                    chat_id, sender_id, sender_name, "fortune", 20,
                    "sign_set", ref_id=f"signset:{volume}",
                )
                if inserted:
                    gained += 20
        if len(owned) >= 36:
            inserted = self._store.grant(
                chat_id, sender_id, sender_name, "fortune", 150,
                "sign_set_all", ref_id="signset:all",
            )
            if inserted:
                gained += 150
        return gained

    def sign(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)

        if int(daily.get("sign_id") or 0):
            sign = sign_by_id(int(daily["sign_id"])) or {}
            level = str(daily.get("sign_level") or "")
            return prefix + (
                f"今天已经求过签了：{rules.level_emoji(level)}"
                f"{level}「{sign.get('name', '')}」\n"
                f"欧气 {self._luck_value(daily)}｜宜：{daily.get('yi', '')}\n"
                f"👉 {self._hint(str(daily.get('yi') or ''))}"
            )

        yesterday = rules.today_minus_days(day)
        prev = self._store.get_daily(chat_id, sender_id, yesterday)
        streak = int(prev.get("streak_days") or 0) + 1 \
            if int(prev.get("sign_id") or 0) else 1

        fortune = self._store.fortune(chat_id, sender_id)
        drawn = self._draw(chat_id, sender_id, day, streak, fortune, daily)
        level = drawn["level"]
        sign = drawn["sign"]

        daily.update({
            "sign_id": int(sign["id"]),
            "sign_level": level["name"],
            "luck": int(drawn["luck"]),
            "yi": sign["yi"],
            "streak_days": streak,
        })
        self._save(daily)

        gained = self._merit_gain(
            int(getattr(self._config, "koi_sign_merit", 50)),
            drawn["event"],
        )
        gained = int(round(gained * self._merit_mult(chat_id, sender_id)))
        self._store.grant(
            chat_id, sender_id, sender_name, "merit", gained,
            "sign", ref_id=f"sign:{day}",
        )
        new_fortune = self._store.fortune(chat_id, sender_id)
        is_new = self._store.collect_sign(
            chat_id, sender_id, int(sign["id"]))
        collected = self._store.sign_count(chat_id, sender_id)
        total = len(all_sign_ids())
        set_fortune = self._sign_set_rewards(chat_id, sender_id, sender_name)
        streak_fortune = self._streak_rewards(
            chat_id, sender_id, sender_name, day, streak)

        lines = [
            f"求得{rules.level_emoji(level['name'])}{level['name']}"
            f"「{sign['name']}」",
            f"欧气 {drawn['luck']}｜连续求签 {streak} 天"
            f"（+{rules.streak_bonus(streak)}）｜宜：{sign['yi']}",
            sign["phrase"],
        ]
        if is_new:
            lines.append(f"📜 新签文收入签谱（{collected}/{total}）")
        if set_fortune:
            lines.append(f"🎴 签谱进度奖励：气运 +{set_fortune}")
        if streak_fortune:
            lines.append(f"🔥 连签里程碑：气运 +{streak_fortune}")
        if drawn["is_lucky"]:
            lines.append(f"⭐ 本周幸运星座 {drawn['lucky_sign']}，欧气 +5")
        lines.append(f"今日星象：{drawn['event']['name']} — "
                     f"{drawn['event']['desc']}")
        lines.append(f"香火 +{gained}｜气运 {new_fortune}")
        lines.append(f"👉 {self._hint(sign['yi'])}")
        return prefix + "\n".join(lines)

    def read_sign(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, _ = self._ids(msg)
        daily = self._daily(chat_id, sender_id, local_day())
        if not int(daily.get("sign_id") or 0):
            return "今天还没求签，先发「求签」再来解签。"
        sign = sign_by_id(int(daily["sign_id"])) or {}
        level = str(daily.get("sign_level") or "")
        return (
            f"{rules.level_emoji(level)}{level}「{sign.get('name', '')}」\n"
            f"{sign.get('phrase', '')}\n"
            f"解读：{rules.LEVEL_ADVICE.get(level, '顺势而为。')}\n"
            f"今日宜：{daily.get('yi', '')}\n"
            f"👉 {self._hint(str(daily.get('yi') or ''))}"
        )

    # ── 观星 / 星骰 ──────────────────────────────────────────────

    def observe(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        event = rules.event_for(chat_id, day)
        lucky = rules.lucky_sign(chat_id, local_week())
        profile = self._profile(chat_id, sender_id)
        is_lucky = bool(profile.get("sign")) and profile["sign"] == lucky

        if int(daily.get("observed") or 0):
            return prefix + (
                f"今天已经观过星了。\n"
                f"今日星象：{event['name']} — {event['desc']}\n"
                f"本周幸运星座：{lucky}\n"
                f"👉 {self._next_action_hint(daily)}"
            )

        # 星骰：行星 / 星座 / 宫位
        planet = rules.DICE_PLANETS[
            self._rng.randrange(len(rules.DICE_PLANETS))]
        zodiac_id = self._rng.randrange(1, 13)
        house_id = self._rng.randrange(1, 13)
        new_dice = 0
        for kind, item_id in (
                ("dice_planet", int(planet["id"])),
                ("dice_zodiac", zodiac_id),
                ("dice_house", house_id)):
            if self._store.collection_add(
                    chat_id, sender_id, kind, item_id):
                new_dice += 1

        effect_text = self._apply_planet(
            chat_id, sender_id, day, planet)

        base = int(getattr(self._config, "koi_observe_merit", 30))
        bonus = int(event.get("observe_bonus", 0) or 0)
        if daily.get("yi") == "观星":
            bonus += 10
        gained = self._merit_gain(base + bonus, event)
        gained = int(round(gained * self._merit_mult(chat_id, sender_id)))
        self._store.grant(
            chat_id, sender_id, sender_name, "merit", gained,
            "observe", ref_id=f"observe:{day}",
        )
        daily["observed"] = 1
        self._save(daily)

        dice_count = (
            self._store.collection_count(chat_id, sender_id, "dice_planet")
            + self._store.collection_count(chat_id, sender_id, "dice_zodiac")
            + self._store.collection_count(chat_id, sender_id, "dice_house")
        )
        lines = [
            f"观星完成：{event['name']} — {event['desc']}",
            f"🎲 星骰：{planet['name']} · "
            f"{rules.DICE_ZODIAC[zodiac_id - 1]} · "
            f"{rules.DICE_HOUSES[house_id - 1]}",
            f"星骰效果：{effect_text}",
            f"香火 +{gained}｜本周幸运星座：{lucky}",
        ]
        if new_dice:
            lines.append(f"🎲 星骰图鉴 +{new_dice}（{dice_count}/31）")
        if is_lucky:
            lines.append("⭐ 你就是本周幸运星座，求签欧气 +5")
        lines.append(f"👉 {self._next_action_hint(daily)}")
        return prefix + "\n".join(lines)

    def _apply_planet(self, chat_id: str, sender_id: str, day: str,
                      planet: dict) -> str:
        kind = str(planet["kind"])
        value = planet["value"]
        if kind == "luck":
            self._add_luck(chat_id, sender_id, day, int(value))
        elif kind == "release":
            self._store.buff_add(
                chat_id, sender_id, "release_boost", value, 1)
        elif kind == "pity":
            self._store.buff_add(
                chat_id, sender_id, "pity_boost", value, 1)
        elif kind == "pool":
            self._store.buff_add(
                chat_id, sender_id, "pool_boost", value, 1)
        elif kind == "duel":
            self._store.buff_add(
                chat_id, sender_id, "duel_boost", value, 1)
        elif kind == "merit":
            self._store.buff_add(
                chat_id, sender_id, "merit_boost", value, 1)
        elif kind == "star_cost":
            self._store.buff_add(
                chat_id, sender_id, "star_cost_discount", value, 1)
        return str(planet["desc"])

    # ── 放生 ─────────────────────────────────────────────────────

    def release(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        event = rules.event_for(chat_id, day)

        if int(daily.get("release_used") or 0):
            return prefix + "今天已经放生过了，明天再来积德。"

        cost = max(5, int(getattr(self._config, "koi_release_cost", 30))
                   - int(event.get("release_discount", 0) or 0))
        balance = self._store.balance(chat_id, sender_id)
        if balance < cost:
            return prefix + (
                f"香火不足：放生需要 {cost}，你现在有 {balance}。\n"
                f"👉 先发「求签」或「观星」攒香火。"
            )

        gain = int(getattr(self._config, "koi_release_fortune", 3))
        if daily.get("yi") == "放生":
            gain *= 2
        gain += int(event.get("release_bonus", 0) or 0)
        gain += int(self._star_bonuses(chat_id, sender_id)["release_bonus"])
        buff = self._store.consume_buff(chat_id, sender_id, "release_boost")
        if buff:
            gain += int(buff["value"] or 0)
        if self._luck_value(daily) >= 80:
            gain += 1

        self._store.grant(chat_id, sender_id, sender_name, "merit",
                          -cost, "release_cost", ref_id=f"release:{day}")
        self._store.grant(chat_id, sender_id, sender_name, "fortune",
                          gain, "release", ref_id=f"release:{day}")
        daily["release_used"] = 1
        self._save(daily)

        fortune = self._store.fortune(chat_id, sender_id)
        rank = rules.rank_for(fortune)
        nxt = rank.get("next")
        next_line = (
            f"距「{nxt['name']}」还差 {rank['to_next']} 气运"
            if nxt else "已达最高段位"
        )
        return prefix + (
            f"放生完成：香火 -{cost}，气运 +{gain}\n"
            f"气运 {fortune}（{rank['name']}）｜{next_line}\n"
            f"👉 {self._next_action_hint(daily)}"
        )

    # ── 转运 ─────────────────────────────────────────────────────

    def reroll(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)

        if not int(daily.get("sign_id") or 0):
            return prefix + "今天还没求签，先发「求签」再来转运。"
        if int(daily.get("rerolls") or 0):
            return prefix + "今天已经转运过了，明天再来。"

        event = rules.event_for(chat_id, day)
        free = self._store.consume_buff(
            chat_id, sender_id, "free_reroll")
        cost = int(getattr(self._config, "koi_reroll_cost", 50))
        if daily.get("yi") == "转运":
            cost //= 2
        cost = max(10, cost - int(event.get("reroll_discount", 0) or 0))
        if free:
            cost = 0
        balance = self._store.balance(chat_id, sender_id)
        if balance < cost:
            return prefix + (
                f"香火不足：转运需要 {cost}，你现在有 {balance}。\n"
                f"👉 先发「求签」或「观星」攒香火。"
            )

        old_sign = sign_by_id(int(daily["sign_id"])) or {}
        old_level = str(daily.get("sign_level") or "")
        old_luck = self._luck_value(daily)

        fortune = self._store.fortune(chat_id, sender_id)
        streak = int(daily.get("streak_days") or 1)
        drawn = self._draw(chat_id, sender_id, day, streak, fortune, daily)

        if cost:
            self._store.grant(chat_id, sender_id, sender_name, "merit",
                              -cost, "reroll", ref_id=f"reroll:{day}")
        else:
            self._store.grant(chat_id, sender_id, sender_name, "merit",
                              0, "reroll", ref_id=f"reroll:{day}")
        daily.update({
            "sign_id": int(drawn["sign"]["id"]),
            "sign_level": drawn["level"]["name"],
            "luck": int(drawn["luck"]),
            "yi": drawn["sign"]["yi"],
            "rerolls": 1,
            "extra_luck": 0,
        })
        self._save(daily)
        is_new = self._store.collect_sign(
            chat_id, sender_id, int(drawn["sign"]["id"]))
        collected = self._store.sign_count(chat_id, sender_id)

        lines = [
            "焚符转运……",
            f"旧签：{rules.level_emoji(old_level)}{old_level}"
            f"「{old_sign.get('name', '')}」欧气 {old_luck}",
            f"新签：{rules.level_emoji(drawn['level']['name'])}"
            f"{drawn['level']['name']}「{drawn['sign']['name']}」"
            f"欧气 {drawn['luck']}",
            f"香火 -{cost}｜今日转运次数已用完",
        ]
        if is_new:
            lines.append(f"📜 新签文收入签谱（{collected}/{len(all_sign_ids())}）")
        lines.append(f"👉 {self._hint(drawn['sign']['yi'])}")
        return prefix + "\n".join(lines)

    # ── 开光 / 塔罗 ──────────────────────────────────────────────

    def open_light(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        limit = int(getattr(self._config, "koi_draw_daily_limit", 3))
        used = int(daily.get("draws_used") or 0)
        if used >= limit:
            return prefix + f"今天已经开光 {limit} 次了，明天再来。"

        cost = 0 if used == 0 else int(
            getattr(self._config, "koi_draw_cost", 100))
        balance = self._store.balance(chat_id, sender_id)
        if balance < cost:
            return prefix + (
                f"香火不足：开光需要 {cost}，你现在有 {balance}。\n"
                f"👉 先发「求签」或「观星」攒香火。"
            )

        pity = self._store.pity_get(chat_id, sender_id)
        pity_base = int(getattr(self._config, "koi_draw_pity", 30))
        effective_pity = max(
            5, pity_base - self._pity_reduction(chat_id, sender_id))
        stars = self._star_bonuses(chat_id, sender_id)
        legendary = (
            pity + 1 >= effective_pity
            or self._rng.random() < (0.05 + float(
                stars.get("legendary_bonus", 0.0)))
        )
        if legendary:
            card = rules.TAROT_BY_ID[21]
            self._store.pity_set(chat_id, sender_id, 0)
        else:
            pool = [c for c in rules.TAROT_CARDS if not c.get("legendary")]
            card = self._rng.choice(pool)
            self._store.pity_set(chat_id, sender_id, pity + 1)

        if cost:
            self._store.grant(chat_id, sender_id, sender_name, "merit",
                              -cost, "draw", ref_id=f"draw:{day}:{used}")
        else:
            self._store.grant(chat_id, sender_id, sender_name, "merit",
                              0, "draw", ref_id=f"draw:{day}:{used}")
        daily["draws_used"] = used + 1
        self._save(daily)

        effect_text, fortune_gain, merit_gain = self._apply_tarot(
            chat_id, sender_id, sender_name, day, card,
            ref_suffix=f"{used}")
        is_new = self._store.collection_add(
            chat_id, sender_id, "tarot", int(card["id"]))
        tarot_count = self._store.collection_count(
            chat_id, sender_id, "tarot")
        set_line = ""
        if tarot_count >= rules.TAROT_TOTAL:
            inserted = self._store.grant(
                chat_id, sender_id, sender_name, "fortune", 150,
                "tarot_set", ref_id="tarotset")
            if inserted:
                set_line = "\n🎴 22 张大阿卡纳集齐，气运 +150，称号「愚者之旅」"

        pity_after = self._store.pity_get(chat_id, sender_id)
        lines = [
            f"开光得「{card['name']}」🎴",
            card["text"],
            f"效果：{effect_text}",
            f"香火 -{cost}｜距传说符保底还差 "
            f"{max(0, effective_pity - pity_after)} 次",
        ]
        if is_new:
            lines.append(f"🎴 新牌收入牌鉴（{tarot_count}/{rules.TAROT_TOTAL}）")
        if set_line:
            lines.append(set_line.strip())
        lines.append(f"👉 {self._next_action_hint(daily)}")
        return prefix + "\n".join(lines)

    def _apply_tarot(self, chat_id: str, sender_id: str,
                     sender_name: str, day: str,
                     card: dict, ref_suffix: str = "") -> tuple[str, int, int]:
        kind = str(card["kind"])
        value = card["value"]
        merit_gain = 0
        fortune_gain = 0
        ref = f"tarot:{day}:{ref_suffix}:{card['id']}"
        if kind == "merit":
            merit_gain = int(value)
            self._store.grant(chat_id, sender_id, sender_name, "merit",
                              merit_gain, "tarot_merit", ref_id=ref)
            effect = f"香火 +{merit_gain}"
        elif kind == "fortune":
            fortune_gain = int(value)
            self._store.grant(chat_id, sender_id, sender_name, "fortune",
                              fortune_gain, "tarot_fortune", ref_id=ref)
            effect = f"气运 +{fortune_gain}"
        elif kind == "luck":
            self._add_luck(chat_id, sender_id, day, int(value))
            effect = f"今日欧气 +{int(value)}"
        elif kind == "free_reroll":
            self._store.buff_add(
                chat_id, sender_id, "free_reroll", 1, 1)
            effect = "获得一次免费转运"
        elif kind == "merit_boost":
            self._store.buff_add(
                chat_id, sender_id, "merit_boost", value, 3)
            effect = f"接下来 3 次香火收益 +{int(value * 100)}%"
        elif kind == "pool_boost":
            self._store.buff_add(
                chat_id, sender_id, "pool_boost", value, 1)
            effect = f"下次锦鲤池权重 +{int(value * 100)}%"
        elif kind == "pity_boost":
            self._store.buff_add(
                chat_id, sender_id, "pity_boost", value, 1)
            effect = f"下次开光保底 -{int(value)} 抽"
        elif kind == "koi_body":
            self._store.buff_add(
                chat_id, sender_id, "pity_boost", 2, 3)
            effect = "接下来 3 次开光保底 -2 抽"
        else:
            effect = "运势流转"
        return effect, fortune_gain, merit_gain

    # ── 点星 / 觉醒 ──────────────────────────────────────────────

    def point_star(self, msg: dict, raw: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        quadrant = rules.normalize_quadrant(raw)
        if quadrant is None:
            return prefix + (
                "点星要指定四象：点星 青龙 / 白虎 / 朱雀 / 玄武。\n"
                "星盘圆满后可点「外圈」。"
            )

        counts = self._store.star_counts(chat_id, sender_id)
        if quadrant == "外圈":
            core_total = sum(counts.get(k, 0) for k in _CORE_KEYS)
            if core_total < rules.STAR_TOTAL:
                return prefix + (
                    f"外圈需要先点亮 28 宿（当前 {core_total}/28）。")
            index = counts.get("outer", 0) + 1
            if index > rules.OUTER_STARS:
                return prefix + "外圈 60 星已经点满了。"
            cost = rules.outer_star_cost(index)
            if self._store.balance(chat_id, sender_id) < cost:
                return prefix + (
                    f"点外圈第 {index} 星需要 {cost} 香火，"
                    f"你现在有 {self._store.balance(chat_id, sender_id)}。")
            self._store.grant(chat_id, sender_id, sender_name, "merit",
                              -cost, "star", ref_id=f"star:outer:{index}")
            self._store.add_star(chat_id, sender_id, "outer", index)
            return prefix + (
                f"点亮外圈第 {index} 星：全属性 +0.2%。\n"
                f"香火 -{cost}｜外圈 {index}/{rules.OUTER_STARS}")

        key = rules.QUADRANTS[quadrant]["key"]
        index = counts.get(key, 0) + 1
        if index > 7:
            return prefix + f"{quadrant}七宿已经点满了。"

        cost = rules.star_cost(index)
        fortune_req = rules.star_fortune_req(index)
        action_req = rules.star_action_req(key, index)
        action = rules.QUADRANTS[quadrant]["action"]
        action_done = self._store.count_reason(chat_id, sender_id, action)
        fortune = self._store.fortune(chat_id, sender_id)

        # 本命四象第一颗星半价
        profile = self._profile(chat_id, sender_id)
        faction = rules.ELEMENT_TO_FACTION.get(
            rules.ELEMENT_OF_SIGN.get(profile.get("sign", ""), ""))
        is_benming = bool(faction and faction == key and index == 1)
        if is_benming:
            cost //= 2
        buff = self._store.buffs_active(chat_id, sender_id)
        discount = sum(float(b["value"] or 0) for b in buff
                       if b["kind"] == "star_cost_discount")
        if discount:
            cost = max(1, int(round(cost * (1 - min(0.5, discount)))))
        balance = self._store.balance(chat_id, sender_id)

        missing = []
        if balance < cost:
            missing.append(f"香火 {balance}/{cost}")
        if fortune < fortune_req:
            missing.append(f"气运 {fortune}/{fortune_req}")
        if action_done < action_req:
            missing.append(
                f"{rules.QUADRANTS[quadrant]['action_label']} "
                f"{action_done}/{action_req}")
        if missing:
            return prefix + (
                f"点{quadrant}第 {index} 星还差：{'；'.join(missing)}。\n"
                f"👉 先发「求签」「观星」「放生」或「转运」。"
            )

        if discount:
            self._store.consume_buff(
                chat_id, sender_id, "star_cost_discount")
        self._store.grant(chat_id, sender_id, sender_name, "merit",
                          -cost, "star", ref_id=f"star:{key}:{index}")
        self._store.add_star(chat_id, sender_id, key, index)
        name = rules.star_name(key, index)
        effect = rules.QUADRANTS[quadrant]["stat"]
        extra = []
        if is_benming:
            extra.append("本命星效果翻倍")
        affinity = rules.mbti_affinity(profile.get("mbti", ""))
        if affinity == key:
            extra.append("MBTI 同象 +20%")
        lines = [
            f"点亮{quadrant}第 {index} 星「{name}」✨",
            f"{effect}提升｜香火 -{cost}",
        ]
        if extra:
            lines.append("＋".join(extra))
        if index == 7:
            lines.append(
                f"🎉 {quadrant}七宿圆满：{rules.QUADRANTS[quadrant]['set_label']}")
            core_total = sum(
                self._store.star_counts(chat_id, sender_id).get(k, 0)
                for k in _CORE_KEYS)
            if core_total >= rules.STAR_TOTAL and not profile.get("awakening"):
                lines.append("🌟 二十八宿圆满！发「觉醒 青龙/白虎/朱雀/玄武」选择命格觉醒。")
        lines.append(f"👉 继续发「点星 {quadrant}」点亮下一颗。")
        return prefix + "\n".join(lines)

    def awaken(self, msg: dict, raw: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        quadrant = rules.normalize_quadrant(raw)
        if quadrant not in rules.AWAKENINGS:
            return "觉醒要指定四象：觉醒 青龙 / 白虎 / 朱雀 / 玄武。"
        profile = self._profile(chat_id, sender_id)
        if profile.get("awakening"):
            return "已经觉醒过了，命格不可更改。"
        if self._star_total(chat_id, sender_id) < rules.STAR_TOTAL:
            return (
                f"觉醒需要点亮 28 宿（当前 "
                f"{self._star_total(chat_id, sender_id)}/28）。")
        awakening = rules.AWAKENINGS[quadrant]
        self._store.save_profile(
            chat_id, sender_id, awakening=awakening["key"])
        return (
            f"🌟 命格觉醒：{awakening['name']}\n"
            f"{awakening['desc']}\n"
            f"👉 发「星盘」查看完整命盘。"
        )

    # ── 锦鲤池 ───────────────────────────────────────────────────

    def pool_bet(self, msg: dict, raw: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        amount = int(raw or 0)
        min_bet = int(getattr(self._config, "koi_pool_min", 10))
        max_bet = int(getattr(self._config, "koi_pool_max_per_day", 200))
        if amount < min_bet:
            return prefix + f"投池至少 {min_bet} 香火，例如「投池 50」。"
        if amount > max_bet:
            return prefix + f"单次最多投 {max_bet} 香火。"
        already = int(daily.get("pool_in") or 0)
        if already + amount > max_bet:
            return prefix + (
                f"今天已投 {already}，单人单日上限 {max_bet} 香火。")
        balance = self._store.balance(chat_id, sender_id)
        if balance < amount:
            return prefix + (
                f"香火不足：投池需要 {amount}，你现在有 {balance}。")
        if self._store.pool_add(
                chat_id, day, sender_id, sender_name,
                amount, self._luck_value(daily)) < 0:
            return prefix + "今天锦鲤池已经开奖了，明天再来。"
        self._store.grant(chat_id, sender_id, sender_name, "merit",
                          -amount, "pool", ref_id=f"pool:{day}:{already}")
        daily["pool_in"] = already + amount
        self._save(daily)
        pool = self._store.pool_get(chat_id, day)
        return prefix + (
            f"已投入锦鲤池 {amount} 香火。\n"
            f"今日池子 {pool.get('total', 0)}｜你已投 "
            f"{daily['pool_in']}/200\n"
            f"👉 21:00 开奖，中奖者独得 80%。"
        )

    def pool_view(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        pool = self._store.pool_get(chat_id, day)
        daily = self._daily(chat_id, sender_id, day)
        if not pool:
            return prefix + (
                "今天锦鲤池还没开张。\n"
                "👉 发「投池 50」参与今晚 21:00 开奖。")
        if pool.get("status") == "drawn":
            return prefix + (
                f"今晚锦鲤池已开奖：\n"
                f"🐟 {pool.get('winner_name') or pool.get('winner_id')} "
                f"独得 {pool.get('payout', 0)} 香火！\n"
                f"池子总额 {pool.get('total', 0)}。")
        if pool.get("status") == "refunded":
            return prefix + "今天参与人数不足，池子已全额退还。"
        entries = self._store.pool_entries(chat_id, day)
        return prefix + (
            f"🎏 今日锦鲤池\n"
            f"池子 {pool.get('total', 0)} 香火｜参与 {len(entries)} 人\n"
            f"你已投 {daily.get('pool_in', 0)}/200\n"
            f"21:00 开奖，中奖者独得 80%，其余返还 15%。\n"
            f"👉 发「投池 50」参与。"
        )

    def settle_open_pools(self) -> list[tuple[str, str]]:
        """Settle every group whose pool is still open and due."""
        result: list[tuple[str, str]] = []
        for row in self._store.open_pools():
            chat_id = str(row["chat_id"])
            day = str(row["day"])
            try:
                text = self.settle_pool(chat_id, day)
            except Exception:
                logger.exception("Pool settlement failed for %s", chat_id)
                continue
            if text:
                result.append((chat_id, text))
        return result

    def settle_pool(self, chat_id: str,
                    day: str | None = None) -> str | None:
        now = int(time.time())
        today = local_day(now)
        day = day or today
        pool = self._store.pool_get(chat_id, day)
        if not pool or str(pool.get("status")) != "open":
            return None
        hour = int(getattr(self._config, "koi_pool_hour", 21))
        if day == today and time.localtime(now).tm_hour < hour:
            return None
        entries = self._store.pool_entries(chat_id, day)
        total = sum(int(e["amount"]) for e in entries)
        if not entries or total <= 0:
            self._store.pool_refund(chat_id, day)
            return None
        if len(entries) < 2:
            for entry in entries:
                self._store.grant(
                    chat_id, str(entry["sender_id"]),
                    str(entry.get("sender_name") or ""), "merit",
                    int(entry["amount"]), "pool_refund",
                    ref_id=f"poolrefund:{day}:{entry['sender_id']}")
            self._store.pool_refund(chat_id, day)
            return "🎏 今天锦鲤池人数不足，已全额退还。"

        weights = []
        for entry in entries:
            weight = math.sqrt(max(1, int(entry["amount"]))) * (
                1 + max(0, int(entry["luck"] or 0)) / 200.0
            ) * self._pool_mult(chat_id, str(entry["sender_id"]))
            weights.append(max(0.0001, weight))
        winner = self._rng.choices(entries, weights=weights, k=1)[0]
        winner_ratio = float(getattr(
            self._config, "koi_pool_winner_ratio", 0.80))
        refund_ratio = float(getattr(
            self._config, "koi_pool_refund_ratio", 0.15))
        payout = int(total * winner_ratio)
        if not self._store.pool_settle(
                chat_id, day, str(winner["sender_id"]),
                str(winner.get("sender_name") or ""), payout):
            return None
        for entry in entries:
            refund = int(int(entry["amount"]) * refund_ratio)
            if refund > 0:
                self._store.grant(
                    chat_id, str(entry["sender_id"]),
                    str(entry.get("sender_name") or ""), "merit",
                    refund, "pool_refund",
                    ref_id=f"poolrefund:{day}:{entry['sender_id']}")
        self._store.grant(
            chat_id, str(winner["sender_id"]),
            str(winner.get("sender_name") or ""), "merit",
            payout, "pool_win", ref_id=f"poolwin:{day}")
        self._store.grant(
            chat_id, str(winner["sender_id"]),
            str(winner.get("sender_name") or ""), "fortune",
            5, "pool_win_fortune", ref_id=f"poolwinf:{day}")
        return (
            f"🎏 今晚锦鲤池开奖！\n"
            f"参与 {len(entries)} 人，池子 {total} 香火\n"
            f"🐟 中奖者：{winner.get('sender_name') or winner['sender_id']}"
            f"（投入 {winner['amount']}，欧气 {winner.get('luck', 0)}）\n"
            f"独得 {payout} 香火 + 5 气运！\n"
            f"其余参与者已返还 15%。"
        )

    # ── 斗星 / 合盘 ──────────────────────────────────────────────

    def duel_star(self, msg: dict, target_id: str,
                  target_name: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        if not target_id or target_id == sender_id:
            return prefix + "请 @ 一位群友再斗星。"
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        used = int(daily.get("duel_star_used") or 0)
        limit = int(getattr(
            self._config, "koi_duel_star_daily_limit", 3))
        if used >= limit:
            return prefix + f"今天斗星 {limit} 次已经用完，明天再来。"
        key = self._rng.choice(_CORE_KEYS)
        mine = self._store.star_counts(chat_id, sender_id).get(key, 0)
        theirs = self._store.star_counts(chat_id, target_id).get(key, 0)
        daily["duel_star_used"] = used + 1
        self._save(daily)
        label = _QUADRANT_LABEL.get(key, key)
        if mine == theirs:
            result = "平手，谁也没赢。"
        else:
            winner_id = sender_id if mine > theirs else target_id
            winner_name = sender_name if mine > theirs else target_name
            reward = 10
            buff = self._store.consume_buff(
                chat_id, winner_id, "duel_boost")
            if buff:
                reward += int(buff["value"] or 0)
            self._store.grant(
                chat_id, winner_id, winner_name, "merit", reward,
                "duel_star", ref_id=f"duelstar:{day}:{winner_id}:{used}")
            result = f"{winner_name} 赢了，香火 +{reward}。"
        return prefix + (
            f"斗星：{label}\n"
            f"{sender_name} {mine} 星 vs {target_name} {theirs} 星\n"
            f"{result}"
        )

    def synastry(self, msg: dict, target_id: str,
                 target_name: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        if not target_id or target_id == sender_id:
            return "请 @ 一位群友再合盘。"
        a = self._profile(chat_id, sender_id)
        b = self._profile(chat_id, target_id)
        if not a.get("sign") or not b.get("sign"):
            return "双方都要先设置星座：发「我是 天蝎座」。"
        result = rules.compatibility(a, b)
        pair = ":".join(sorted([sender_id, target_id]))
        day = local_day()
        inserted = self._store.grant(
            chat_id, sender_id, sender_name, "fortune", 0,
            "synastry", ref_id=f"synastry:{day}:{pair}")
        if inserted:
            self._add_luck(chat_id, sender_id, day, 1)
            self._add_luck(chat_id, target_id, day, 1)
        return (
            f"合盘：{sender_name} × {target_name}\n"
            f"契合度 {result['score']}/100 — {result['text']}\n"
            f"太阳：{a['sign']} × {b['sign']}"
            f"｜MBTI：{a.get('mbti') or '未设置'} × "
            f"{b.get('mbti') or '未设置'}\n"
            f"{'双方欧气 +1' if inserted else '今天已经合过这一对了'}"
        )

    # ── 周运 ─────────────────────────────────────────────────────

    def _weekly_progress(self, chat_id: str, sender_id: str,
                         week: str, goals: list[dict]) -> list[dict]:
        since = rules.week_start_ts()
        result = []
        for goal in goals:
            key = goal["key"]
            if key == "star":
                done = sum(int(row["n"]) for row in
                           self._store.stars_since_by_member(
                               chat_id, since)
                           if str(row["sender_id"]) == sender_id)
            else:
                done = self._store.count_reason(
                    chat_id, sender_id, key, since_ts=since)
            result.append({
                **goal,
                "done": min(done, int(goal["target"])),
                "complete": done >= int(goal["target"]),
            })
        return result

    def _ensure_weekly(self, chat_id: str, sender_id: str,
                       week: str) -> tuple[list[dict], dict]:
        row = self._store.weekly_get(chat_id, sender_id, week)
        if row:
            goals = json.loads(row.get("goals_json") or "[]")
        else:
            goals = rules.weekly_goals(chat_id, week)
            self._store.weekly_save(
                chat_id, sender_id, week,
                json.dumps(goals, ensure_ascii=False))
            row = self._store.weekly_get(chat_id, sender_id, week)
        return goals, row

    def weekly(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        week = local_week()
        goals, row = self._ensure_weekly(chat_id, sender_id, week)
        progress = self._weekly_progress(chat_id, sender_id, week, goals)
        lines = [f"【本周运势】{week}"]
        for item in progress:
            mark = "✓" if item["complete"] else "○"
            lines.append(
                f"{mark} {item['label']} "
                f"{item['done']}/{item['target']}")
        all_done = all(item["complete"] for item in progress)
        if row.get("claimed"):
            lines.append("本周奖励已领取。")
        elif all_done:
            lines.append("🎁 本周目标完成！发「领取周运」领取 100 香火 + 5 气运。")
        else:
            lines.append("👉 完成三个目标可领 100 香火 + 5 气运。")
        return "\n".join(lines)

    def claim_weekly(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        week = local_week()
        goals, row = self._ensure_weekly(chat_id, sender_id, week)
        if row.get("claimed"):
            return "本周周运奖励已经领过了。"
        progress = self._weekly_progress(chat_id, sender_id, week, goals)
        if not all(item["complete"] for item in progress):
            return "本周目标还没完成，发「周运」看看进度。"
        if not self._store.weekly_claim(chat_id, sender_id, week):
            return "本周周运奖励已经领过了。"
        self._store.grant(chat_id, sender_id, sender_name, "merit",
                          int(getattr(self._config,
                                      "koi_weekly_reward_merit", 100)),
                          "weekly", ref_id=f"weekly:{week}")
        self._store.grant(chat_id, sender_id, sender_name, "fortune",
                          int(getattr(self._config,
                                      "koi_weekly_reward_fortune", 5)),
                          "weekly_fortune", ref_id=f"weekly:{week}")
        return (
            f"🎁 周运奖励已领取：香火 +"
            f"{int(getattr(self._config, 'koi_weekly_reward_merit', 100))}，"
            f"气运 +"
            f"{int(getattr(self._config, 'koi_weekly_reward_fortune', 5))}。")

    # ── 社交榜单 ─────────────────────────────────────────────────

    def group_portrait(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, _, _ = self._ids(msg)
        profiles = self._store.all_profiles(chat_id)
        if not profiles:
            return "还没有人设置本命星。发「我是 天蝎座」开始。"
        signs: dict[str, int] = {}
        mbtis: dict[str, int] = {}
        for p in profiles:
            if p.get("sign"):
                signs[p["sign"]] = signs.get(p["sign"], 0) + 1
            if p.get("mbti"):
                mbtis[p["mbti"]] = mbtis.get(p["mbti"], 0) + 1
        top_signs = sorted(signs.items(), key=lambda x: (-x[1], x[0]))[:5]
        top_mbtis = sorted(mbtis.items(), key=lambda x: (-x[1], x[0]))[:5]
        lines = ["【群像】"]
        lines.append("星座：" + "、".join(
            f"{k}×{v}" for k, v in top_signs) if top_signs else "星座：暂无")
        lines.append("MBTI：" + "、".join(
            f"{k}×{v}" for k, v in top_mbtis) if top_mbtis else "MBTI：暂无")
        lines.append(f"已设置命盘 {len(profiles)} 人")
        lines.append("👉 发「我是 天蝎座」「我是 INFP」加入群像。")
        return "\n".join(lines)

    def cp_board(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, _, _ = self._ids(msg)
        profiles = [p for p in self._store.all_profiles(chat_id)
                    if p.get("sign") or p.get("mbti")][:50]
        if len(profiles) < 2:
            return "至少需要 2 个人设置命盘才能排 CP 榜。"
        pairs = []
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                a, b = profiles[i], profiles[j]
                result = rules.compatibility(a, b)
                pairs.append((result["score"], result["text"], a, b))
        pairs.sort(key=lambda x: -x[0])
        lines = ["【CP榜】本群契合度 Top5"]
        for score, text, a, b in pairs[:5]:
            lines.append(
                f"{a.get('sign') or '—'} × {b.get('sign') or '—'}"
                f"（{a.get('mbti') or '—'} / {b.get('mbti') or '—'}）"
                f" — {score}")
        lines.append("👉 发「合盘 @某人」看详细解读。")
        return "\n".join(lines)

    # ── 四象阵营 ─────────────────────────────────────────────────

    def _faction_scores(self, chat_id: str) -> dict:
        since = rules.week_start_ts()
        active = self._store.active_members_since(chat_id, since)
        profiles = {
            str(p["sender_id"]): p
            for p in self._store.all_profiles(chat_id)
        }
        scores = {
            key: {"members": 0, "stars": 0, "score": 0.0}
            for key in _CORE_KEYS
        }
        member_faction: dict[str, str] = {}
        for sender_id in active:
            profile = profiles.get(sender_id, {})
            faction = rules.ELEMENT_TO_FACTION.get(
                rules.ELEMENT_OF_SIGN.get(profile.get("sign", ""), ""))
            if faction in scores:
                scores[faction]["members"] += 1
                member_faction[sender_id] = faction
        for row in self._store.stars_since_by_member(chat_id, since):
            faction = member_faction.get(str(row["sender_id"]))
            if faction in scores:
                scores[faction]["stars"] += int(row["n"] or 0)
        for value in scores.values():
            value["score"] = value["stars"] / max(1, value["members"])
        return scores

    def faction_leader(self, chat_id: str) -> str | None:
        scores = self._faction_scores(chat_id)
        best_key, best_score = None, 0.0
        for key, value in scores.items():
            if value["score"] > best_score:
                best_key, best_score = key, value["score"]
        return best_key if best_score > 0 else None

    def _faction_bonus(self, chat_id: str, sender_id: str) -> int:
        leader = self.faction_leader(chat_id)
        if not leader:
            return 0
        profile = self._profile(chat_id, sender_id)
        faction = rules.ELEMENT_TO_FACTION.get(
            rules.ELEMENT_OF_SIGN.get(profile.get("sign", ""), ""))
        return 5 if faction == leader else 0

    def faction_board(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, _ = self._ids(msg)
        scores = self._faction_scores(chat_id)
        leader = self.faction_leader(chat_id)
        lines = ["【四象榜】本周"]
        for key in _CORE_KEYS:
            value = scores[key]
            mark = " 👑" if key == leader else ""
            lines.append(
                f"{_QUADRANT_LABEL[key]}：{value['stars']} 星 / "
                f"{value['members']} 人 = {value['score']:.1f}{mark}")
        profile = self._profile(chat_id, sender_id)
        mine = rules.ELEMENT_TO_FACTION.get(
            rules.ELEMENT_OF_SIGN.get(profile.get("sign", ""), ""))
        if leader:
            lines.append(
                f"本周领先：{_QUADRANT_LABEL[leader]}"
                f"（同象成员求签欧气 +5）")
            if mine == leader:
                lines.append("🔥 你就在领先阵营！")
        lines.append("👉 发「我是 天蝎座」加入阵营，点星为阵营加分。")
        return "\n".join(lines)

    # ── 个人卡 / 榜单 / 签谱 ─────────────────────────────────────

    def card(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        balance = self._store.balance(chat_id, sender_id)
        fortune = self._store.fortune(chat_id, sender_id)
        rank = rules.rank_for(fortune)
        profile = self._profile(chat_id, sender_id)
        counts = self._store.star_counts(chat_id, sender_id)
        core_total = sum(counts.get(k, 0) for k in _CORE_KEYS)

        if int(daily.get("sign_id") or 0):
            sign = sign_by_id(int(daily["sign_id"])) or {}
            sign_line = (
                f"{rules.level_emoji(str(daily.get('sign_level') or ''))}"
                f"{daily.get('sign_level')}「{sign.get('name', '')}」"
                f"｜宜：{daily.get('yi', '')}"
            )
        else:
            sign_line = "今天还没求签"

        nxt = rank.get("next")
        rank_line = (
            f"{rank['name']}（距「{nxt['name']}」还差 {rank['to_next']} 气运）"
            if nxt else f"{rank['name']}（已达最高段位）"
        )
        identity = profile.get("sign") or "未设置"
        if profile.get("mbti"):
            identity += f" · {profile['mbti']}"
        if profile.get("noble_star"):
            identity += f" · {profile['noble_star']}"

        done = [
            "观星✓" if int(daily.get("observed") or 0) else "观星○",
            "放生✓" if int(daily.get("release_used") or 0) else "放生○",
            "转运✓" if int(daily.get("rerolls") or 0) else "转运○",
            f"开光 {int(daily.get('draws_used') or 0)}/3",
        ]
        buffs = self._store.buffs_active(chat_id, sender_id)
        buff_line = ""
        if buffs:
            buff_line = "\n符：" + "、".join(
                f"{b['kind']}×{b['uses_left']}" for b in buffs)
        bazi_line = ""
        if int(profile.get("birth_year") or 0):
            hex_count = self._store.collection_count(
                chat_id, sender_id, "hexagram")
            bazi_line = (
                f"\n八字：{self._birth_label(profile)}"
                f"｜卦谱 {hex_count}/64")
        return prefix + (
            f"【功德卡】\n"
            f"香火 {balance}｜气运 {fortune}｜欧气 {self._luck_value(daily)}\n"
            f"段位：{rank_line}\n"
            f"本命星：{identity}\n"
            f"今日签：{sign_line}\n"
            f"星盘：{core_total}/28"
            f"{'（已觉醒）' if profile.get('awakening') else ''}"
            f"｜四象 {counts.get('qinglong', 0)}/"
            f"{counts.get('baihu', 0)}/{counts.get('zhuque', 0)}/"
            f"{counts.get('xuanwu', 0)}{buff_line}{bazi_line}\n"
            f"今日进度：{'｜'.join(done)}\n"
            f"👉 {self._next_action_hint(daily)}"
        )

    def leaderboard(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, _ = self._ids(msg)
        rows = self._store.leaderboard(chat_id, 10)
        if not rows:
            return "气运榜还是空的。发「求签」开始今天的第一签。"
        lines = ["【气运榜】"]
        for index, row in enumerate(rows, 1):
            rank = rules.rank_for(int(row["fortune"]))
            lines.append(
                f"{index}. {row['sender_name'] or row['sender_id']} — "
                f"{row['fortune']}（{rank['name']}）"
            )
        mine = self._store.fortune(chat_id, sender_id)
        lines.append(f"我的气运：{mine}（{rules.rank_for(mine)['name']}）")
        lines.append("👉 发「放生」把香火换成气运。")
        return "\n".join(lines)

    def sign_book(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, _ = self._ids(msg)
        owned = set(self._store.sign_ids(chat_id, sender_id))
        total = len(all_sign_ids())
        lines = [f"【签谱】已收集 {len(owned)}/{total}"]
        for level in rules.SIGN_LEVELS:
            names = [
                sign["name"] for sign in signs_by_level(level["name"])
                if int(sign["id"]) in owned
            ]
            if names:
                lines.append(f"{level['name']}：{'、'.join(names)}")
        missing = total - len(owned)
        if missing:
            lines.append(f"还有 {missing} 支没抽到（未收集显示「？？？」）。")
        else:
            lines.append("36 支签已集齐，称号「知天命」。")
        tarot = self._store.collection_count(chat_id, sender_id, "tarot")
        dice = (
            self._store.collection_count(chat_id, sender_id, "dice_planet")
            + self._store.collection_count(chat_id, sender_id, "dice_zodiac")
            + self._store.collection_count(chat_id, sender_id, "dice_house")
        )
        hex_count = self._store.collection_count(
            chat_id, sender_id, "hexagram")
        lines.append(
            f"牌鉴 {tarot}/{rules.TAROT_TOTAL}｜星骰图鉴 {dice}/31"
            f"｜卦谱 {hex_count}/64")
        lines.append("👉 每天求签、开光、观星，慢慢集齐。")
        return "\n".join(lines)

    # ── 身份 ─────────────────────────────────────────────────────

    def set_sign(self, msg: dict, raw: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        text = str(raw or "").strip()
        for prefix in ("设置生日", "生日", "设置星座", "我是"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break

        month = day = 0
        digits = [int(n) for n in re.findall(r"\d+", text)]
        if len(digits) >= 2:
            month, day = digits[0], digits[1]
            sign = rules.sign_from_date(month, day)
        else:
            sign = rules.normalize_sign(text)
        if sign is None:
            return (
                "没认出这个星座。\n"
                "👉 发「我是 天蝎座」，或「设置生日 10-24」。"
            )

        current = self._profile(chat_id, sender_id)
        now = int(time.time())
        cooldown = int(getattr(
            self._config, "koi_sign_change_days", 30)) * 86400
        if current.get("sign") and current.get("sign") != sign and \
                cooldown > 0 and \
                now - int(current.get("sign_updated_at") or 0) < cooldown:
            return (
                f"本命星 30 天内只能改一次（当前 {current['sign']}）。")

        moon = rules.moon_sign(sender_id)
        rising = rules.rising_sign(sender_id)
        noble = rules.noble_star(month, day) if month and day else ""
        life = rules.life_number(month, day) if month and day else 0
        self._store.save_profile(
            chat_id, sender_id, sign=sign, moon=moon, rising=rising,
            noble_star=noble, life_number=life, sign_updated_at=now)
        lucky = rules.lucky_sign(chat_id, local_week())
        match = "（你就是本周幸运星座）" if sign == lucky else ""
        extra = ""
        if noble:
            extra = f"\n本命宿：{noble}｜生命数字：{life}"
        return (
            f"本命星已设为「{sign}」。\n"
            f"月亮：{moon}｜上升：{rising}{extra}\n"
            f"本周幸运星座：{lucky}{match}\n"
            f"👉 发「求签」，今日欧气会带上本命星。"
        )

    def set_mbti(self, msg: dict, raw: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        text = str(raw or "").strip()
        for prefix in ("设置MBTI", "设置mbti", "MBTI", "mbti", "我是"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        mbti = rules.normalize_mbti(text)
        if mbti is None:
            return (
                "没认出这个 MBTI 类型。\n"
                "👉 发「我是 INFP」这样的 16 型之一。"
            )
        current = self._profile(chat_id, sender_id)
        now = int(time.time())
        cooldown = int(getattr(
            self._config, "koi_mbti_change_days", 30)) * 86400
        if current.get("mbti") and current.get("mbti") != mbti and \
                cooldown > 0 and \
                now - int(current.get("mbti_updated_at") or 0) < cooldown:
            return f"MBTI 30 天内只能改一次（当前 {current['mbti']}）。"

        self._store.save_profile(
            chat_id, sender_id, mbti=mbti, mbti_updated_at=now)
        profile = self._profile(chat_id, sender_id)
        sign = profile.get("sign") or "未设置星座"
        title = f"{sign.replace('座', '')} · {mbti} · 观星者"
        free_star = ""
        affinity = rules.mbti_affinity(mbti)
        if affinity and self._star_total(chat_id, sender_id) == 0:
            self._store.add_star(chat_id, sender_id, affinity, 1)
            free_star = (
                f"\n🎁 免费点亮{_QUADRANT_LABEL[affinity]}第一星"
                f"（{rules.star_name(affinity, 1)}）")
        return (
            f"MBTI 已记录：{mbti}。\n"
            f"命格称号：{title}{free_star}\n"
            f"👉 发「星盘」查看完整命盘。"
        )

    def profile(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        prefix = self._settle_prefix(chat_id)
        self._ensure_member(chat_id, sender_id, sender_name)
        data = self._profile(chat_id, sender_id)
        if not data.get("sign"):
            return prefix + (
                "还没设置本命星。\n"
                "👉 发「我是 天蝎座」，或「设置生日 10-24」。"
            )
        lucky = rules.lucky_sign(chat_id, local_week())
        is_lucky = data["sign"] == lucky
        fortune = self._store.fortune(chat_id, sender_id)
        rank = rules.rank_for(fortune)
        event = rules.event_for(chat_id, local_day())
        counts = self._store.star_counts(chat_id, sender_id)
        core_total = sum(counts.get(k, 0) for k in _CORE_KEYS)
        title = data.get("title") or (
            f"{data['sign'].replace('座', '')} · "
            f"{data.get('mbti') or '未知'} · 观星者"
        )
        star_line = (
            f"星盘 {core_total}/28｜青龙 {counts.get('qinglong', 0)}/7 "
            f"白虎 {counts.get('baihu', 0)}/7 "
            f"朱雀 {counts.get('zhuque', 0)}/7 "
            f"玄武 {counts.get('xuanwu', 0)}/7"
        )
        if counts.get("outer"):
            star_line += f"｜外圈 {counts['outer']}/60"
        if data.get("awakening"):
            awakening = rules.AWAKENING_BY_KEY.get(data["awakening"])
            if awakening:
                star_line += f"\n觉醒：{awakening['name']}"
        noble = data.get("noble_star") or ""
        life = data.get("life_number") or 0
        extra = f"\n本命宿：{noble}｜生命数字：{life}" if noble else ""
        bazi_line = ""
        if int(data.get("birth_year") or 0):
            hex_count = self._store.collection_count(
                chat_id, sender_id, "hexagram")
            bazi_line = (
                f"\n八字：{self._birth_label(data)}"
                f"｜卦谱 {hex_count}/64")
        return prefix + (
            f"【本命星盘】\n"
            f"太阳：{data['sign']}｜月亮：{data.get('moon_sign') or '—'}"
            f"｜上升：{data.get('rising_sign') or '—'}{extra}\n"
            f"MBTI：{data.get('mbti') or '未设置'}｜命格：{title}\n"
            f"{star_line}{bazi_line}\n"
            f"今日星象：{event['name']} — {event['desc']}\n"
            f"本周幸运星座：{lucky}"
            f"{'（就是你）' if is_lucky else ''}\n"
            f"气运 {fortune}（{rank['name']}）\n"
            f"👉 发「点星 青龙」开始点亮星盘。"
        )

    def star_event(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        event = rules.event_for(chat_id, local_day())
        lucky = rules.lucky_sign(chat_id, local_week())
        profile = self._profile(chat_id, sender_id)
        is_lucky = bool(profile.get("sign")) and profile["sign"] == lucky
        fortune = self._store.fortune(chat_id, sender_id)
        rank = rules.rank_for(fortune)
        return (
            f"今日星象：{event['name']} — {event['desc']}\n"
            f"本周幸运星座：{lucky}"
            f"{'（就是你）' if is_lucky else ''}\n"
            f"气运 {fortune}（{rank['name']}）\n"
            f"👉 发「观星」领取今日香火。"
        )

    def share_card(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, _ = self._ids(msg)
        daily = self._daily(chat_id, sender_id, local_day())
        if not int(daily.get("sign_id") or 0):
            return "今天还没求签，先发「求签」再分享。"
        sign = sign_by_id(int(daily["sign_id"])) or {}
        level = str(daily.get("sign_level") or "")
        profile = self._profile(chat_id, sender_id)
        identity = profile.get("sign") or "星盘"
        return (
            f"【{identity}·今日签】\n"
            f"{rules.level_emoji(level)}{level}「{sign.get('name', '')}」\n"
            f"欧气 {self._luck_value(daily)}｜宜 {daily.get('yi', '')}\n"
            f"{sign.get('phrase', '')}\n"
            f"#星盘 #求签"
        )

    # ── 八字 / 八卦 ──────────────────────────────────────────────

    @staticmethod
    def _birth_dict(profile: dict) -> dict | None:
        year = int(profile.get("birth_year") or 0)
        month = int(profile.get("birth_month") or 0)
        day = int(profile.get("birth_day") or 0)
        if not (year and month and day):
            return None
        hour = int(profile.get("birth_hour") or -1)
        return {
            "year": year, "month": month, "day": day,
            "hour": None if hour < 0 else hour, "minute": 0,
        }

    def _chart(self, profile: dict) -> dict | None:
        birth = self._birth_dict(profile)
        return bazi_rules.bazi(birth) if birth else None

    @staticmethod
    def _birth_label(profile: dict) -> str:
        hour = int(profile.get("birth_hour") or -1)
        label = (f"{int(profile.get('birth_year') or 0):04d}-"
                 f"{int(profile.get('birth_month') or 0):02d}-"
                 f"{int(profile.get('birth_day') or 0):02d}")
        if hour >= 0:
            label += f" {hour:02d}时"
        return label

    @staticmethod
    def _pillar_text(chart: dict) -> str:
        return "｜".join(
            f"{name}{stem}{branch}"
            for name, stem, branch in chart["pillars"])

    @staticmethod
    def _element_text(chart: dict) -> str:
        counts = chart["elements"]
        return " ".join(f"{element}{counts[element]}" for element in
                        ("木", "火", "土", "金", "水"))

    @staticmethod
    def _hexagram_from_id(item_id: int) -> dict:
        index = max(1, min(64, int(item_id))) - 1
        upper = index // 8
        lower = index % 8
        return {
            "name": bazi_rules.HEXAGRAMS[upper][lower],
            "symbol": f"{bazi_rules.TRIGRAM_SYMBOLS[upper]}"
                      f"{bazi_rules.TRIGRAM_SYMBOLS[lower]}",
            "upper": bazi_rules.TRIGRAMS[upper],
            "lower": bazi_rules.TRIGRAMS[lower],
        }

    def set_bazi(self, msg: dict, raw: str) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        birth = bazi_rules.parse_birth(raw)
        if birth is None:
            return (
                "没认出出生时间。\n"
                "👉 发「设置八字 1995-08-15 08:30」，"
                "不知道时辰就发「设置八字 1995-08-15」。"
            )
        chart = bazi_rules.bazi(birth)
        sign = rules.sign_from_date(birth["month"], birth["day"])
        moon = rules.moon_sign(sender_id)
        rising = rules.rising_sign(sender_id)
        noble = rules.noble_star(birth["month"], birth["day"])
        life = rules.life_number(birth["month"], birth["day"])
        hour_value = -1 if birth["hour"] is None else int(birth["hour"])
        self._store.save_profile(
            chat_id, sender_id,
            sign=sign, moon=moon, rising=rising,
            noble_star=noble, life_number=life,
            birth_year=birth["year"], birth_month=birth["month"],
            birth_day=birth["day"], birth_hour=hour_value,
            sign_updated_at=int(time.time()),
        )
        note = "" if birth["hour"] is not None else \
            "\n未填时辰，只排三柱；补时辰可发「设置八字 1995-08-15 08:30」。"
        label = (f"{birth['year']:04d}-{birth['month']:02d}-"
                 f"{birth['day']:02d}")
        if hour_value >= 0:
            label += f" {hour_value:02d}时"
        return (
            f"八字已记录：{label}\n"
            f"四柱：{self._pillar_text(chart)}\n"
            f"五行：{self._element_text(chart)}｜"
            f"{chart['dominant']}旺｜{chart['quadrant']}命\n"
            f"生肖：{chart['zodiac']}｜星座：{sign}\n"
            f"本命宿：{noble}｜生命数字：{life}{note}\n"
            f"👉 发「八字」看天圆地方盘，发「批命」看今日解读。"
        )

    def bazi_card(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        profile = self._profile(chat_id, sender_id)
        chart = self._chart(profile)
        if chart is None:
            return (
                "还没设置出生时间。\n"
                "👉 发「设置八字 1995-08-15 08:30」，"
                "不知道时辰就发「设置八字 1995-08-15」。"
            )
        daily = self._daily(chat_id, sender_id, local_day())
        if daily.get("hexagram_id"):
            cast = self._hexagram_from_id(int(daily["hexagram_id"]))
            cast_line = f"今日卦象：{cast['name']}（{cast['symbol']}）"
        else:
            cast = bazi_rules.hexagram(
                chart, f"{sender_id}:{local_day()}")
            cast_line = (
                f"今日卦象：{cast['name']}（{cast['symbol']}）"
                f"｜未起卦，先给你排一卦")
        palace = "\n".join(bazi_rules.palace(chart))
        return (
            f"【命盘】{self._birth_label(profile)}\n"
            f"四柱：{self._pillar_text(chart)}\n"
            f"五行：{self._element_text(chart)}｜"
            f"{chart['dominant']}旺｜{chart['quadrant']}命\n"
            f"生肖：{chart['zodiac']}｜日主：{chart['day_master']}"
            f"（{chart['day_master_element']}）\n"
            f"天圆地方（洛书九宫）：\n{palace}\n"
            f"{cast_line}\n"
            f"本命宿：{profile.get('noble_star') or '—'}"
            f"｜生命数字：{profile.get('life_number') or '—'}\n"
            f"👉 发「批命」看今日解读，发「起卦」收卦谱。"
            f"（娱乐向，不必当真）"
        )

    def cast_hexagram(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        if int(daily.get("hexagram_used") or 0):
            cast = self._hexagram_from_id(int(daily.get("hexagram_id") or 1))
            return (
                f"今天已经起过卦了：{cast['name']}（{cast['symbol']}）\n"
                f"👉 发「批命」看完整解读。"
            )
        profile = self._profile(chat_id, sender_id)
        chart = self._chart(profile)
        if chart is None:
            return (
                "起卦前先设置出生时间。\n"
                "👉 发「设置八字 1995-08-15」，有时辰更好："
                "「设置八字 1995-08-15 08:30」。"
            )
        cast = bazi_rules.hexagram(
            chart, f"{sender_id}:{day}:{profile.get('birth_year')}-"
                   f"{profile.get('birth_month')}-{profile.get('birth_day')}")
        gain = int(getattr(self._config, "koi_hexagram_merit", 10))
        self._store.grant(
            chat_id, sender_id, sender_name, "merit", gain,
            "hexagram", ref_id=f"hexagram:{day}")
        self._add_luck(chat_id, sender_id, day, 2)
        daily["hexagram_used"] = 1
        daily["hexagram_id"] = int(cast["number"])
        self._save(daily)
        new = self._store.collection_add(
            chat_id, sender_id, "hexagram", int(cast["number"]))
        count = self._store.collection_count(
            chat_id, sender_id, "hexagram")
        lines = [
            f"🎴 起卦：{cast['name']}（{cast['symbol']}）",
            f"上卦 {cast['upper']}｜下卦 {cast['lower']}｜"
            f"动爻 第{cast['changing_line']}爻",
            f"变卦：{cast['changed']}",
            cast["upper_advice"],
            f"香火 +{gain}｜今日欧气 +2｜卦谱 {count}/64",
        ]
        if new:
            lines.append("📜 新卦收入卦谱")
        lines.append("👉 发「批命」看完整解读。")
        return "\n".join(lines)

    def daily_fortune(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        if int(daily.get("fortune_used") or 0):
            return "今天已经批过命了，明天再来。"
        profile = self._profile(chat_id, sender_id)
        chart = self._chart(profile)
        if chart is None:
            return (
                "批命前先设置出生时间。\n"
                "👉 发「设置八字 1995-08-15」，有时辰更好："
                "「设置八字 1995-08-15 08:30」。"
            )
        cast = bazi_rules.hexagram(
            chart, f"{sender_id}:{day}:{profile.get('birth_year')}-"
                   f"{profile.get('birth_month')}-{profile.get('birth_day')}")
        lines = [f"【批命】{day}"]
        lines.extend(bazi_rules.reading(
            chart, cast, bazi_rules.today_ganzhi()))
        lines.append(
            f"本卦：{cast['name']}｜变卦：{cast['changed']}")
        gain = int(getattr(self._config, "koi_fortune_merit", 10))
        luck = int(getattr(self._config, "koi_fortune_luck", 3))
        self._store.grant(
            chat_id, sender_id, sender_name, "merit", gain,
            "fortune", ref_id=f"fortune:{day}")
        self._add_luck(chat_id, sender_id, day, luck)
        daily["fortune_used"] = 1
        daily["hexagram_id"] = int(cast["number"])
        self._save(daily)
        lines.append(f"香火 +{gain}｜今日欧气 +{luck}")
        lines.append("👉 发「八字」看天圆地方盘。")
        return "\n".join(lines)

    def hexagram_book(self, msg: dict) -> str | None:
        if not self.enabled(msg):
            return None
        chat_id, sender_id, _ = self._ids(msg)
        owned = self._store.collection_ids(
            chat_id, sender_id, "hexagram")
        names = [self._hexagram_from_id(item)["name"] for item in owned]
        lines = [f"【卦谱】已收集 {len(owned)}/64"]
        if names:
            lines.append("、".join(names[:20]) +
                         ("……" if len(names) > 20 else ""))
        else:
            lines.append("还没有起过卦。发「起卦」收第一卦。")
        lines.append("👉 每天起一卦，集齐六十四卦。")
        return "\n".join(lines)

    # ── 聊天积德 ─────────────────────────────────────────────────

    def award_chat_merit(self, msg: dict) -> int:
        if not self.enabled(msg):
            return 0
        if int(msg.get("msg_type") or 0) != 1:
            return 0
        now = int(time.time())
        if now - int(msg.get("timestamp") or now) > 300:
            return 0
        chat_id, sender_id, sender_name = self._ids(msg)
        self._ensure_member(chat_id, sender_id, sender_name)
        day = local_day()
        daily = self._daily(chat_id, sender_id, day)
        cap = int(getattr(self._config, "koi_chat_merit_daily_cap", 20))
        if daily.get("yi") == "聊天":
            cap += 5
        if int(daily.get("chat_merit") or 0) >= cap:
            return 0
        ref = str(msg.get("message_id") or f"chat:{now}")
        inserted = self._store.grant(
            chat_id, sender_id, sender_name, "merit",
            int(getattr(self._config, "koi_chat_merit_per_message", 1)),
            "chat", ref_id=ref,
        )
        if not inserted:
            return 0
        daily["chat_merit"] = int(daily.get("chat_merit") or 0) + 1
        self._save(daily)
        return 1
