"""Equipment catalog (``data/equipment.json``).

Mirrors the lots/nicknames config pattern: a JSON file with built-in
defaults, validated on every load, editable from the Web UI.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import PROJECT_ROOT

from .skills import skill_by_id

logger = logging.getLogger(__name__)

# 装备品阶 — the quality ladder.  Items start at 凡品 (0) and the catalog's
# ceiling is 圣品 (9).  New high-tier items extend what used to stop at
# 宝品 (6), giving endgame members a longer gear chase.
TIER_TITLES: list[str] = [
    "凡品", "下品", "中品", "上品", "极品",
    "灵品", "宝品", "仙品", "神品", "圣品",
]
EQUIPMENT_TIER_MAX = len(TIER_TITLES) - 1

DEFAULT_SLOTS: list[dict] = [
    {"id": "weapon", "name": "武器"},
    {"id": "armor", "name": "护甲"},
    {"id": "charm", "name": "饰品"},
    {"id": "mount", "name": "坐骑"},
]

DEFAULT_ITEMS: list[dict] = [
    # ── 武器 ──
    {"id": "wood_sword", "name": "木剑", "slot": "weapon",
     "tier": 0, "power": 4, "price": 30, "skills": []},
    {"id": "iron_sword", "name": "铁剑", "slot": "weapon",
     "tier": 1, "power": 9, "price": 90, "skills": []},
    {"id": "black_blade", "name": "玄铁刀", "slot": "weapon",
     "tier": 2, "power": 16, "price": 220, "skills": []},
    {"id": "dragon_sword", "name": "龙纹剑", "slot": "weapon",
     "tier": 3, "power": 24, "price": 480, "skills": []},
    {"id": "star_sword", "name": "星陨剑", "slot": "weapon",
     "tier": 4, "power": 34, "price": 900,
     "skills": ["sword_intent"]},
    {"id": "god_slayer", "name": "弑神刃", "slot": "weapon",
     "tier": 5, "power": 46, "price": 1700,
     "skills": ["godslayer_will"]},
    {"id": "origin_sword", "name": "太初剑", "slot": "weapon",
     "tier": 6, "power": 60, "price": 3200,
     "skills": ["primordial_qi", "heaven_break"]},
    {"id": "immortal_sword", "name": "诛仙剑", "slot": "weapon",
     "tier": 7, "power": 78, "price": 5200,
     "skills": ["flame_slash"]},
    {"id": "chaos_sword", "name": "鸿蒙剑", "slot": "weapon",
     "tier": 8, "power": 98, "price": 9000,
     "skills": ["soul_chase", "chaos_force"]},
    {"id": "creation_sword", "name": "造化神剑", "slot": "weapon",
     "tier": 9, "power": 120, "price": 16000,
     "skills": ["time_stop", "saint_dao"]},

    # ── 护甲 ──
    {"id": "cloth_armor", "name": "布衣", "slot": "armor",
     "tier": 0, "power": 3, "price": 25, "skills": []},
    {"id": "leather", "name": "皮甲", "slot": "armor",
     "tier": 1, "power": 8, "price": 80, "skills": []},
    {"id": "chainmail", "name": "锁子甲", "slot": "armor",
     "tier": 2, "power": 14, "price": 200, "skills": []},
    {"id": "black_armor", "name": "玄铁甲", "slot": "armor",
     "tier": 3, "power": 21, "price": 450,
     "skills": ["iron_body"]},
    {"id": "dragon_scale", "name": "龙鳞甲", "slot": "armor",
     "tier": 4, "power": 30, "price": 850,
     "skills": ["dragon_guard"]},
    {"id": "silk_armor", "name": "天蚕甲", "slot": "armor",
     "tier": 5, "power": 42, "price": 1600,
     "skills": ["iron_body"]},
    {"id": "immortal_armor", "name": "不朽战甲", "slot": "armor",
     "tier": 6, "power": 55, "price": 3000,
     "skills": ["immortal_aegis"]},
    {"id": "immortal_robe", "name": "仙灵甲", "slot": "armor",
     "tier": 7, "power": 72, "price": 5000,
     "skills": ["iron_guard"]},
    {"id": "chaos_armor", "name": "鸿蒙甲", "slot": "armor",
     "tier": 8, "power": 92, "price": 8800,
     "skills": ["immortal_aegis", "dragon_guard"]},
    {"id": "creation_armor", "name": "造化神甲", "slot": "armor",
     "tier": 9, "power": 115, "price": 15000,
     "skills": ["iron_body", "saint_dao"]},

    # ── 饰品 ──
    {"id": "straw_rope", "name": "草绳", "slot": "charm",
     "tier": 0, "power": 2, "price": 20, "skills": []},
    {"id": "jade", "name": "玉坠", "slot": "charm",
     "tier": 1, "power": 6, "price": 70, "skills": []},
    {"id": "mirror", "name": "护心镜", "slot": "charm",
     "tier": 2, "power": 12, "price": 180, "skills": []},
    {"id": "dragon_jade", "name": "龙纹佩", "slot": "charm",
     "tier": 3, "power": 18, "price": 400,
     "skills": ["keen_eye"]},
    {"id": "taiji_jade", "name": "太极玉", "slot": "charm",
     "tier": 4, "power": 26, "price": 750,
     "skills": ["keen_eye"]},
    {"id": "chaos_pearl", "name": "混沌珠", "slot": "charm",
     "tier": 5, "power": 38, "price": 1500,
     "skills": ["chaos_force"]},
    {"id": "samsara_jade", "name": "轮回佩", "slot": "charm",
     "tier": 6, "power": 50, "price": 2800,
     "skills": ["soul_chase"]},
    {"id": "immortal_jade", "name": "仙灵佩", "slot": "charm",
     "tier": 7, "power": 65, "price": 4800,
     "skills": ["keen_eye"]},
    {"id": "chaos_jade", "name": "鸿蒙玉", "slot": "charm",
     "tier": 8, "power": 85, "price": 8500,
     "skills": ["chaos_force", "soul_chase"]},
    {"id": "creation_jade", "name": "造化玉牒", "slot": "charm",
     "tier": 9, "power": 108, "price": 14500,
     "skills": ["time_stop", "saint_dao"]},

    # ── 坐骑 ──
    {"id": "bay_horse", "name": "枣红马", "slot": "mount",
     "tier": 0, "power": 8, "price": 150, "skills": []},
    {"id": "snow_horse", "name": "踏雪马", "slot": "mount",
     "tier": 1, "power": 20, "price": 550,
     "skills": ["wind_step"]},
    {"id": "red_hare", "name": "赤兔", "slot": "mount",
     "tier": 2, "power": 35, "price": 1400,
     "skills": ["wind_step"]},
    {"id": "qilin", "name": "麒麟", "slot": "mount",
     "tier": 3, "power": 52, "price": 3000,
     "skills": ["dragon_roar"]},
    {"id": "crane", "name": "仙鹤", "slot": "mount",
     "tier": 7, "power": 68, "price": 5000,
     "skills": ["wind_step", "soul_chase"]},
    {"id": "chaos_qilin", "name": "鸿蒙麒麟", "slot": "mount",
     "tier": 8, "power": 88, "price": 9000,
     "skills": ["dragon_roar"]},
    {"id": "creation_dragon", "name": "造化神龙", "slot": "mount",
     "tier": 9, "power": 110, "price": 15500,
     "skills": ["flame_slash", "saint_dao"]},
]


def tier_title(tier: int) -> str:
    """Name of a quality tier, clamped to the ladder."""
    tier = max(0, min(EQUIPMENT_TIER_MAX, int(tier or 0)))
    return TIER_TITLES[tier]


# 套装 — a set bonus activates as pieces of that set are equipped.
EQUIPMENT_SETS: list[dict] = [
    {"id": "xian", "name": "仙品套",
     "pieces": ["immortal_sword", "immortal_robe",
                "immortal_jade", "crane"],
     "bonuses": [
         {"pieces": 2, "effects": {"attack_pct": 0.06},
          "desc": "2 件：攻击 +6%"},
         {"pieces": 4, "effects": {"boss_damage": 0.10},
          "desc": "4 件：Boss 伤害 +10%"},
     ]},
    {"id": "chaos", "name": "鸿蒙套",
     "pieces": ["chaos_sword", "chaos_armor",
                "chaos_jade", "chaos_qilin"],
     "bonuses": [
         {"pieces": 2, "effects": {"attack_pct": 0.08,
                                   "defense_pct": 0.08},
          "desc": "2 件：攻防 +8%"},
         {"pieces": 4, "effects": {"boss_damage": 0.15},
          "desc": "4 件：Boss 伤害 +15%"},
     ]},
    {"id": "creation", "name": "造化套",
     "pieces": ["creation_sword", "creation_armor",
                "creation_jade", "creation_dragon"],
     "bonuses": [
         {"pieces": 2, "effects": {"attack_pct": 0.10,
                                   "defense_pct": 0.10},
          "desc": "2 件：攻防 +10%"},
         {"pieces": 4, "effects": {"boss_damage": 0.20,
                                   "duel_win": 0.05},
          "desc": "4 件：Boss 伤害 +20%，切磋胜率 +5%"},
     ]},
]

_SET_BY_ID = {item["id"]: item for item in EQUIPMENT_SETS}
_SET_BY_ITEM = {
    item_id: item["id"]
    for item in EQUIPMENT_SETS for item_id in item["pieces"]
}


def set_by_id(set_id: str) -> dict | None:
    return _SET_BY_ID.get(str(set_id or ""))


def set_id_for_item(item_id: str) -> str:
    return _SET_BY_ITEM.get(str(item_id or ""), "")


DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data/equipment.json"

# 强化（精炼）: every level adds a flat power bonus; cost grows linearly
# with the item price and the current level, so endgame gear is a long
# balance sink rather than a one-off purchase.
ENHANCE_MAX = 15
ENHANCE_POWER_PER_LEVEL = 2
ENHANCE_COST_RATIO = 0.10
ENHANCE_MIN_COST = 10


def enhance_cost(item: dict, level: int) -> int:
    """Balance cost to go from *level* to *level + 1*."""
    level = max(0, int(level))
    if level >= ENHANCE_MAX:
        return 0
    price = max(0, int(item.get("price", 0) or 0))
    return max(ENHANCE_MIN_COST,
               int(round(price * ENHANCE_COST_RATIO * (level + 1))))


def effective_power(item: dict, level: int) -> int:
    """Power after enhancement."""
    level = max(0, min(ENHANCE_MAX, int(level or 0)))
    return int(item.get("power", 0) or 0) + \
        level * ENHANCE_POWER_PER_LEVEL


def effective_stats(item: dict, level: int) -> tuple[int, int]:
    """Split one item's effective power into ``(attack, defense)``.

    Weapons are pure attack, armor pure defense, charms lean attack and
    mounts lean defense.  The sum always equals :func:`effective_power`.
    """
    total = effective_power(item, level)
    slot = str(item.get("slot") or "")
    if slot == "weapon":
        return total, 0
    if slot == "armor":
        return 0, total
    if slot == "mount":
        attack = total // 2
        return attack, total - attack
    attack = (total + 1) // 2
    return attack, total - attack


def resolve_path(path: str | Path | None) -> Path:
    if not path:
        return DEFAULT_CATALOG_PATH
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def validate_catalog(data: dict) -> dict:
    """Validate a catalog dict and return a normalized copy."""
    if not isinstance(data, dict):
        raise ValueError("装备目录必须是对象")

    raw_slots = data.get("slots") or DEFAULT_SLOTS
    slots: list[dict] = []
    seen_slots: set[str] = set()
    for slot in raw_slots:
        if not isinstance(slot, dict):
            raise ValueError("槽位条目必须是对象")
        slot_id = str(slot.get("id") or "").strip()
        name = str(slot.get("name") or "").strip()
        if not slot_id or not name:
            raise ValueError("槽位必须有 id 和 name")
        if slot_id in seen_slots:
            raise ValueError(f"槽位 id 重复: {slot_id}")
        seen_slots.add(slot_id)
        slots.append({"id": slot_id, "name": name})

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("装备列表不能为空")

    items: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("装备条目必须是对象")
        item_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        slot = str(item.get("slot") or "").strip()
        try:
            power = int(item.get("power"))
            price = int(item.get("price"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"装备 {name or item_id} 的武力/价格无效") from exc
        try:
            tier = int(item.get("tier", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"装备 {name or item_id} 的品阶无效") from exc

        if not item_id or not name:
            raise ValueError("装备必须有 id 和 name")
        if item_id in seen_ids or name in seen_names:
            raise ValueError(f"装备 id 或名称重复: {name}")
        if slot not in seen_slots:
            raise ValueError(f"装备 {name} 的槽位不存在: {slot}")
        if power < 0:
            raise ValueError(f"装备 {name} 的武力不能为负")
        if price <= 0:
            raise ValueError(f"装备 {name} 的价格必须大于 0")
        if not (0 <= tier <= EQUIPMENT_TIER_MAX):
            raise ValueError(
                f"装备 {name} 的品阶必须在 0~{EQUIPMENT_TIER_MAX} 之间"
            )

        raw_skills = item.get("skills") or []
        if not isinstance(raw_skills, list):
            raise ValueError(f"装备 {name} 的技能必须是列表")
        skills: list[str] = []
        for skill_id in raw_skills:
            skill_id = str(skill_id or "").strip()
            if not skill_id:
                continue
            definition = skill_by_id(skill_id)
            if definition is None:
                raise ValueError(
                    f"装备 {name} 的技能不存在: {skill_id}")
            if skill_id not in skills:
                skills.append(skill_id)

        set_id = str(item.get("set_id") or set_id_for_item(item_id) or "")
        if set_id and set_by_id(set_id) is None:
            raise ValueError(f"装备 {name} 的套装不存在: {set_id}")
        set_def = set_by_id(set_id) if set_id else None

        seen_ids.add(item_id)
        seen_names.add(name)
        items.append({
            "id": item_id, "name": name, "slot": slot,
            "tier": tier, "tier_title": tier_title(tier),
            "power": power, "price": price,
            "skills": skills,
            "skill_names": [
                skill_by_id(sid)["name"] for sid in skills
            ],
            "set_id": set_id,
            "set_name": set_def["name"] if set_def else "",
        })

    # Same-slot ladders must be monotonic in price → power.
    for slot in slots:
        slot_items = sorted(
            (i for i in items if i["slot"] == slot["id"]),
            key=lambda i: i["price"],
        )
        last_power = -1
        for item in slot_items:
            if item["power"] < last_power:
                raise ValueError(
                    f"槽位 {slot['name']} 中价格更高的装备武力不能更低"
                )
            last_power = item["power"]

    return {"slots": slots, "items": items}


class EquipmentCatalog:
    """Load/save the equipment catalog with mtime caching."""

    def __init__(self, path: str | Path | None = None):
        self._path = resolve_path(path)
        self._cache: dict | None = None
        self._mtime: float = -1.0

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        if self._cache is not None:
            try:
                if self._path.stat().st_mtime == self._mtime:
                    return self._cache
            except OSError:
                pass

        if not self._path.exists():
            self._cache = validate_catalog(
                {"slots": DEFAULT_SLOTS, "items": DEFAULT_ITEMS}
            )
            self._mtime = -1.0
            return self._cache

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._cache = validate_catalog(data)
            self._mtime = self._path.stat().st_mtime
        except Exception as exc:  # noqa: BLE001 - config files are untrusted
            logger.warning(
                "Invalid equipment config at %s (%s); using defaults",
                self._path, exc,
            )
            self._cache = validate_catalog(
                {"slots": DEFAULT_SLOTS, "items": DEFAULT_ITEMS}
            )
            self._mtime = -1.0
        return self._cache

    def save(self, data: dict) -> dict:
        cleaned = validate_catalog(data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._cache = cleaned
        self._mtime = self._path.stat().st_mtime
        logger.info("Saved equipment catalog to %s", self._path)
        return cleaned

    # ── Lookup ─────────────────────────────────────────────────────

    def items(self) -> list[dict]:
        return list(self.load()["items"])

    def slots(self) -> list[dict]:
        return list(self.load()["slots"])

    def find(self, key: str) -> dict | None:
        """Find an item by id or display name (case-insensitive)."""
        key = (key or "").strip()
        if not key:
            return None
        for item in self.load()["items"]:
            if item["id"] == key or item["name"] == key:
                return item
        lowered = key.lower()
        for item in self.load()["items"]:
            if item["name"].lower() == lowered or item["id"].lower() == lowered:
                return item
        return None

    def by_id(self, item_id: str) -> dict | None:
        for item in self.load()["items"]:
            if item["id"] == item_id:
                return item
        return None

    def slot_name(self, slot_id: str) -> str:
        for slot in self.load()["slots"]:
            if slot["id"] == slot_id:
                return slot["name"]
        return slot_id

    def same_slot_ids(self, item: dict) -> list[str]:
        return [
            i["id"] for i in self.load()["items"]
            if i["slot"] == item["slot"]
        ]
