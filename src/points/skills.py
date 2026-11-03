"""装备技能 — passive and active skills granted by equipped gear.

A skill is attached to one or more equipment catalog entries.  Only the
*equipped* item grants its skills:

* passive skills apply their effects automatically (attack/defense
  percentage, duel win rate, boss damage);
* active skills must be cast with 「施放 技能名」 and then last for a short
  duration before going on cooldown.  Active buffs are consumed by the
  next duels / boss hits while they are active.

Skill effects use fractions: ``attack_pct: 0.08`` means +8% attack.
Enhancement scales passive effects — each +1 on the source item adds 3%
to the skill's effect.
"""

from __future__ import annotations

SKILL_ENHANCE_SCALE = 0.03
SKILL_LEVEL_SCALE = 0.15
SKILL_MAX_LEVEL = 5

# ── Definitions ──────────────────────────────────────────────────────

SKILLS: list[dict] = [
    # Passive — always active while the source item is equipped.
    {"id": "sword_intent", "name": "剑意", "type": "passive",
     "desc": "剑随心动，提高攻击",
     "effects": {"attack_pct": 0.05}},
    {"id": "iron_body", "name": "铁骨", "type": "passive",
     "desc": "钢筋铁骨，提高防御",
     "effects": {"defense_pct": 0.05}},
    {"id": "keen_eye", "name": "明察", "type": "passive",
     "desc": "洞察破绽，提高切磋胜率",
     "effects": {"duel_win": 0.02}},
    {"id": "wind_step", "name": "追风", "type": "passive",
     "desc": "身法轻灵，兼顾攻防",
     "effects": {"attack_pct": 0.03, "defense_pct": 0.03}},
    {"id": "dragon_guard", "name": "龙鳞护体", "type": "passive",
     "desc": "龙鳞覆身，大幅提高防御",
     "effects": {"defense_pct": 0.08}},
    {"id": "chaos_force", "name": "混沌之力", "type": "passive",
     "desc": "混沌之力加身，提高攻击与 Boss 伤害",
     "effects": {"attack_pct": 0.08, "boss_damage": 0.08}},
    {"id": "godslayer_will", "name": "弑神意志", "type": "passive",
     "desc": "弑神者意志，显著提高攻击与 Boss 伤害",
     "effects": {"attack_pct": 0.10, "boss_damage": 0.10}},
    {"id": "primordial_qi", "name": "太初之气", "type": "passive",
     "desc": "太初之气流转，攻防兼修",
     "effects": {"attack_pct": 0.08, "defense_pct": 0.08}},
    {"id": "immortal_aegis", "name": "仙灵护体", "type": "passive",
     "desc": "仙灵护体，大幅提高防御",
     "effects": {"defense_pct": 0.12}},
    {"id": "saint_dao", "name": "圣道加身", "type": "passive",
     "desc": "圣道加身，攻防俱增",
     "effects": {"attack_pct": 0.15, "defense_pct": 0.15}},

    # Active — cast with 施放, lasts ``duration`` seconds.
    {"id": "flame_slash", "name": "烈焰斩", "type": "active",
     "desc": "下一次 Boss 伤害大幅提高",
     "effects": {"boss_damage": 0.50},
     "cooldown": 1800, "duration": 600},
    {"id": "iron_guard", "name": "金刚护体", "type": "active",
     "desc": "短时间内防御大幅提高",
     "effects": {"defense_pct": 0.40},
     "cooldown": 1800, "duration": 600},
    {"id": "heaven_break", "name": "天崩地裂", "type": "active",
     "desc": "短时间内攻击大幅提高",
     "effects": {"attack_pct": 0.50},
     "cooldown": 3600, "duration": 600},
    {"id": "soul_chase", "name": "追魂击", "type": "active",
     "desc": "短时间内切磋胜率提高",
     "effects": {"duel_win": 0.10},
     "cooldown": 1800, "duration": 600},
    {"id": "dragon_roar", "name": "龙吟九天", "type": "active",
     "desc": "短时间内攻击与 Boss 伤害提高",
     "effects": {"attack_pct": 0.30, "boss_damage": 0.30},
     "cooldown": 2700, "duration": 600},
    {"id": "time_stop", "name": "时停术", "type": "active",
     "desc": "短时间内攻防同时大幅提高",
     "effects": {"attack_pct": 0.25, "defense_pct": 0.25},
     "cooldown": 3600, "duration": 600},

    # 丹药 — same timed-buff machinery, consumed from the alchemy bag.
    {"id": "pill_qi", "name": "聚气丹", "type": "active", "pill": True,
     "desc": "服用后短时间攻击提高",
     "effects": {"attack_pct": 0.20},
     "cooldown": 600, "duration": 600},
    {"id": "pill_guard", "name": "护体丹", "type": "active", "pill": True,
     "desc": "服用后短时间防御提高",
     "effects": {"defense_pct": 0.25},
     "cooldown": 600, "duration": 600},
    {"id": "pill_break", "name": "破军丹", "type": "active", "pill": True,
     "desc": "服用后短时间 Boss 伤害提高",
     "effects": {"boss_damage": 0.25},
     "cooldown": 600, "duration": 600},
]

