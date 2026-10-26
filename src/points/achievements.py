"""成就与称号 — cross-gameplay milestones.

Achievement definitions live here (data, not DB).  Unlocks are persisted
by :class:`PointStore` and each one pays a one-time balance reward.
Unlocked achievements also grant a display title that a member can equip.
"""

from __future__ import annotations

# kind values map directly onto snapshot keys, except "species_complete"
# which compares caught species against the catalog size.
ACHIEVEMENTS: list[dict] = [
    {"id": "newcomer", "name": "初来乍到", "title": "群新人",
     "kind": "earned", "value": 20, "reward": 30,
     "desc": "累计积分达到 20"},
    {"id": "chatterbox", "name": "话痨认证", "title": "话痨",
     "kind": "earned", "value": 500, "reward": 80,
     "desc": "累计积分达到 500"},
    {"id": "pillar", "name": "群柱", "title": "群柱",
     "kind": "earned", "value": 1200, "reward": 150,
     "desc": "累计积分达到 1200"},
    {"id": "legend", "name": "传说", "title": "传说",
     "kind": "earned", "value": 3000, "reward": 300,
     "desc": "累计积分达到 3000"},
    {"id": "eternal", "name": "永恒", "title": "永恒",
     "kind": "earned", "value": 120000, "reward": 1000,
     "desc": "累计积分达到 120000"},

    {"id": "collector", "name": "装备党", "title": "装备党",
     "kind": "equipment_count", "value": 5, "reward": 100,
     "desc": "拥有 5 件装备"},
    {"id": "enhancer", "name": "强化大师", "title": "强化大师",
     "kind": "max_enhance", "value": 10, "reward": 300,
     "desc": "任意装备强化到 +10"},

    {"id": "first_blood", "name": "初战告捷", "title": "初战告捷",
     "kind": "duel_wins", "value": 1, "reward": 50,
     "desc": "赢下 1 场切磋"},
    {"id": "war_god", "name": "战神", "title": "战神",
     "kind": "duel_wins", "value": 50, "reward": 500,
     "desc": "生涯赢下 50 场切磋"},

    {"id": "first_cast", "name": "初次下杆", "title": "钓鱼新手",
     "kind": "catches", "value": 1, "reward": 30,
     "desc": "钓到第 1 条鱼"},
    {"id": "fish_master", "name": "图鉴大师", "title": "图鉴大师",
     "kind": "species_complete", "value": 1, "reward": 500,
     "desc": "集齐全部鱼种图鉴"},
    {"id": "mythic_hunter", "name": "神话猎手", "title": "神话猎手",
     "kind": "mythic_caught", "value": 1, "reward": 600,
     "desc": "钓到任意一条神话鱼"},

    {"id": "streak_7", "name": "七日连签", "title": "全勤达人",
     "kind": "streak_days", "value": 7, "reward": 100,
     "desc": "连续活跃（签到）7 天"},

    {"id": "boss_hunter", "name": "Boss 猎手", "title": "Boss 猎手",
     "kind": "boss_damage", "value": 10000, "reward": 400,
     "desc": "累计对群 Boss 造成 10000 伤害"},

    {"id": "realm_1", "name": "炼气修士", "title": "炼气修士",
     "kind": "realm", "value": 1, "reward": 200,
     "desc": "完成第 1 次境界突破"},
]

_BY_ID = {item["id"]: item for item in ACHIEVEMENTS}


def achievement_by_id(achievement_id: str) -> dict | None:
    return _BY_ID.get(str(achievement_id or ""))


def achievement_by_title(title: str) -> dict | None:
    key = str(title or "").strip()
    if not key:
        return None
    for item in ACHIEVEMENTS:
        if item["title"] == key:
            return item
    return None


class AchievementService:
    """Evaluates achievements against the member's current stats."""

    def __init__(self, store, config, levels, duels, fishing=None):
        self._store = store
        self._config = config
        self._levels = levels
        self._duels = duels
        self._fishing = fishing

    # ── Snapshot ──────────────────────────────────────────────────

    def snapshot(self, chat_id: str, sender_id: str,
                 now_ts: int | None = None) -> dict:
        stats = self._store.member_stats(
            chat_id, sender_id, now_ts=now_ts)
        catches = int(self._store.fishing_totals(
            chat_id, sender_id).get("catches", 0))
        caught = len(self._store.fishing_collection(chat_id, sender_id))
        species_total = 0
        if self._fishing is not None:
            try:
                species_total = len(self._fishing.catalog.species())
            except Exception:  # noqa: BLE001 - catalog is optional
                species_total = 0
        record = self._duels.record(chat_id, sender_id)
        return {
            "earned": stats.earned,
            "equipment_count": self._store.equipment_count(
                chat_id, sender_id),
            "max_enhance": self._store.max_enhance_level(
                chat_id, sender_id),
            "duel_wins": int(record.get("wins", 0)),
            "catches": catches,
            "species_count": caught,
            "species_total": species_total,
            "mythic_caught": 1 if self._store.has_rarity_catch(
                chat_id, sender_id, "mythic") else 0,
            "streak_days": self._store.streak_days(
                chat_id, sender_id, now_ts),
            "boss_damage": self._store.boss_total_damage(
                chat_id, sender_id),
            "realm": self._store.realm_of(chat_id, sender_id),
        }

    @staticmethod
    def qualifies(definition: dict, snapshot: dict) -> bool:
        kind = definition.get("kind", "")
        if kind == "species_complete":
            total = int(snapshot.get("species_total", 0))
            return total > 0 and \
                int(snapshot.get("species_count", 0)) >= total
        value = int(definition.get("value", 0))
        return int(snapshot.get(kind, 0)) >= value

    # ── Evaluation ────────────────────────────────────────────────

    def evaluate(self, chat_id: str, sender_id: str,
                 now_ts: int | None = None) -> dict:
        """Unlock every newly-qualified achievement."""
        unlocked = self._store.achievements_of(chat_id, sender_id)
        snapshot = self.snapshot(chat_id, sender_id, now_ts=now_ts)
        newly: list[dict] = []
        for definition in ACHIEVEMENTS:
            if definition["id"] in unlocked:
                continue
            if not self.qualifies(definition, snapshot):
                continue
            if self._store.unlock_achievement(
                chat_id, sender_id, definition["id"],
                int(definition.get("reward", 0)), now_ts=now_ts,
            ):
                newly.append(definition)
                unlocked.add(definition["id"])
        return {
            "new": newly,
            "unlocked": unlocked,
            "snapshot": snapshot,
        }

    def unlocked(self, chat_id: str, sender_id: str) -> list[dict]:
        ids = self._store.achievements_of(chat_id, sender_id)
        return [item for item in ACHIEVEMENTS if item["id"] in ids]

    def title_of(self, chat_id: str, sender_id: str) -> str:
        return self._store.equipped_title(chat_id, sender_id)
