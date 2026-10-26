"""境界 (realm) progression — the post-Lv12 endgame ladder.

A member who has reached the maximum level can spend balance to break
through to the next realm.  Realms are permanent and each one adds a flat
power bonus, so they keep feeding duels, the boss and the power board.
"""

from __future__ import annotations

REALM_TITLES: list[str] = [
    "凡人", "炼气", "筑基", "金丹", "元婴",
    "化神", "炼虚", "合体", "大乘", "渡劫",
]

REALM_MAX = len(REALM_TITLES) - 1
REALM_POWER_PER_LEVEL = 10
REALM_BASE_COST = 3000


def realm_title(realm: int) -> str:
    realm = max(0, min(REALM_MAX, int(realm or 0)))
    return REALM_TITLES[realm]


def realm_cost(realm: int) -> int:
    """Balance needed to break through from *realm* to the next one."""
    realm = max(0, min(REALM_MAX, int(realm or 0)))
    if realm >= REALM_MAX:
        return 0
    return REALM_BASE_COST * (realm + 1)


def realm_power_bonus(realm: int) -> int:
    realm = max(0, min(REALM_MAX, int(realm or 0)))
    return realm * REALM_POWER_PER_LEVEL


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
