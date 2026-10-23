"""Fishing (钓鱼) — cast resolution and balance rewards.

Design contract with the points economy:

* Fishing writes ledger rows with ``category='fish'``.
* ``PointStore.balance_of`` sums *all* categories, so fish increase the
  spendable balance (same wallet used by the shop, duels and shields).
* ``PointStore.earned_of`` / boards / levels only count ``earn`` and
  ``adjust``, so fishing **never** changes cumulative points or rank.

Pure formulas live at module level so they can be tested without a DB;
:class:`FishingService` handles limits, inventory and persistence.
"""

from __future__ import annotations

import logging
import random
import time

from .fishing_catalog import RARITY_BONUS_MULT, FishingCatalog
from .models import FishCatch, FishingResult
from .store import PointStore
from .util import is_group_enabled, local_day, local_hour_start

logger = logging.getLogger(__name__)

MIN_MISS_CHANCE = 0.02
MAX_MISS_CHANCE = 0.50


# ── Pure formulas ────────────────────────────────────────────────────

def species_weight(species: dict, rare_bonus: float) -> float:
    """Effective roll weight for one species under a rare bonus."""
    base = float(species.get("weight", 0) or 0)
    multiplier = RARITY_BONUS_MULT.get(species.get("rarity", ""), 0.0)
    return base * (1.0 + max(0.0, float(rare_bonus)) * multiplier)


def pick_species(species: list[dict], rare_bonus: float,
                 roll: float) -> dict | None:
    """Pick one species by weighted roll in ``[0, 1)``."""
    weighted = [(item, species_weight(item, rare_bonus))
                for item in species]
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return None
    target = min(max(float(roll), 0.0), 0.999999) * total
    cursor = 0.0
    for item, weight in weighted:
        cursor += weight
        if target < cursor:
            return item
    return weighted[-1][0]


def roll_weight(species: dict, roll: float) -> int:
    """Weight in grams, uniformly inside the species range."""
    low = int(species.get("min_weight_g", 1))
    high = int(species.get("max_weight_g", low))
    if high <= low:
        return max(1, low)
    offset = int(min(max(float(roll), 0.0), 0.999999) * (high - low + 1))
    return max(low, min(high, low + offset))


def catch_amount(species: dict, weight_g: int, *,
                 rod_value_bonus: float = 0.0,
                 bait_value_bonus: float = 0.0) -> int:
    """Balance credited for one catch.

    Size matters (0.6× ~ 1.4× of base value) and gear adds a percentage.
    Junk species keep a base value of 0 and therefore pay nothing.
    """
    base = int(species.get("base_value", 0) or 0)
    if base <= 0:
        return 0
    low = int(species.get("min_weight_g", 1))
    high = int(species.get("max_weight_g", low))
    span = max(1, high - low)
    size_factor = 0.6 + 0.8 * max(
        0.0, min(1.0, (int(weight_g) - low) / span),
    )
    gear_factor = (1.0 + max(0.0, float(rod_value_bonus))) * \
        (1.0 + max(0.0, float(bait_value_bonus)))
    return max(1, int(round(base * size_factor * gear_factor)))


def effective_miss_chance(base_chance: float,
                          miss_reduction: float = 0.0) -> float:
    chance = float(base_chance) - max(0.0, float(miss_reduction))
    return max(MIN_MISS_CHANCE, min(MAX_MISS_CHANCE, chance))


def format_weight(grams: int) -> str:
    grams = max(0, int(grams))
    if grams >= 1000:
        return f"{grams / 1000:.2f}kg"
    return f"{grams}g"


def _roll(rng) -> float:
    if rng is None:
        return random.random()
    if hasattr(rng, "random"):
        return float(rng.random())
    return float(rng())


# ── Service ──────────────────────────────────────────────────────────

