"""功法 (cultivation techniques) — passive bonuses + meditation progress.

Every learned technique contributes its passive effects.  Meditation
progresses the member's *current* technique; when progress is full the
member can attempt a breakthrough.  A failed breakthrough halves the
progress; five consecutive failures at one layer drop the technique one
layer.

Effects are expressed "per layer" and summed across every learned
technique, so higher layers and more techniques both make a member
stronger.
"""

from __future__ import annotations

MAX_LAYER = 9
PROGRESS_PER_LAYER = 100          # layer L → L+1 needs 100 * L progress
LEARN_COST = 500
BREAKTHROUGH_BASE = 0.85
BREAKTHROUGH_STEP = 0.07
BREAKTHROUGH_MIN = 0.30
FAILURES_TO_DROP = 5
MEDITATION_POINTS_PER_MIN = 2
MEDITATION_PROGRESS_PER_MIN = 1
MEDITATION_MAX_MINUTES = 60

TECHNIQUES: list[dict] = [
    {"id": "tuna", "name": "吐纳诀", "feature": "修炼",
     "desc": "打坐时修炼值与功法进度获取提高",
     "effects": {"attack": 2, "defense": 1,
                 "meditation_points": 0.05, "meditation_progress": 0.05}},
    {"id": "zhuti", "name": "铸体诀", "feature": "装备",
     "desc": "淬炼肉身，大幅提高防御",
     "effects": {"defense": 4}},
    {"id": "zhanyi", "name": "战意诀", "feature": "切磋",
     "desc": "提高攻击与切磋胜率",
     "effects": {"attack": 3, "duel_win": 0.01}},
    {"id": "chuigou", "name": "垂钓心法", "feature": "钓鱼",
     "desc": "提高钓鱼稀有度与渔获价值",
     "effects": {"fishing_rare": 0.03, "fishing_value": 0.02}},
    {"id": "qiyun", "name": "气运诀", "feature": "抽奖 / 抽签",
     "desc": "提高抽奖高价值奖励概率与钓鱼稀有度",
     "effects": {"lottery_luck": 0.03, "fishing_rare": 0.01}},
    {"id": "pojun", "name": "破军诀", "feature": "群 Boss",
     "desc": "提高攻击与对 Boss 的伤害",
     "effects": {"attack": 2, "boss_damage": 0.04}},
    {"id": "yangsheng", "name": "养生诀", "feature": "签到 / 打坐",
     "desc": "提高打坐进度与防御",
     "effects": {"defense": 2, "meditation_progress": 0.08}},
    {"id": "guiyuan", "name": "归元诀", "feature": "境界",
     "desc": "均衡提高攻击、防御与打坐收益",
     "effects": {"attack": 1, "defense": 1, "meditation_points": 0.03}},
]

_BY_ID = {item["id"]: item for item in TECHNIQUES}
_BY_NAME = {item["name"]: item for item in TECHNIQUES}


def technique_by_id(technique_id: str) -> dict | None:
    return _BY_ID.get(str(technique_id or ""))


def technique_by_name(name: str) -> dict | None:
    return _BY_NAME.get(str(name or "").strip())


def required_progress(layer: int) -> int:
    """Progress needed to attempt a breakthrough from *layer*."""
    layer = max(1, min(MAX_LAYER, int(layer or 1)))
    if layer >= MAX_LAYER:
        return 0
    return PROGRESS_PER_LAYER * layer


def breakthrough_chance(layer: int) -> float:
    layer = max(1, min(MAX_LAYER, int(layer or 1)))
    return max(BREAKTHROUGH_MIN,
               BREAKTHROUGH_BASE - BREAKTHROUGH_STEP * (layer - 1))


def aggregate_effects(rows: list[dict]) -> dict[str, float]:
    """Sum every learned technique's per-layer effects."""
    totals: dict[str, float] = {}
    for row in rows:
        definition = technique_by_id(row.get("technique_id", ""))
        if definition is None:
            continue
        layer = max(1, int(row.get("layer", 1) or 1))
        for key, per_layer in definition["effects"].items():
            totals[key] = totals.get(key, 0.0) + float(per_layer) * layer
    return totals


def effects_for(store, chat_id: str, sender_id: str) -> dict[str, float]:
    """Aggregate the passive effects of every technique this member knows."""
    return aggregate_effects(store.techniques_of(chat_id, sender_id))


def format_effects(effects: dict[str, float], layer: int = 1) -> str:
    """Human-readable effect summary for one layer/multiplier."""
    parts: list[str] = []
    labels = {
        "attack": "攻击",
        "defense": "防御",
        "duel_win": "切磋胜率",
        "fishing_rare": "钓鱼稀有",
        "fishing_value": "渔获价值",
        "lottery_luck": "抽奖气运",
        "boss_damage": "Boss 伤害",
        "meditation_points": "打坐修炼值",
        "meditation_progress": "打坐进度",
    }
    percent = {
        "duel_win", "fishing_rare", "fishing_value",
        "lottery_luck", "boss_damage",
        "meditation_points", "meditation_progress",
    }
    for key, per_layer in effects.items():
        value = float(per_layer) * max(1, layer)
        label = labels.get(key, key)
        if key in percent:
            parts.append(f"{label} +{round(value * 100)}%")
        else:
            parts.append(f"{label} +{int(round(value))}")
    return " · ".join(parts)
