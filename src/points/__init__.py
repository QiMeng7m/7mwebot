"""Activity points, equipment, duels and fishing.

Public entry points are wired into :class:`src.router.MessageRouter`:

* :class:`src.points.engine.PointEngine` — awards points for group messages
* :class:`src.points.handler.PointHandler` — 群内纯文本指令

Design: ``docs/ACTIVITY_POINTS_DESIGN.md``,
``docs/DUEL_EQUIPMENT_DESIGN.md`` and ``docs/FISHING_DESIGN.md``.
"""

from .models import (
    Command, DuelResult, FishCatch, FishingResult, MemberStats,
    PointAward, PowerBreakdown,
)
from .store import PointStore

__all__ = [
    "Command",
    "DuelResult",
    "FishCatch",
    "FishingResult",
    "MemberStats",
    "PointAward",
    "PointStore",
    "PowerBreakdown",
]