class FishingService:
    """Resolves 「钓鱼」 casts against the store and the catalog."""

    def __init__(self, store: PointStore, config,
                 catalog: FishingCatalog | None = None):
        self._store = store
        self._config = config
        self._catalog = catalog or FishingCatalog(
            getattr(config, "fishing_catalog_path", "data/fishing.json")
        )

    @property
    def catalog(self) -> FishingCatalog:
        return self._catalog

    # ── Helpers ───────────────────────────────────────────────────

    def is_enabled(self, msg: dict) -> bool:
        cfg = self._config
        if not getattr(cfg, "fishing_enabled", False):
            return False
        return is_group_enabled(
            str(msg.get("chat_id") or ""), str(msg.get("group_name") or ""),
            getattr(cfg, "fishing_groups", ["*"]),
        )

    def rod_of(self, chat_id: str, sender_id: str) -> dict:
        rod_id = self._store.fishing_equipped_rod(chat_id, sender_id)
        rod = self._catalog.by_rod_id(rod_id) if rod_id else None
        return rod or self._catalog.default_rod()

    def _next_bait(self, chat_id: str, sender_id: str) -> dict | None:
        """Cheapest owned bait with stock left (auto-consumed)."""
        rows = [
            row for row in self._store.fishing_inventory(
                chat_id, sender_id)
            if row["kind"] == "bait" and int(row["quantity"]) > 0
        ]
        best: tuple[float, dict] | None = None
        for row in rows:
            bait = self._catalog.by_bait_id(row["item_id"])
            if bait is None:
                continue
            key = (float(bait["price"]), bait["id"])
            if best is None or key < (float(best[1]["price"]), best[1]["id"]):
                best = (float(bait["price"]), bait)
        return best[1] if best else None

    # ── Cast ──────────────────────────────────────────────────────

    def cast(self, msg: dict, count: int = 1, rng=None,
             now_ts: int | None = None) -> FishingResult:
        """Run up to *count* casts for one command message."""
        cfg = self._config
        chat_id = str(msg.get("chat_id") or "")
        sender_id = str(msg.get("sender_id") or "")
        sender_name = str(msg.get("sender_name") or "")
        message_id = str(msg.get("message_id") or "")
        now = int(time.time()) if now_ts is None else int(now_ts)
        day = local_day(now)
        hour_start = local_hour_start(now)

        if not self.is_enabled(msg):
            return FishingResult(False, "disabled", "钓鱼功能未开启。")
        if not chat_id or not sender_id:
            return FishingResult(False, "invalid", "钓鱼失败，请稍后再试。")

        limit = max(1, int(getattr(cfg, "fishing_hourly_attempts", 30)))
        cap = max(0, int(getattr(cfg, "fishing_daily_cap", 120)))
        cooldown = max(0, int(getattr(cfg, "fishing_cooldown_sec", 5)))
        max_batch = max(1, int(getattr(cfg, "fishing_max_batch", 10)))
        base_miss = float(getattr(cfg, "fishing_miss_chance", 0.15))

        attempts = self._store.fishing_attempts_since(
            chat_id, sender_id, hour_start)
        earned = self._store.fishing_earned_on_day(chat_id, sender_id, day)

        if attempts >= limit:
            reset_in = max(0, hour_start + 3600 - now)
            reset_at = time.strftime(
                "%H:%M", time.localtime(hour_start + 3600))
            wait_minutes = max(1, (reset_in + 59) // 60)
            return FishingResult(
                False, "attempts",
                f"本小时的钓鱼次数已用完（{limit} 次），"
                f"{wait_minutes} 分钟后（{reset_at}）重置。",
                attempts_used=attempts, attempts_limit=limit,
                earned_today=earned, daily_cap=cap, wait_seconds=reset_in,
            )
        if cap > 0 and earned >= cap:
            return FishingResult(
                False, "cap",
                f"今天钓鱼收益已达上限（{cap}），明天再来。",
                attempts_used=attempts, attempts_limit=limit,
                earned_today=earned, daily_cap=cap,
            )

        last = self._store.fishing_last_cast_at(chat_id, sender_id)
        if cooldown and last and now - last < cooldown:
            wait = cooldown - (now - last)
            return FishingResult(
                False, "cooldown",
                f"刚抛过竿，{wait} 秒后再来。",
                wait_seconds=wait,
                attempts_used=attempts, attempts_limit=limit,
                earned_today=earned, daily_cap=cap,
            )

        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, max_batch, limit - attempts))

        rod = self.rod_of(chat_id, sender_id)
        species_list = self._catalog.species()
        cast_rows: list[FishCatch] = []
        total_value = 0

        for index in range(count):
            if cap > 0 and earned >= cap:
                break

            bait = self._next_bait(chat_id, sender_id)
            bait_id = bait["id"] if bait else ""
            rare_bonus = float(rod.get("rare_bonus", 0) or 0) + \
                float(bait.get("rare_bonus", 0) if bait else 0)
            value_bonus = float(rod.get("value_bonus", 0) or 0) + \
                float(bait.get("value_bonus", 0) if bait else 0)

            miss = _roll(rng) < effective_miss_chance(
                base_miss, float(rod.get("miss_reduction", 0) or 0),
            )

            if miss:
                species = None
                weight_g = 0
                amount = 0
            else:
                species = pick_species(species_list, rare_bonus, _roll(rng))
                if species is None:
                    break
                weight_g = roll_weight(species, _roll(rng))
                amount = catch_amount(
                    species, weight_g,
                    rod_value_bonus=value_bonus,
                    bait_value_bonus=0.0,
                )
                if cap > 0:
                    amount = min(amount, max(0, cap - earned))

            inserted, _bait_used = self._store.commit_fishing_cast(
                chat_id=chat_id,
                sender_id=sender_id,
                sender_name=sender_name,
                message_id=message_id or f"fish:{chat_id}:{now}:{index}",
                cast_index=index,
                rod_id=str(rod.get("id", "")),
                bait_id=bait_id,
                species_id=str(species.get("id", "")) if species else "",
                species_name=str(species.get("name", "")) if species else "",
                rarity=str(species.get("rarity", "")) if species else "",
                weight_g=weight_g,
                amount=amount,
                day=day,
                now_ts=now,
            )
            if not inserted:
                break

            cast_rows.append(FishCatch(
                species_id=str(species.get("id", "")) if species else "",
                species_name=str(species.get("name", "")) if species else "",
                rarity=str(species.get("rarity", "")) if species else "",
                weight_g=weight_g,
                amount=amount,
                rod_id=str(rod.get("id", "")),
                bait_id=bait_id,
                delay_sec=self._catalog.delay_for(
                    str(species.get("rarity", "")) if species else "miss"),
            ))
            total_value += amount
            earned += amount

        if not cast_rows:
            return FishingResult(
                False, "duplicate", "这次没能抛竿，请稍后再试。",
                attempts_used=attempts, attempts_limit=limit,
                earned_today=self._store.fishing_earned_on_day(
                    chat_id, sender_id, day),
                daily_cap=cap, rod_id=str(rod.get("id", "")),
            )

        return FishingResult(
            True, "ok", "",
            casts=cast_rows,
            total_value=total_value,
            balance_after=self._store.balance_of(chat_id, sender_id),
            attempts_used=self._store.fishing_attempts_since(
                chat_id, sender_id, hour_start),
            attempts_limit=limit,
            earned_today=self._store.fishing_earned_on_day(
                chat_id, sender_id, day),
            daily_cap=cap,
            rod_id=str(rod.get("id", "")),
            delay_sec=max((item.delay_sec for item in cast_rows), default=0),
        )

    # ── Stats ─────────────────────────────────────────────────────

    def stats(self, chat_id: str, sender_id: str,
              now_ts: int | None = None) -> dict:
        now = int(time.time()) if now_ts is None else int(now_ts)
        day = local_day(now)
        hour_start = local_hour_start(now)
        limit = max(1, int(getattr(
            self._config, "fishing_hourly_attempts", 30)))
        cap = max(0, int(getattr(self._config, "fishing_daily_cap", 120)))

        rod = self.rod_of(chat_id, sender_id)
        inventory = self._store.fishing_inventory(chat_id, sender_id)
        baits = []
        for row in inventory:
            if row["kind"] != "bait" or int(row["quantity"]) <= 0:
                continue
            bait = self._catalog.by_bait_id(row["item_id"])
            if bait is None:
                continue
            baits.append({**bait, "quantity": int(row["quantity"])})
        baits.sort(key=lambda item: item["price"])

        totals = self._store.fishing_totals(chat_id, sender_id)
        caught = self._store.fishing_collection(chat_id, sender_id)
        return {
            "attempts": self._store.fishing_attempts_since(
                chat_id, sender_id, hour_start),
            "attempts_limit": limit,
            "attempts_reset_in": max(0, hour_start + 3600 - now),
            "earned_today": self._store.fishing_earned_on_day(
                chat_id, sender_id, day),
            "daily_cap": cap,
            "balance": self._store.balance_of(chat_id, sender_id),
            "rod": rod,
            "baits": baits,
            "total_value": int(totals.get("value", 0)),
            "catches": int(totals.get("catches", 0)),
            "biggest_weight": int(totals.get("biggest", 0)),
            "caught_species": caught,
            "species_total": len(self._catalog.species()),
            "last_cast_at": self._store.fishing_last_cast_at(
                chat_id, sender_id),
        }
