"""Level thresholds and titles for the points system.

Stored in ``data/point_levels.json`` (editable from the Web UI).  When the
file is missing or invalid the built-in defaults are used — the file is only
created on an explicit save.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DEFAULT_LEVELS: list[dict] = [
    {"level": 0, "title": "潜水员", "min_points": 0},
    {"level": 1, "title": "冒泡", "min_points": 20},
    {"level": 2, "title": "常客", "min_points": 80},
    {"level": 3, "title": "活跃", "min_points": 200},
    {"level": 4, "title": "话痨", "min_points": 500},
    {"level": 5, "title": "群柱", "min_points": 1200},
    {"level": 6, "title": "传说", "min_points": 3000},
    # ── 进阶段位（老玩家继续提升的目标）──────────────────────────
    {"level": 7, "title": "宗师", "min_points": 6000},
    {"level": 8, "title": "传奇", "min_points": 10000},
    {"level": 9, "title": "王者", "min_points": 18000},
    {"level": 10, "title": "神话", "min_points": 30000},
    {"level": 11, "title": "超凡", "min_points": 60000},
    {"level": 12, "title": "永恒", "min_points": 120000},
]

DEFAULT_LEVELS_PATH = PROJECT_ROOT / "data/point_levels.json"


def resolve_path(path: str | Path | None) -> Path:
    """Resolve a configured path against the project root."""
    if not path:
        return DEFAULT_LEVELS_PATH
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def validate_levels(levels: list[dict]) -> list[dict]:
    """Normalize and validate a level table.

    Raises ValueError when levels are not a strictly increasing ladder
    starting at 0 points.
    """
    if not isinstance(levels, list) or not levels:
        raise ValueError("等级配置不能为空")

    cleaned: list[dict] = []
    for item in levels:
        if not isinstance(item, dict):
            raise ValueError("等级条目必须是对象")
        try:
            level = int(item["level"])
            min_points = int(item["min_points"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"等级条目字段无效: {item!r}") from exc
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError(f"Lv{level} 称号不能为空")
        if level < 0 or min_points < 0:
            raise ValueError("等级与门槛必须为非负数")
        cleaned.append({"level": level, "title": title,
                        "min_points": min_points})

    cleaned.sort(key=lambda x: (x["level"], x["min_points"]))
    seen_levels: set[int] = set()
    last_points = -1
    for item in cleaned:
        if item["level"] in seen_levels:
            raise ValueError(f"等级 Lv{item['level']} 重复")
        seen_levels.add(item["level"])
        if item["min_points"] <= last_points:
            raise ValueError("等级门槛必须严格递增")
        last_points = item["min_points"]

    if cleaned[0]["min_points"] != 0:
        raise ValueError("最低等级的修炼值门槛必须为 0")

    return cleaned


class LevelTable:
    """Loads levels with mtime-based cache; falls back to defaults."""

    def __init__(self, path: str | Path | None = None):
        self._path = resolve_path(path)
        self._cache: list[dict] | None = None
        self._mtime: float = -1.0

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[dict]:
        if self._cache is not None:
            try:
                if self._path.stat().st_mtime == self._mtime:
                    return self._cache
            except OSError:
                pass

        if not self._path.exists():
            self._cache = [dict(item) for item in DEFAULT_LEVELS]
            self._mtime = -1.0
            return self._cache

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            levels = data["levels"] if isinstance(data, dict) else data
            self._cache = validate_levels(levels)
            self._mtime = self._path.stat().st_mtime
        except Exception as exc:  # noqa: BLE001 - config files are untrusted
            logger.warning(
                "Invalid levels config at %s (%s); using defaults",
                self._path, exc,
            )
            self._cache = [dict(item) for item in DEFAULT_LEVELS]
            self._mtime = -1.0
        return self._cache

    def save(self, levels: list[dict]) -> list[dict]:
        cleaned = validate_levels(levels)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"levels": cleaned}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._cache = cleaned
        self._mtime = self._path.stat().st_mtime
        logger.info("Saved %d levels to %s", len(cleaned), self._path)
        return cleaned

    # ── Lookup helpers ─────────────────────────────────────────────

    def for_points(self, points: int) -> dict:
        """Level entry reached with *points*."""
        levels = self.load()
        current = levels[0]
        for item in levels:
            if points >= item["min_points"]:
                current = item
            else:
                break
        return current

    def next_for(self, points: int) -> dict | None:
        """The next level entry above *points*, or None at max level."""
        for item in self.load():
            if item["min_points"] > points:
                return item
        return None

    def label(self, points: int) -> str:
        item = self.for_points(points)
        return f"Lv{item['level']} {item['title']}"
