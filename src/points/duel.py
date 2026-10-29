"""Combat power, win probability and duel resolution."""

from __future__ import annotations

import logging
import math
import random
import time

from .equipment import EquipmentCatalog, effective_stats
from .levels import LevelTable
from .models import DuelResult, PowerBreakdown
from .realm import realm_info
from .store import PointStore
from .techniques import effects_for
from .util import is_group_enabled, local_day

logger = logging.getLogger(__name__)

BASE_ATTACK = 5
BASE_DEFENSE = 5
ATTACK_PER_LEVEL = 2
DEFENSE_PER_LEVEL = 3
# Backwards-compatible total (attack + defense) used by older displays.
BASE_POWER = BASE_ATTACK + BASE_DEFENSE
POWER_PER_LEVEL = ATTACK_PER_LEVEL + DEFENSE_PER_LEVEL
MIN_DEFENDER_BALANCE = 2

# 切磋段位 — based on career wins (not daily), so strong players keep a
# long-term ladder after their daily attempts are spent.
DUEL_RANKS: list[dict] = [
    {"rank": 0, "title": "青铜", "min_wins": 0},
    {"rank": 1, "title": "白银", "min_wins": 5},
    {"rank": 2, "title": "黄金", "min_wins": 15},
    {"rank": 3, "title": "铂金", "min_wins": 30},
    {"rank": 4, "title": "钻石", "min_wins": 50},
    {"rank": 5, "title": "王者", "min_wins": 80},
    {"rank": 6, "title": "战神", "min_wins": 120},
]


def duel_rank(wins: int) -> dict:
    """Return the duel rank entry for *wins*, plus progress to the next."""
    wins = max(0, int(wins or 0))
    current = DUEL_RANKS[0]
    for entry in DUEL_RANKS:
        if wins >= entry["min_wins"]:
            current = entry
        else:
            break
    index = DUEL_RANKS.index(current)
    nxt = DUEL_RANKS[index + 1] if index + 1 < len(DUEL_RANKS) else None
    return {
        **current,
        "next": nxt,
        "wins_to_next": (
            max(0, int(nxt["min_wins"]) - wins) if nxt else 0
        ),
    }


# ── Pure formulas ────────────────────────────────────────────────────

def win_probability(attacker_power: int, defender_power: int, *,
                    k: float = 1.15,
                    min_prob: float = 0.08,
                    max_prob: float = 0.92) -> float:
    """Chance the attacker wins.

    Higher power wins more often, but never with certainty:
    ``p = clamp(0.5 + 0.5*tanh(k*ln(A/D)), min_prob, max_prob)``.
    """
    if attacker_power <= 0 or defender_power <= 0:
        return min_prob
    x = float(k) * math.log(attacker_power / defender_power)
    p = 0.5 + 0.5 * math.tanh(x)
    return max(min_prob, min(max_prob, p))


def steal_amount(attacker_power: int, defender_power: int,
                 defender_balance: int, *,
                 ratio: float = 0.08,
                 reward_k: float = 1.2,
                 per_fight_cap: int = 20,
                 min_base: int = 2) -> int:
    """Points stolen on a win.

    The base is a share of the defender's balance; the bigger the
    attacker's power advantage, the smaller the multiplier — so ganking
    much weaker members is not profitable.
    """
    if defender_balance <= 0:
        return 0
    base = max(min_base, min(int(per_fight_cap),
                             round(defender_balance * float(ratio))))
    if attacker_power <= 0 or defender_power <= 0:
        gap = 0.0
    else:
        gap = max(0.0, math.log(attacker_power / defender_power))
    multiplier = max(0.2, min(1.0, 1.0 / (1.0 + float(reward_k) * gap)))
    amount = int(round(base * multiplier))
    return max(0, min(amount, int(per_fight_cap), int(defender_balance)))


# ── Service ──────────────────────────────────────────────────────────