_BY_ID = {item["id"]: item for item in SKILLS}
_BY_NAME = {item["name"]: item for item in SKILLS}

EFFECT_LABELS = {
    "attack_pct": "攻击",
    "defense_pct": "防御",
    "duel_win": "切磋胜率",
    "boss_damage": "Boss 伤害",
}


def skill_by_id(skill_id: str) -> dict | None:
    return _BY_ID.get(str(skill_id or ""))


def skill_by_name(name: str) -> dict | None:
    return _BY_NAME.get(str(name or "").strip())


def passive_skills() -> list[dict]:
    return [item for item in SKILLS if item["type"] == "passive"]


def active_skills() -> list[dict]:
    return [item for item in SKILLS if item["type"] == "active"]


def format_effects(effects: dict, scale: float = 1.0) -> str:
    """Render ``{"attack_pct": 0.05}`` as ``攻击 +5%``."""
    parts: list[str] = []
    for key, value in effects.items():
        label = EFFECT_LABELS.get(key, key)
        parts.append(f"{label} +{round(float(value) * scale * 100)}%")
    return " · ".join(parts)


# ── Loadout ──────────────────────────────────────────────────────────

def loadout_skills(store, catalog, chat_id: str,
                   sender_id: str) -> list[dict]:
    """Every skill granted by the member's *equipped* items.

    Duplicate skills from two equipped items are merged by keeping the
    stronger (higher enhancement) source.  Skill level multiplies the
    effect on top of enhancement scaling.
    """
    levels = _skill_levels(store, chat_id, sender_id)
    best: dict[str, dict] = {}
    for row in store.owned_items(chat_id, sender_id):
        if not row.get("equipped"):
            continue
        item = catalog.by_id(str(row.get("item_id") or ""))
        if item is None:
            continue
        level = int(row.get("enhance_level", 0) or 0)
        for skill_id in item.get("skills") or ():
            definition = skill_by_id(skill_id)
            if definition is None:
                continue
            skill_level = int(levels.get(skill_id, 1) or 1)
            entry = {
                **definition,
                "item_id": item["id"],
                "item_name": item["name"],
                "enhance_level": level,
                "skill_level": skill_level,
                "scale": (1.0 + SKILL_ENHANCE_SCALE * level)
                * (1.0 + SKILL_LEVEL_SCALE * (skill_level - 1)),
            }
            current = best.get(definition["id"])
            if current is None or entry["enhance_level"] > \
                    current["enhance_level"]:
                best[definition["id"]] = entry
    return list(best.values())


def _skill_levels(store, chat_id: str, sender_id: str) -> dict[str, int]:
    fn = getattr(store, "skill_levels", None)
    if fn is None:
        return {}
    try:
        return fn(chat_id, sender_id)
    except Exception:  # noqa: BLE001 - levels are optional
        return {}


def skills_of_item(item: dict) -> list[dict]:
    """Skill definitions attached to one catalog item."""
    return [
        definition for definition in (
            skill_by_id(skill_id) for skill_id in item.get("skills") or ()
        ) if definition is not None
    ]


def passive_effects(store, catalog, chat_id: str,
                    sender_id: str) -> dict[str, float]:
    """Aggregate passive skill effects (scaled by source enhancement)."""
    totals: dict[str, float] = {}
    for skill in loadout_skills(store, catalog, chat_id, sender_id):
        if skill["type"] != "passive":
            continue
        for key, value in skill["effects"].items():
            totals[key] = totals.get(key, 0.0) + \
                float(value) * float(skill["scale"])
    return totals


def active_skill_rows(store, chat_id: str, sender_id: str,
                      now_ts: int) -> list[dict]:
    """Cast rows whose buff is still running, joined with definitions."""
    rows = store.skill_casts(chat_id, sender_id)
    active: list[dict] = []
    for row in rows:
        if int(row.get("expires_at", 0) or 0) <= int(now_ts):
            continue
        definition = skill_by_id(str(row.get("skill_id") or ""))
        if definition is None:
            continue
        active.append({**definition, **row})
    return active


def active_effects(store, chat_id: str, sender_id: str,
                   now_ts: int) -> dict[str, float]:
    """Aggregate every currently running active buff."""
    levels = _skill_levels(store, chat_id, sender_id)
    totals: dict[str, float] = {}
    for skill in active_skill_rows(store, chat_id, sender_id, now_ts):
        level = int(levels.get(skill["id"], 1) or 1)
        scale = 1.0 + SKILL_LEVEL_SCALE * (level - 1)
        for key, value in skill["effects"].items():
            totals[key] = totals.get(key, 0.0) + float(value) * scale
    return totals


def level_scale(level: int) -> float:
    """Effect multiplier for a skill level (1..5)."""
    level = max(1, min(SKILL_MAX_LEVEL, int(level or 1)))
    return 1.0 + SKILL_LEVEL_SCALE * (level - 1)
