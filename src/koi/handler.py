"""群指令解析与回复格式化 for the 星盘 gameplay."""

from __future__ import annotations

import re

from src.game_utils import load_group_members, strip_invisible

from . import rules
from .service import KoiService

_WS = re.compile(r"\s+")
_REPLY_PREFIX = re.compile(r"^\s*([A-Za-z0-9_@.\-]{1,64}):\s+")
_AT_NAME = re.compile(r"@([^\s@]+)")

EXACT: dict[str, str] = {
    "求签": "sign",
    "抽签": "sign",
    "解签": "read_sign",
    "观星": "observe",
    "放生": "release",
    "转运": "reroll",
    "开光": "open_light",
    "抽符": "open_light",
    "锦鲤池": "pool_view",
    "功德": "card",
    "我的": "card",
    "我的信息": "card",
    "星盘": "profile",
    "命格": "profile",
    "八字": "bazi_card",
    "命盘": "bazi_card",
    "五维": "scores_card",
    "分支评分": "scores_card",
    "批命": "daily_fortune",
    "算命": "daily_fortune",
    "起卦": "cast_hexagram",
    "摇卦": "cast_hexagram",
    "卦谱": "hexagram_book",
    "星象": "star_event",
    "今日星象": "star_event",
    "气运榜": "leaderboard",
    "锦鲤榜": "leaderboard",
    "四象榜": "faction_board",
    "阵营榜": "faction_board",
    "签谱": "sign_book",
    "周运": "weekly",
    "领取周运": "claim_weekly",
    "群像": "group_portrait",
    "CP榜": "cp_board",
    "cp榜": "cp_board",
    "分享": "share_card",
    "签卡": "share_card",
}

def looks_like_koi_command(text: str) -> bool:
    text = strip_invisible(text)
    if not text:
        return False
    if text in EXACT:
        return True
    if text.startswith("我是"):
        rest = text[2:].strip()
        if not rest:
            return False
        return (
            rules.normalize_sign(rest) is not None
            or rules.normalize_mbti(rest) is not None
            or bool(re.search(r"\d", rest))
        )
    return any(
        text.startswith(prefix)
        for prefix in ("设置生日", "设置星座", "设置八字", "设置生辰",
                       "设置出生", "设置MBTI", "设置mbti",
                       "点星", "投池", "觉醒", "斗星", "合盘")
    )


class KoiHandler:
    def __init__(self, service: KoiService, nickname_service=None):
        self._service = service
        self._nicks = nickname_service

    # ── Entry ─────────────────────────────────────────────────────

    def handle(self, content: str, msg: dict) -> str | None:
        text = strip_invisible(content)
        if not text:
            return None

        if text in EXACT:
            action = EXACT[text]
            method = getattr(self._service, action, None)
            return self._label(method(msg) if method else None, msg)

        if text.startswith("设置八字") or text.startswith("设置生辰") or \
                text.startswith("设置出生"):
            return self._label(self._service.set_bazi(msg, text), msg)
        if text.startswith("设置生日") or text.startswith("设置星座"):
            return self._label(self._service.set_sign(msg, text), msg)
        if text.startswith("设置MBTI") or text.startswith("设置mbti"):
            return self._label(self._service.set_mbti(msg, text), msg)

        if text.startswith("我是"):
            rest = text[2:].strip()
            if not rest:
                return self._label(
                    "发「我是 天蝎座」设置本命星，"
                    "或「我是 INFP」设置 MBTI。", msg)
            if rules.normalize_sign(rest) is not None or \
                    re.search(r"\d", rest):
                return self._label(self._service.set_sign(msg, rest), msg)
            if rules.normalize_mbti(rest) is not None:
                return self._label(self._service.set_mbti(msg, rest), msg)
            return self._label(self._service.set_sign(msg, rest), msg)

        if text.startswith("点星"):
            return self._label(
                self._service.point_star(msg, text[2:].strip()), msg)
        if text.startswith("觉醒"):
            return self._label(
                self._service.awaken(msg, text[2:].strip()), msg)
        if text.startswith("投池"):
            raw = text[2:].strip()
            return self._label(self._service.pool_bet(msg, raw), msg)
        if text.startswith("斗星"):
            target_id, target_name = self._target(msg)
            return self._label(
                self._service.duel_star(msg, target_id, target_name), msg)
        if text.startswith("合盘"):
            target_id, target_name = self._target(msg)
            return self._label(
                self._service.synastry(msg, target_id, target_name), msg)

        return None

    # ── Target resolution ─────────────────────────────────────────

    def _target(self, msg: dict) -> tuple[str, str]:
        chat_id = str(msg.get("chat_id") or "")
        sender_id = str(msg.get("sender_id") or "")
        members = load_group_members(chat_id) or {}
        reverse = {name: wxid for wxid, name in members.items()}

        for raw in (msg.get("mentioned_ids") or []):
            target_id = str(raw)
            if target_id and target_id != sender_id:
                return target_id, members.get(target_id, target_id)

        content = str(msg.get("content") or "")
        match = _REPLY_PREFIX.match(content)
        if match and match.group(1) != sender_id:
            target_id = match.group(1)
            return target_id, members.get(target_id, target_id)

        match = _AT_NAME.search(content)
        if match:
            name = match.group(1)
            if name in reverse:
                return reverse[name], name
            if name in members:
                return name, members[name]
            return "", name
        return "", ""

    # ── Label ─────────────────────────────────────────────────────

    def _label(self, reply: str | None, msg: dict) -> str | None:
        if not reply:
            return reply
        name = ""
        if self._nicks is not None:
            try:
                name = str(self._nicks.resolve_name(
                    str(msg.get("sender_id") or "")) or "")
            except Exception:
                name = ""
        if not name or name == str(msg.get("sender_id") or ""):
            name = str(msg.get("sender_name") or "").strip()
        if not name:
            return reply
        first_line = reply.split("\n", 1)[0]
        if first_line.startswith(name) or f"@{name}" in first_line:
            return reply
        return f"{name}：{reply}"