class DuelService:
    """Resolves 切磋 commands against the store."""

    def __init__(self, store: PointStore, config,
                 levels: LevelTable, catalog: EquipmentCatalog):
        self._store = store
        self._config = config
        self._levels = levels
        self._catalog = catalog

    # ── Power ─────────────────────────────────────────────────────

    def power(self, chat_id: str, sender_id: str) -> PowerBreakdown:
        earned = self._store.earned_of(chat_id, sender_id)
        level = self._levels.for_points(earned)
        level_no = int(level["level"])
        base_attack = BASE_ATTACK + level_no * ATTACK_PER_LEVEL
        base_defense = BASE_DEFENSE + level_no * DEFENSE_PER_LEVEL

        items: list[dict] = []
        equipment_attack = 0
        equipment_defense = 0
        for owned in self._store.owned_items(chat_id, sender_id):
            if not owned.get("equipped"):
                continue
            item = self._catalog.by_id(owned["item_id"])
            if item is None:
                continue
            enhance_level = int(owned.get("enhance_level", 0) or 0)
            item_attack, item_defense = effective_stats(
                item, enhance_level)
            items.append({
                **item, "enhance_level": enhance_level,
                "attack": item_attack, "defense": item_defense,
                "effective_power": item_attack + item_defense,
            })
            equipment_attack += item_attack
            equipment_defense += item_defense

        realm = self._store.realm_of(chat_id, sender_id)
        realm_bonus = realm_info(realm)["power_bonus"]
        realm_attack = realm_bonus // 2
        realm_defense = realm_bonus - realm_attack

        technique_effects = effects_for(self._store, chat_id, sender_id)
        technique_attack = int(round(technique_effects.get("attack", 0)))
        technique_defense = int(round(technique_effects.get("defense", 0)))

        attack = base_attack + equipment_attack + realm_attack + \
            technique_attack
        defense = base_defense + equipment_defense + realm_defense + \
            technique_defense
        base = base_attack + base_defense
        equipment = equipment_attack + equipment_defense

        return PowerBreakdown(
            total=attack + defense,
            base=base,
            level=level_no,
            title=str(level["title"]),
            equipment=equipment,
            realm=realm,
            realm_bonus=realm_attack + realm_defense,
            attack=attack,
            defense=defense,
            base_attack=base_attack,
            base_defense=base_defense,
            equipment_attack=equipment_attack,
            equipment_defense=equipment_defense,
            realm_attack=realm_attack,
            realm_defense=realm_defense,
            technique_attack=technique_attack,
            technique_defense=technique_defense,
            technique_effects=technique_effects,
            items=items,
        )

    # ── Career record ─────────────────────────────────────────────

    def record(self, chat_id: str, sender_id: str) -> dict:
        """Career duel record with the member's current rank."""
        row = self._store.duel_record(chat_id, sender_id)
        wins = int(row.get("wins", 0))
        total = int(row.get("total", 0))
        return {
            "wins": wins,
            "losses": max(0, total - wins),
            "total": total,
            "stolen": int(row.get("stolen", 0)),
            "rank": duel_rank(wins),
        }

    def leaderboard(self, chat_id: str, limit: int = 10) -> list[dict]:
        """Career win leaderboard, each row annotated with its rank."""
        rows = self._store.duel_leaderboard(chat_id, limit)
        for row in rows:
            row["rank"] = duel_rank(int(row.get("wins", 0)))
        return rows

    # ── Duel ──────────────────────────────────────────────────────

    def challenge(self, msg: dict, target_id: str,
                  rng=None, defender_name: str = "") -> DuelResult:
        """Run one duel attempt.

        ``rng`` may be a ``random.Random`` instance or a zero-arg callable
        returning a float; tests inject deterministic values.
        ``defender_name`` is the caller-resolved display name (group
        nickname); it falls back to the name stored on messages.
        """
        cfg = self._config
        chat_id = str(msg.get("chat_id") or "")
        attacker_id = str(msg.get("sender_id") or "")
        attacker_name = str(msg.get("sender_name") or "")
        message_id = str(msg.get("message_id") or "")
        now = int(time.time())
        day = local_day(now)

        if not getattr(cfg, "duel_enabled", False):
            return DuelResult(False, "disabled", "切磋功能未开启。")
        if not is_group_enabled(chat_id, msg.get("group_name", ""),
                                getattr(cfg, "duel_groups", ["*"])):
            return DuelResult(False, "disabled", "本群未开启切磋。")

        target_id = str(target_id or "")
        if not target_id or target_id == attacker_id:
            return DuelResult(False, "self", "不能和自己切磋。")
        if not self._store.has_member(chat_id, target_id):
            return DuelResult(
                False, "not_member",
                "对方还没在本群发过言，暂时不能切磋。",
            )
        defender_name = (
            str(defender_name or "").strip()
            or self._store.member_name(chat_id, target_id)
        )

        attempts_limit = int(getattr(cfg, "duel_daily_attempts", 10))
        steal_cap = int(getattr(cfg, "duel_daily_steal_cap", 50))
        attempts = self._store.duel_attempts(chat_id, attacker_id, day)
        stolen = self._store.duel_stolen(chat_id, attacker_id, day)

        shield_expiry = self._store.shield_expires_at(
            chat_id, target_id, now,
        )
        if shield_expiry:
            self._store.record_duel(
                chat_id, message_id, attacker_id, attacker_name,
                target_id, defender_name, 0, 0, 0.0, False, 0,
                shield_blocked=True, now_ts=now,
            )
            return DuelResult(
                False, "shield",
                f"{defender_name} 正在免战中，切磋未发起，不消耗次数。",
                shield_remaining=shield_expiry - now,
                attempts_used=attempts,
                attempts_limit=attempts_limit,
            )

        loss_cooldown = int(getattr(cfg, "duel_loss_cooldown_sec", 120))
        last_loss = self._store.last_loss_at(chat_id, attacker_id)
        if last_loss and now - last_loss < loss_cooldown:
            wait = loss_cooldown - (now - last_loss)
            return DuelResult(
                False, "exhausted",
                f"刚输了一场，{self._format_wait(wait)}后才能再次切磋。",
                wait_seconds=wait,
                attempts_used=attempts, attempts_limit=attempts_limit,
            )

        # Daily per-pair cap: each attacker may fight the same defender
        # at most N times a day.  The defender is not limited by how many
        # *different* people attack them.
        pair_limit = int(getattr(cfg, "duel_daily_pair_attempts", 5))
        pair_count = self._store.pair_duels(
            chat_id, attacker_id, target_id, day,
        )
        if pair_limit > 0 and pair_count >= pair_limit:
            return DuelResult(
                False, "pair_limit",
                f"今天已经和 {defender_name} 切磋 {pair_count} 次了"
                f"（每天最多 {pair_limit} 次），明天再来。",
                attempts_used=attempts, attempts_limit=attempts_limit,
            )

        pair_cooldown = int(getattr(cfg, "duel_pair_cooldown_sec", 300))
        last_pair = self._store.last_pair_at(chat_id, attacker_id, target_id)
        if last_pair and now - last_pair < pair_cooldown:
            wait = pair_cooldown - (now - last_pair)
            return DuelResult(
                False, "pair_cooldown",
                f"你和 {defender_name} 刚切磋过，"
                f"{self._format_wait(wait)}后才能再来。",
                wait_seconds=wait,
                attempts_used=attempts, attempts_limit=attempts_limit,
            )

        if attempts >= attempts_limit:
            return DuelResult(
                False, "attempts",
                f"今天的切磋次数已用完（{attempts_limit} 次），明天再来。",
                attempts_used=attempts, attempts_limit=attempts_limit,
            )

        novice_points = int(getattr(
            cfg, "duel_novice_protection_points", 20))
        if self._store.earned_of(chat_id, target_id) < novice_points:
            return DuelResult(
                False, "novice",
                f"{defender_name} 还在新手保护期，先放过他吧。",
                attempts_used=attempts, attempts_limit=attempts_limit,
            )

        defender_balance = self._store.balance_of(chat_id, target_id)
        if defender_balance < MIN_DEFENDER_BALANCE:
            return DuelResult(
                False, "empty",
                f"{defender_name} 囊中羞涩，改天再来。",
                attempts_used=attempts, attempts_limit=attempts_limit,
                defender_balance=defender_balance,
            )

        if stolen >= steal_cap:
            return DuelResult(
                False, "steal_cap",
                f"今天已经抢够 {steal_cap} 灵石了，明天再战。",
                attempts_used=attempts, attempts_limit=attempts_limit,
                stolen_today=stolen, steal_cap=steal_cap,
            )

        attacker = self.power(chat_id, attacker_id)
        defender = self.power(chat_id, target_id)
        prob = win_probability(
            attacker.attack, defender.defense,
            k=float(getattr(cfg, "duel_prob_k", 1.15)),
            min_prob=float(getattr(cfg, "duel_min_prob", 0.08)),
            max_prob=float(getattr(cfg, "duel_max_prob", 0.92)),
        )
        # 功法 can add a small flat win-rate bonus.
        prob = min(
            float(getattr(cfg, "duel_max_prob", 0.92)),
            prob + float(attacker.technique_effects.get("duel_win", 0)),
        )

        roll = self._roll(rng)
        attacker_win = roll < prob

        moved = 0
        if attacker_win:
            moved = steal_amount(
                attacker.attack, defender.defense, defender_balance,
                ratio=float(getattr(cfg, "duel_base_steal_ratio", 0.08)),
                reward_k=float(getattr(cfg, "duel_reward_k", 1.2)),
                per_fight_cap=int(getattr(cfg, "duel_per_fight_cap", 20)),
            )
            moved = min(moved, max(0, steal_cap - stolen))

        self._store.record_duel(
            chat_id, message_id, attacker_id, attacker_name,
            target_id, defender_name, attacker.attack, defender.defense,
            prob, attacker_win, moved, now_ts=now,
        )

        return DuelResult(
            True, "win" if attacker_win else "lose", "",
            attacker_power=attacker.attack,
            defender_power=defender.defense,
            win_prob=prob,
            attacker_win=attacker_win,
            points_moved=moved,
            balance_after=self._store.balance_of(chat_id, attacker_id),
            attempts_used=attempts + 1,
            attempts_limit=attempts_limit,
            stolen_today=stolen + moved,
            steal_cap=steal_cap,
            defender_balance=defender_balance - moved,
        )

    @staticmethod
    def _roll(rng) -> float:
        if rng is None:
            return random.random()
        if hasattr(rng, "random"):
            return float(rng.random())
        return float(rng())

    @staticmethod
    def _format_wait(seconds: int) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} 秒"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes / 60
        return f"{hours:.1f} 小时"
