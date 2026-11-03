"""境界 (realm) progression — the post-Lv12 endgame ladder.

A member who has reached the maximum level can spend balance to break
through to the next realm.  Realms are permanent and each one adds a flat
power bonus, so they keep feeding duels, the boss and the power board.
"""

from __future__ import annotations

REALM_TITLES: list[str] = [
    # 原有十境（0~9）
    "凡人", "炼气", "筑基", "金丹", "元婴",
    "化神", "炼虚", "合体", "大乘", "渡劫",
    # 飞升之后的十境（10~19）
    "散仙", "真仙", "玄仙", "金仙", "太乙",
    "大罗", "准圣", "圣人", "天道", "鸿蒙",
]

# Realm 9 (渡劫) used to be the ceiling.  Realms beyond it cost more and
# grant a bigger flat bonus, so the extended ladder is a real endgame goal.
LEGACY_REALM_MAX = 9
REALM_MAX = len(REALM_TITLES) - 1
REALM_POWER_PER_LEVEL = 10
HIGH_REALM_POWER_PER_LEVEL = 20
REALM_BASE_COST = 300
HIGH_REALM_COST_GROWTH = 1.35


def realm_title(realm: int) -> str:
    realm = max(0, min(REALM_MAX, int(realm or 0)))
    return REALM_TITLES[realm]


def realm_cost(realm: int) -> int:
    """Cumulative points needed to break through from *realm*.

    The original ladder grows linearly; every realm past 渡劫 grows by a
    further 35%, making 鸿蒙 a long-term target rather than a quick hop.
    """
    realm = max(0, min(REALM_MAX, int(realm or 0)))
    if realm >= REALM_MAX:
        return 0
    base = REALM_BASE_COST * (realm + 1)
    if realm < LEGACY_REALM_MAX:
        return base
    return int(round(base * (HIGH_REALM_COST_GROWTH **
                             (realm - LEGACY_REALM_MAX))))


def realm_power_bonus(realm: int) -> int:
    realm = max(0, min(REALM_MAX, int(realm or 0)))
    if realm <= LEGACY_REALM_MAX:
        return realm * REALM_POWER_PER_LEVEL
    return (LEGACY_REALM_MAX * REALM_POWER_PER_LEVEL
            + (realm - LEGACY_REALM_MAX) * HIGH_REALM_POWER_PER_LEVEL)


def realm_info(realm: int) -> dict:
    """Render one realm row with its next step."""
    realm = max(0, min(REALM_MAX, int(realm or 0)))
    nxt = realm + 1 if realm < REALM_MAX else None
    return {
        "realm": realm,
        "title": realm_title(realm),
        "power_bonus": realm_power_bonus(realm),
        "next": nxt,
        "next_title": realm_title(nxt) if nxt is not None else "",
        "next_cost": realm_cost(realm),
    }
