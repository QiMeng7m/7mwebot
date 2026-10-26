"""Dataclasses shared by the points engine, store and handler."""

from __future__ import annotations

from dataclasses import dataclass, field


class DelayedReply(str):
    """A reply that the router should send after ``delay_sec`` seconds.

    Subclassing ``str`` keeps every existing caller (and test) working:
    the value is still the message text.  The router checks ``delay_sec``
    and, when a backend sender is registered, schedules the send on a
    daemon thread instead of returning the text for immediate delivery.

    ``ack_text`` is sent immediately (via the normal reply path) so the
    group sees "钓鱼中，请稍等…" while the result is still pending.
    """

    delay_sec: int
    ack_text: str

    def __new__(cls, text: str, delay_sec: int = 0,
                ack_text: str = ""):
        obj = super().__new__(cls, text)
        obj.delay_sec = max(0, int(delay_sec))
        obj.ack_text = str(ack_text or "")
        return obj


@dataclass(frozen=True)
class PointAward:
    """One ledger row to write for a single scoring component."""

    sender_id: str
    sender_name: str
    reason: str
    points: int
    category: str = "earn"


@dataclass
class Command:
    """A parsed group command.

    ``kind`` values: member_card, my_info, board, rules, query, shop,
    buy, equip, unequip, bag, power, power_board, duel, buy_shield,
    duel_log, fish, fish_rods, fish_baits, fish_bag, fish_book,
    fish_board, fish_log, admin_adjust, admin_rebuild.
    """

    kind: str
    arg: str = ""
    scope: str = "total"          # board scope: total | season | day
    target_id: str = ""           # resolved wxid for query / duel
    target_name: str = ""
    amount: int = 0               # admin adjustment
    note: str = ""                # admin reason
    candidates: list[str] = field(default_factory=list)


@dataclass
class PowerBreakdown:
    """Effective combat power and where it comes from."""

    total: int
    base: int
    level: int
    title: str
    equipment: int
    realm: int = 0
    realm_bonus: int = 0
    items: list[dict] = field(default_factory=list)


@dataclass
class MemberStats:
    """Aggregated view of one member in one group."""

    sender_id: str
    sender_name: str
    earned: int = 0          # cumulative earned (ranking / level)
    balance: int = 0         # spendable balance
    season_points: int = 0
    day_points: int = 0
    last_at: int = 0
    total_rank: int = 0
    season_rank: int = 0
    streak_days: int = 0


@dataclass
class DuelResult:
    """Outcome of a duel attempt (or the reason it was rejected)."""

    ok: bool
    code: str = ""
    reply: str = ""
    attacker_power: int = 0
    defender_power: int = 0
    win_prob: float = 0.0
    attacker_win: bool = False
    points_moved: int = 0
    balance_after: int = 0
    attempts_used: int = 0
    attempts_limit: int = 0
    stolen_today: int = 0
    steal_cap: int = 0
    wait_seconds: int = 0
    defender_balance: int = 0
    shield_remaining: int = 0


@dataclass
class FishCatch:
    """One cast result (a miss has an empty species id)."""

    species_id: str = ""
    species_name: str = ""
    rarity: str = ""
    weight_g: int = 0
    amount: int = 0
    rod_id: str = ""
    bait_id: str = ""
    duplicate: bool = False
    delay_sec: int = 0


@dataclass
class FishingResult:
    """Outcome of one 「钓鱼」 command (one or more casts)."""

    ok: bool
    code: str = ""
    reply: str = ""
    casts: list[FishCatch] = field(default_factory=list)
    total_value: int = 0
    balance_after: int = 0
    attempts_used: int = 0
    attempts_limit: int = 0
    earned_today: int = 0
    daily_cap: int = 0
    rod_id: str = ""
    wait_seconds: int = 0
    delay_sec: int = 0
