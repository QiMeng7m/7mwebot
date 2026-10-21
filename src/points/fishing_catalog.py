"""Fishing catalog (``data/fishing.json``).

Same pattern as :mod:`src.points.equipment`: built-in defaults, validated
on every load, editable from the Web UI.  The catalog holds three lists:

* ``rods``    — permanent gear, one equipped at a time
* ``baits``   — consumable stacks, auto-consumed cheapest first
* ``species`` — what can be caught, weighted by rarity
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

RARITIES = ("junk", "common", "rare", "epic", "legendary")

RARITY_LABELS = {
    "junk": "垃圾",
    "common": "普通",
    "rare": "稀有",
    "epic": "史诗",
    "legendary": "传说",
}

# How strongly fishing gear improves the odds of rarer species.  Junk
# and common fish are not boosted at all, otherwise better rods would
# only produce more junk.
RARITY_BONUS_MULT = {
    "junk": 0.0,
    "common": 0.0,
    "rare": 1.0,
    "epic": 1.5,
    "legendary": 2.0,
}

# Suspense timing: the rarer the catch, the longer the bot "waits" before
# posting the result.  Misses and junk come back fast; a legendary fish
# takes a full minute.  Values are seconds, editable in fishing.json.
DEFAULT_DELAYS: dict[str, int] = {
    "miss": 5,
    "junk": 5,
    "common": 5,
    "rare": 15,
    "epic": 30,
    "legendary": 60,
}

DEFAULT_RODS: list[dict] = [
    {"id": "bamboo", "name": "竹竿", "price": 0,
     "rare_bonus": 0.0, "value_bonus": 0.0, "miss_reduction": 0.0,
     "default": True},
    {"id": "fiberglass", "name": "玻璃钢竿", "price": 120,
     "rare_bonus": 0.15, "value_bonus": 0.10, "miss_reduction": 0.02},
    {"id": "carbon", "name": "碳素竿", "price": 300,
     "rare_bonus": 0.30, "value_bonus": 0.20, "miss_reduction": 0.04},
    {"id": "dragon", "name": "龙纹竿", "price": 700,
     "rare_bonus": 0.50, "value_bonus": 0.35, "miss_reduction": 0.06},
    {"id": "abyss", "name": "龙渊竿", "price": 1500,
     "rare_bonus": 0.80, "value_bonus": 0.50, "miss_reduction": 0.08},
]

DEFAULT_BAITS: list[dict] = [
    {"id": "worm", "name": "蚯蚓", "price": 2,
     "rare_bonus": 0.10, "value_bonus": 0.0},
    {"id": "redworm", "name": "红虫", "price": 5,
     "rare_bonus": 0.25, "value_bonus": 0.10},
    {"id": "mystic", "name": "神秘饵", "price": 15,
     "rare_bonus": 0.60, "value_bonus": 0.20},
]

DEFAULT_SPECIES: list[dict] = [
    # 垃圾 — 白跑一趟，但仍有钓鱼记录
    {"id": "boot", "name": "破靴子", "rarity": "junk", "weight": 4,
     "min_weight_g": 300, "max_weight_g": 900, "base_value": 0},
    {"id": "weed", "name": "水草", "rarity": "junk", "weight": 6,
     "min_weight_g": 50, "max_weight_g": 500, "base_value": 0},
    {"id": "can", "name": "易拉罐", "rarity": "junk", "weight": 4,
     "min_weight_g": 20, "max_weight_g": 100, "base_value": 0},

    # 普通
    {"id": "crucian", "name": "鲫鱼", "rarity": "common", "weight": 22,
     "min_weight_g": 80, "max_weight_g": 400, "base_value": 4},
    {"id": "carp", "name": "鲤鱼", "rarity": "common", "weight": 16,
     "min_weight_g": 300, "max_weight_g": 1500, "base_value": 7},
    {"id": "grass_carp", "name": "草鱼", "rarity": "common", "weight": 12,
     "min_weight_g": 500, "max_weight_g": 2500, "base_value": 9},
    {"id": "chub", "name": "鲢鱼", "rarity": "common", "weight": 14,
     "min_weight_g": 400, "max_weight_g": 2000, "base_value": 6},
    {"id": "loach", "name": "泥鳅", "rarity": "common", "weight": 10,
     "min_weight_g": 20, "max_weight_g": 120, "base_value": 3},
    {"id": "crayfish", "name": "小龙虾", "rarity": "common", "weight": 8,
     "min_weight_g": 30, "max_weight_g": 150, "base_value": 5},

    # 稀有
    {"id": "bass", "name": "鲈鱼", "rarity": "rare", "weight": 10,
     "min_weight_g": 400, "max_weight_g": 1800, "base_value": 14},
    {"id": "mandarin", "name": "桂鱼", "rarity": "rare", "weight": 6,
     "min_weight_g": 500, "max_weight_g": 2000, "base_value": 18},
    {"id": "eel", "name": "黄鳝", "rarity": "rare", "weight": 7,
     "min_weight_g": 100, "max_weight_g": 600, "base_value": 12},
    {"id": "softshell", "name": "甲鱼", "rarity": "rare", "weight": 4,
     "min_weight_g": 400, "max_weight_g": 1500, "base_value": 22},

    # 史诗
    {"id": "sturgeon", "name": "中华鲟", "rarity": "epic", "weight": 2.5,
     "min_weight_g": 2000, "max_weight_g": 8000, "base_value": 45},
    {"id": "crab", "name": "大闸蟹", "rarity": "epic", "weight": 3,
     "min_weight_g": 150, "max_weight_g": 500, "base_value": 35},
    {"id": "puffer", "name": "河豚", "rarity": "epic", "weight": 2,
     "min_weight_g": 300, "max_weight_g": 1200, "base_value": 40},

    # 传说
    {"id": "koi", "name": "锦鲤", "rarity": "legendary", "weight": 0.8,
     "min_weight_g": 500, "max_weight_g": 3000, "base_value": 88},
    {"id": "arowana", "name": "龙鱼", "rarity": "legendary", "weight": 0.35,
     "min_weight_g": 1000, "max_weight_g": 5000, "base_value": 120},
    {"id": "golden_carp", "name": "黄金鲤鱼", "rarity": "legendary",
     "weight": 0.15, "min_weight_g": 800, "max_weight_g": 4000,
     "base_value": 200},
]

DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data/fishing.json"


def resolve_path(path: str | Path | None) -> Path:
    if not path:
        return DEFAULT_CATALOG_PATH
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def default_catalog() -> dict:
    return {
        "rods": [dict(item) for item in DEFAULT_RODS],
        "baits": [dict(item) for item in DEFAULT_BAITS],
        "species": [dict(item) for item in DEFAULT_SPECIES],
        "delays": dict(DEFAULT_DELAYS),
    }


def _validate_gear(entries, kind: str) -> list[dict]:
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{kind}列表不能为空")

    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{kind}条目必须是对象")
        item_id = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not item_id or not name:
            raise ValueError(f"{kind}必须有 id 和 name")
        if item_id in seen_ids or name in seen_names:
            raise ValueError(f"{kind} id 或名称重复: {name}")
        seen_ids.add(item_id)
        seen_names.add(name)

        try:
            price = int(entry.get("price"))
            rare_bonus = float(entry.get("rare_bonus", 0) or 0)
            value_bonus = float(entry.get("value_bonus", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{kind} {name} 的数值无效") from exc

        if price < 0:
            raise ValueError(f"{kind} {name} 的价格不能为负")
        if rare_bonus < 0 or value_bonus < 0:
            raise ValueError(f"{kind} {name} 的加成不能为负")

        row = {
            "id": item_id, "name": name, "price": price,
            "rare_bonus": rare_bonus, "value_bonus": value_bonus,
        }
        if kind == "鱼竿":
            try:
                miss_reduction = float(entry.get("miss_reduction", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"鱼竿 {name} 的 miss_reduction 无效") from exc
            if not (0.0 <= miss_reduction <= 0.5):
                raise ValueError(f"鱼竿 {name} 的空竿减免必须在 0~0.5")
            row["miss_reduction"] = miss_reduction
            row["default"] = bool(entry.get("default", False))
        cleaned.append(row)

    if kind == "鱼竿":
        defaults = [item for item in cleaned if item["default"]]
        if len(defaults) != 1:
            raise ValueError("鱼竿必须且只能有一根默认鱼竿")
        if defaults[0]["price"] != 0:
            raise ValueError("默认鱼竿的价格必须为 0")
    elif min(item["price"] for item in cleaned) <= 0:
        raise ValueError("鱼饵价格必须大于 0")

    return cleaned


def _validate_species(entries) -> list[dict]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("鱼种列表不能为空")

    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("鱼种条目必须是对象")
        item_id = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        rarity = str(entry.get("rarity") or "").strip()
        if not item_id or not name:
            raise ValueError("鱼种必须有 id 和 name")
        if rarity not in RARITIES:
            raise ValueError(f"鱼种 {name} 的稀有度无效: {rarity}")
        if item_id in seen_ids or name in seen_names:
            raise ValueError(f"鱼种 id 或名称重复: {name}")
        seen_ids.add(item_id)
        seen_names.add(name)

        try:
            weight = float(entry.get("weight"))
            min_g = int(entry.get("min_weight_g"))
            max_g = int(entry.get("max_weight_g"))
            base_value = int(entry.get("base_value"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"鱼种 {name} 的数值无效") from exc

        if weight <= 0:
            raise ValueError(f"鱼种 {name} 的权重必须大于 0")
        if min_g <= 0 or max_g < min_g:
            raise ValueError(f"鱼种 {name} 的重量范围无效")
        if base_value < 0:
            raise ValueError(f"鱼种 {name} 的价值不能为负")

        cleaned.append({
            "id": item_id, "name": name, "rarity": rarity,
            "weight": weight, "min_weight_g": min_g,
            "max_weight_g": max_g, "base_value": base_value,
        })
    return cleaned


def _validate_delays(data) -> dict[str, int]:
    raw = data if isinstance(data, dict) else {}
    cleaned: dict[str, int] = {}
    for key in ("miss", *RARITIES):
        try:
            value = int(raw.get(key, DEFAULT_DELAYS[key]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"钓鱼反馈时长 {key} 无效") from exc
        if not (0 <= value <= 300):
            raise ValueError(f"钓鱼反馈时长 {key} 必须在 0~300 秒")
        cleaned[key] = value
    return cleaned


def validate_catalog(data: dict) -> dict:
    """Validate a catalog dict and return a normalized copy."""
    if not isinstance(data, dict):
        raise ValueError("钓鱼目录必须是对象")
    return {
        "rods": _validate_gear(data.get("rods") or DEFAULT_RODS, "鱼竿"),
        "baits": _validate_gear(data.get("baits") or DEFAULT_BAITS, "鱼饵"),
        "species": _validate_species(
            data.get("species") or DEFAULT_SPECIES),
        "delays": _validate_delays(data.get("delays")),
    }


class FishingCatalog:
    """Load/save the fishing catalog with mtime caching."""

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
            self._cache = validate_catalog(default_catalog())
            self._mtime = -1.0
            return self._cache

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._cache = validate_catalog(data)
            self._mtime = self._path.stat().st_mtime
        except Exception as exc:  # noqa: BLE001 - config files are untrusted
            logger.warning(
                "Invalid fishing config at %s (%s); using defaults",
                self._path, exc,
            )
            self._cache = validate_catalog(default_catalog())
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
        logger.info("Saved fishing catalog to %s", self._path)
        return cleaned

    # ── Lookup ─────────────────────────────────────────────────────

    def rods(self) -> list[dict]:
        return list(self.load()["rods"])

    def baits(self) -> list[dict]:
        return list(self.load()["baits"])

    def species(self) -> list[dict]:
        return list(self.load()["species"])

    @staticmethod
    def _find(entries: list[dict], key: str) -> dict | None:
        key = (key or "").strip()
        if not key:
            return None
        for item in entries:
            if item["id"] == key or item["name"] == key:
                return item
        lowered = key.lower()
        for item in entries:
            if item["id"].lower() == lowered or \
                    item["name"].lower() == lowered:
                return item
        return None

    def find_rod(self, key: str) -> dict | None:
        return self._find(self.rods(), key)

    def by_rod_id(self, rod_id: str) -> dict | None:
        return self._find(self.rods(), rod_id)

    def find_bait(self, key: str) -> dict | None:
        return self._find(self.baits(), key)

    def by_bait_id(self, bait_id: str) -> dict | None:
        return self._find(self.baits(), bait_id)

    def by_species_id(self, species_id: str) -> dict | None:
        return self._find(self.species(), species_id)

    def default_rod(self) -> dict:
        for rod in self.rods():
            if rod.get("default"):
                return rod
        return self.rods()[0]

    def rarity_label(self, rarity: str) -> str:
        return RARITY_LABELS.get(rarity, rarity)

    def delay_for(self, rarity: str) -> int:
        """Seconds to wait before posting the result of this rarity."""
        delays = self.load().get("delays") or DEFAULT_DELAYS
        if rarity in delays:
            return int(delays[rarity])
        return int(delays.get("miss", DEFAULT_DELAYS["miss"]))
