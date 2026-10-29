"""Equipment catalog (``data/equipment.json``).

Mirrors the lots/nicknames config pattern: a JSON file with built-in
defaults, validated on every load, editable from the Web UI.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DEFAULT_SLOTS: list[dict] = [
    {"id": "weapon", "name": "武器"},
    {"id": "armor", "name": "护甲"},
    {"id": "charm", "name": "饰品"},
    {"id": "mount", "name": "坐骑"},
]

DEFAULT_ITEMS: list[dict] = [
    {"id": "wood_sword", "name": "木剑", "slot": "weapon", "power": 4, "price": 30},
    {"id": "iron_sword", "name": "铁剑", "slot": "weapon", "power": 9, "price": 90},
    {"id": "black_blade", "name": "玄铁刀", "slot": "weapon", "power": 16, "price": 220},
    {"id": "dragon_sword", "name": "龙纹剑", "slot": "weapon", "power": 24, "price": 480},
    {"id": "star_sword", "name": "星陨剑", "slot": "weapon", "power": 34, "price": 900},
    {"id": "god_slayer", "name": "弑神刃", "slot": "weapon", "power": 46, "price": 1700},
    {"id": "origin_sword", "name": "太初剑", "slot": "weapon", "power": 60, "price": 3200},

    {"id": "cloth_armor", "name": "布衣", "slot": "armor", "power": 3, "price": 25},
    {"id": "leather", "name": "皮甲", "slot": "armor", "power": 8, "price": 80},
    {"id": "chainmail", "name": "锁子甲", "slot": "armor", "power": 14, "price": 200},
    {"id": "black_armor", "name": "玄铁甲", "slot": "armor", "power": 21, "price": 450},
    {"id": "dragon_scale", "name": "龙鳞甲", "slot": "armor", "power": 30, "price": 850},
    {"id": "silk_armor", "name": "天蚕甲", "slot": "armor", "power": 42, "price": 1600},
    {"id": "immortal_armor", "name": "不朽战甲", "slot": "armor", "power": 55, "price": 3000},

    {"id": "straw_rope", "name": "草绳", "slot": "charm", "power": 2, "price": 20},
    {"id": "jade", "name": "玉坠", "slot": "charm", "power": 6, "price": 70},
    {"id": "mirror", "name": "护心镜", "slot": "charm", "power": 12, "price": 180},
    {"id": "dragon_jade", "name": "龙纹佩", "slot": "charm", "power": 18, "price": 400},
    {"id": "taiji_jade", "name": "太极玉", "slot": "charm", "power": 26, "price": 750},
    {"id": "chaos_pearl", "name": "混沌珠", "slot": "charm", "power": 38, "price": 1500},
    {"id": "samsara_jade", "name": "轮回佩", "slot": "charm", "power": 50, "price": 2800},

    {"id": "bay_horse", "name": "枣红马", "slot": "mount", "power": 8, "price": 150},
    {"id": "snow_horse", "name": "踏雪马", "slot": "mount", "power": 20, "price": 550},
    {"id": "red_hare", "name": "赤兔", "slot": "mount", "power": 35, "price": 1400},
    {"id": "qilin", "name": "麒麟", "slot": "mount", "power": 52, "price": 3000},
]

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

        seen_ids.add(item_id)
        seen_names.add(name)
        items.append({
            "id": item_id, "name": name, "slot": slot,
            "power": power, "price": price,
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
