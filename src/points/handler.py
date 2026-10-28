"""Group text commands for points, equipment and duels.

All commands are plain text (no @ mention needed) and are returned as raw
text so the router skips markdown stripping.  ``查询`` is a *weak* command:
if the name does not resolve to exactly one member the handler returns
None and the message continues to the normal AI path.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from pathlib import Path

from src.config import PROJECT_ROOT

from . import rules
from .achievements import (
    ACHIEVEMENTS,
    AchievementService,
    achievement_by_title,
)
from .boss import BossService
from .duel import DuelService
from .equipment import (
    ENHANCE_MAX,
    EquipmentCatalog,
    effective_power,
    enhance_cost,
)
from .fishing import FishingService, fishing_level, format_weight
from .fishing_catalog import RARITIES, RARITY_LABELS
from .levels import LevelTable
from .models import Command, DelayedReply
from .realm import (
    REALM_MAX,
    realm_cost,
    realm_info,
)
from .rules import (
    INVISIBLE_WS as _WS,
    clean_command_text as _clean_for_commands,
    strip_invisible as _strip_invisible,
)
from .store import PointStore
from .util import (
    days_until_season_end,
    is_group_enabled,
    local_day,
    previous_season,
    season_end_day,
)

logger = logging.getLogger(__name__)

# wxid -> 群昵称, written by WcdbBackend at startup.  Members @ each
# other by group nickname ("西湖-金金"), which often differs from the
# contact name stored on messages ("降娄").
_GROUP_NICKNAMES_PATH = Path(PROJECT_ROOT) / "data" / "group_nicknames.json"


EXACT_COMMANDS = {
    "签到": ("checkin", ""),
    "积分": ("member_card", ""),
    "我的积分": ("member_card", ""),
    "积分卡": ("member_card", ""),
    "我的信息": ("my_info", ""),
    "积分榜": ("board", "total"),
    "排行榜": ("board", "total"),
    "月榜": ("board", "season"),
    "今日榜": ("board", "day"),
    "积分规则": ("rules", ""),
    "商店": ("shop", ""),
    "我的装备": ("bag", ""),
    "背包": ("bag", ""),
    "武力值": ("power", ""),
    "战力": ("power", ""),
    "战力榜": ("power_board", ""),
    "切磋记录": ("duel_log", ""),
    "切磋战绩": ("duel_record", ""),
    "战神榜": ("duel_board", ""),
    "买免战券": ("buy_shield", ""),
    "免战": ("buy_shield", ""),
    # ── Fishing ──
    "钓鱼": ("fish", ""),
    "鱼竿": ("fish_rods", ""),
    "鱼饵": ("fish_baits", ""),
    "鱼篓": ("fish_bag", ""),
    "我的鱼": ("fish_bag", ""),
    "钓鱼图鉴": ("fish_book", ""),
    "图鉴": ("fish_book", ""),
    "钓鱼榜": ("fish_board", "total"),
    "钓鱼记录": ("fish_log", ""),
    # ── Endgame ──
    "Boss": ("boss_attack", ""),
    "boss": ("boss_attack", ""),
    "挑战Boss": ("boss_attack", ""),
    "打Boss": ("boss_attack", ""),
    "Boss状态": ("boss_status", ""),
    "Boss榜": ("boss_board", ""),
    "成就": ("achievements", ""),
    "称号": ("titles", ""),
    "境界": ("realm", ""),
    "突破": ("breakthrough", ""),
}

PREFIX_COMMANDS = (
    "购买", "装备", "卸下", "查询", "切磋", "积分调整", "积分重算",
    "钓鱼", "买鱼竿", "买鱼饵", "换鱼竿",
    "强化", "精炼", "佩戴称号", "戴上称号",
)

_BOARD_SCOPES = {
    "总": "total", "总榜": "total",
    "月": "season", "月榜": "season", "本月": "season",
    "今日": "day", "今日榜": "day", "今天": "day",
}

_FISH_BOARD_SCOPES = {
    "总": "value", "总榜": "value", "累计": "value",
    "今日": "today", "今天": "today",
    "大鱼": "biggest", "最大": "biggest",
    "数量": "count", "条数": "count",
}

_CN_NUMBERS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_ADMIN_ADJUST_RE = re.compile(
    rf"^积分调整{_WS}+(.+?){_WS}+([+-]?\d+)(?:{_WS}+(.+))?{_WS}*$"
)


def looks_like_command(text: str) -> bool:
    """Cheap syntax check used by the router for the staleness guard."""
    text = _strip_invisible(text)
    if not text:
        return False
    if text in EXACT_COMMANDS:
        return True
    if re.match(rf"^积分榜{_WS}+", text):
        return True
    if re.match(rf"^钓鱼榜{_WS}+", text):
        return True
    if text.startswith("@") and _ends_with_keyword(text, "切磋"):
        return True
    head = re.split(_WS, text, maxsplit=1)[0]
    return head in PREFIX_COMMANDS


def _norm_key(name: str) -> str:
    name = _strip_invisible(unicodedata.normalize("NFC", name))
    name = name.lstrip("@").strip()
    return re.sub(r"\s+", " ", name).casefold()


class PointHandler:
    """Parses and executes all point-system group commands."""

    def __init__(self, store: PointStore, config,
                 levels: LevelTable, catalog: EquipmentCatalog,
                 duels: DuelService, engine=None,
                 nickname_service=None, group_nicknames_path=None,
                 fishing: FishingService | None = None):
        self._store = store
        self._config = config
        self._levels = levels
        self._catalog = catalog
        self._duels = duels
        self._engine = engine
        self._fishing = fishing
        self._nicks = nickname_service
        self._boss = BossService(store, config, duels)
        self._achievements = AchievementService(
            store, config, levels, duels, fishing,
        )
        self._group_nicknames_path = Path(
            group_nicknames_path or _GROUP_NICKNAMES_PATH,
        )
        self._group_nicks_cache: dict | None = None
        self._group_nicks_key: tuple[str, float] | None = None

    # ── Entry ─────────────────────────────────────────────────────

    def handle(self, content: str, msg: dict) -> str | None:
        """Return reply text, or None when this is not a point command."""
        cfg = self._config
        if not getattr(cfg, "points_enabled", False):
            return None
        chat_id = str(msg.get("chat_id") or "")
        if not is_group_enabled(chat_id, msg.get("group_name", ""),
                                getattr(cfg, "points_groups", ["*"])):
            return None

        text = _clean_for_commands(
            content, getattr(cfg, "bot_display_name", ""),
            getattr(cfg, "bot_aliases", ()) or (),
        )
        if not text:
            return None

        sender_id = str(msg.get("sender_id") or "")
        sender_name = self._display_name(chat_id, sender_id)
        is_admin = bool(getattr(cfg, "admin_wxid", "")) and \
            sender_id == cfg.admin_wxid

        # ── Exact commands ───────────────────────────────────────
        if text in EXACT_COMMANDS:
            kind, arg = EXACT_COMMANDS[text]
            return self._dispatch(kind, arg, text, msg, chat_id,
                                  sender_id, sender_name, is_admin)

        # ── Scoped board: 积分榜 月 / 今日 ────────────────────────
        m = re.match(rf"^积分榜{_WS}+(\S+){_WS}*$", text)
        if m:
            scope = _BOARD_SCOPES.get(m.group(1))
            if scope:
                return self._board(chat_id, msg.get("group_name", ""), scope)

        # ── Scoped fishing board: 钓鱼榜 今日 / 大鱼 ─────────────
        m = re.match(rf"^钓鱼榜{_WS}+(\S+){_WS}*$", text)
        if m:
            scope = _FISH_BOARD_SCOPES.get(m.group(1))
            if scope:
                return self._fish_board(
                    chat_id, msg.get("group_name", ""), scope)

        # ── Prefix commands ──────────────────────────────────────
        parts = re.split(_WS, text, maxsplit=1)
        head = parts[0]
        rest = parts[1].strip() if len(parts) > 1 else ""

        if head == "购买":
            return self._buy(chat_id, sender_id, sender_name, rest)
        if head == "装备":
            return self._equip(chat_id, sender_id, rest, equip=True)
        if head == "卸下":
            return self._equip(chat_id, sender_id, rest, equip=False)
        if head == "查询":
            return self._query(chat_id, rest, sender_id, sender_name)
        if head == "切磋":
            return self._duel(msg, chat_id, sender_id, sender_name,
                              rest, text)
        if head == "钓鱼":
            return self._fish(msg, chat_id, sender_id, sender_name, rest)
        if head == "买鱼竿":
            return self._buy_rod(chat_id, sender_id, sender_name, rest)
        if head == "买鱼饵":
            return self._buy_bait(chat_id, sender_id, sender_name, rest)
        if head == "换鱼竿":
            return self._equip_rod(chat_id, sender_id, rest)
        if head in ("强化", "精炼"):
            return self._enhance(msg, chat_id, sender_id, sender_name, rest)
        if head in ("佩戴称号", "戴上称号"):
            return self._equip_title(
                chat_id, sender_id, sender_name, rest)
        if head == "积分重算":
            return self._rebuild(chat_id, is_admin)
        if head == "积分调整":
            return self._admin_adjust(chat_id, text, is_admin)

        # 引用回复 + 切磋 / @某人 切磋（关键字可能在末尾）
        if _ends_with_keyword(text, "切磋"):
            return self._duel(msg, chat_id, sender_id, sender_name,
                              text, text)

        return None

    # ── Dispatch ──────────────────────────────────────────────────

    def _dispatch(self, kind: str, arg: str, text: str, msg: dict,
                  chat_id: str, sender_id: str, sender_name: str,
                  is_admin: bool) -> str | None:
        if kind == "checkin":
            return self._checkin(chat_id, sender_id, sender_name, msg)
        if kind == "member_card":
            return self._member_card(chat_id, sender_id, sender_name)
        if kind == "my_info":
            return self._my_info(chat_id, sender_id, sender_name)
        if kind == "board":
            return self._board(chat_id, msg.get("group_name", ""), arg or "total")
        if kind == "rules":
            return self._rules()
        if kind == "shop":
            return self._shop(chat_id, sender_id)
        if kind == "bag":
            return self._bag(chat_id, sender_id, sender_name)
        if kind == "power":
            return self._power(chat_id, sender_id, sender_name)
        if kind == "power_board":
            return self._power_board(chat_id, msg.get("group_name", ""))
        if kind == "duel_log":
            return self._duel_log(chat_id, sender_id)
        if kind == "duel_record":
            return self._duel_record(chat_id, sender_id, sender_name)
        if kind == "duel_board":
            return self._duel_board(chat_id, msg.get("group_name", ""))
        if kind == "buy_shield":
            return self._buy_shield(chat_id, sender_id, sender_name)
        if kind == "fish":
            return self._fish(msg, chat_id, sender_id, sender_name, arg)
        if kind == "fish_rods":
            return self._fish_rods(chat_id, sender_id)
        if kind == "fish_baits":
            return self._fish_baits(chat_id, sender_id)
        if kind == "fish_bag":
            return self._fish_bag(chat_id, sender_id, sender_name)
        if kind == "fish_book":
            return self._fish_book(chat_id, sender_id, sender_name)
        if kind == "fish_board":
            return self._fish_board(
                chat_id, msg.get("group_name", ""), arg or "value")
        if kind == "fish_log":
            return self._fish_log(chat_id, sender_id)
        if kind == "boss_attack":
            return self._boss_attack(msg, chat_id, sender_id, sender_name)
        if kind == "boss_status":
            return self._boss_status(chat_id, msg.get("group_name", ""),
                                     sender_id)
        if kind == "boss_board":
            return self._boss_board(chat_id, msg.get("group_name", ""))
        if kind == "achievements":
            return self._achievements_command(
                chat_id, sender_id, sender_name)
        if kind == "titles":
            return self._titles_command(chat_id, sender_id, sender_name)
        if kind == "realm":
            return self._realm_command(chat_id, sender_id, sender_name)
        if kind == "breakthrough":
            return self._breakthrough(msg, chat_id, sender_id, sender_name)
        return None

    # ── Identity helpers ──────────────────────────────────────────

    def _display_name(self, chat_id: str, sender_id: str,
                      fallback: str = "") -> str:
        if self._nicks is not None and sender_id:
            try:
                name = self._nicks.resolve_name(sender_id)
                if name and name != sender_id:
                    return name
            except Exception:  # noqa: BLE001 - nickname service is optional
                pass
        if fallback:
            return fallback
        # New members may not be in the message table yet; their 群昵称
        # (what @mentions use) is the only name we have.
        group_nick = self._load_group_nicknames(chat_id).get(sender_id)
        if group_nick:
            return group_nick
        return self._store.member_name(chat_id, sender_id) or sender_id

    def _candidate_map(self, chat_id: str) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        nick_map: dict[str, str] = {}
        if self._nicks is not None:
            try:
                nick_map = self._nicks.load()
            except Exception:  # noqa: BLE001
                nick_map = {}

        group_nicks = self._load_group_nicknames(chat_id)

        for member in self._store.members(chat_id):
            sid = member["sender_id"]
            # The raw username is also a valid search key: admins are
            # told to use wxid when a nickname is ambiguous.
            names = {member["sender_name"], sid}
            nick = nick_map.get(sid)
            if nick:
                names.add(nick)
            group_nick = group_nicks.get(sid)
            if group_nick:
                names.add(group_nick)
            for name in names:
                key = _norm_key(name)
                if not key:
                    continue
                bucket = mapping.setdefault(key, [])
                if sid not in bucket:
                    bucket.append(sid)

        # Group nicknames also cover members who have never spoken, so
        # 切磋/查询 by 群昵称 works even without message history.
        known = {sid for ids in mapping.values() for sid in ids}
        for sid, group_nick in group_nicks.items():
            if sid in known:
                continue
            for name in (group_nick, sid):
                key = _norm_key(name)
                if not key:
                    continue
                bucket = mapping.setdefault(key, [])
                if sid not in bucket:
                    bucket.append(sid)
        return mapping

    def _load_group_nicknames(self, chat_id: str) -> dict[str, str]:
        """Read ``{wxid: 群昵称}`` for one chat (empty on any error)."""
        try:
            mtime = self._group_nicknames_path.stat().st_mtime
        except OSError:
            self._group_nicks_cache = {}
            self._group_nicks_key = None
            return {}
        key = (str(self._group_nicknames_path), mtime)
        if self._group_nicks_cache is None \
                or self._group_nicks_key != key:
            try:
                data = json.loads(
                    self._group_nicknames_path.read_text(encoding="utf-8"),
                )
            except (OSError, ValueError):
                return {}
            self._group_nicks_cache = data if isinstance(data, dict) else {}
            self._group_nicks_key = key
        mapping = self._group_nicks_cache.get(chat_id)
        if not isinstance(mapping, dict):
            return {}
        return {
            str(sid): str(name)
            for sid, name in mapping.items()
            if name and str(name).strip()
        }

    def _resolve_name(self, chat_id: str,
                      raw: str) -> tuple[str, str, list[str]]:
        """Resolve a typed name.

        Returns ``(sender_id, display_name, ambiguous_ids)``.  When the
        name is unknown both ids are empty; when ambiguous ``sender_id``
        is empty and ``ambiguous_ids`` lists the matches.
        """
        name = _strip_invisible(raw).lstrip("@").strip()
        if not name:
            return "", "", []
        if name in ("我", "自己"):
            return "", "", []

        mapping = self._candidate_map(chat_id)
        key = _norm_key(name)
        ids = mapping.get(key)
        if ids is None:
            # Tolerate extra internal spaces / missing spaces.
            compact = re.sub(r"\s+", "", key)
            for candidate, candidate_ids in mapping.items():
                if re.sub(r"\s+", "", candidate) == compact:
                    ids = candidate_ids
                    break
            if not ids:
                # Group aliases may add a prefix ("萧山-张三") that the
                # message table does not store.  Accept a unique
                # affix match as a fallback.
                ids = self._affix_matches(mapping, compact)

        if not ids:
            return "", "", []
        if len(ids) > 1:
            return "", "", list(ids)
        sid = ids[0]
        return sid, self._display_name(chat_id, sid), []

    @staticmethod
    def _affix_matches(mapping: dict[str, list[str]],
                       compact: str) -> list[str]:
        """Member ids whose name shares a prefix/suffix with *compact*.

        Used when a typed @name carries a group alias prefix (or the
        stored name does) — e.g. "萧山-张三" vs "张三".
        """
        if len(compact) < 2:
            return []
        matched: list[str] = []
        for candidate, candidate_ids in mapping.items():
            flat = re.sub(r"\s+", "", candidate)
            if len(flat) < 2:
                continue
            if not (flat.endswith(compact) or compact.endswith(flat)):
                continue
            for sid in candidate_ids:
                if sid not in matched:
                    matched.append(sid)
        return matched

    def _reply_target(self, chat_id: str, text: str,
                      sender_id: str = "") -> tuple[str, str]:
        """Use the WeChat reply-quote wxid when present."""
        wxid = rules.reply_target(text, sender_id)
        if not wxid:
            return "", ""
        return wxid, self._display_name(chat_id, wxid)

    def _mention_target(self, chat_id: str, msg: dict,
                        sender_id: str) -> tuple[str, str]:
        """Use the wxid behind an @mention when there is exactly one.

        WCDB stores mentions as ``@wxid_xxx``; the backend keeps the
        ids so a display-name/alias mismatch cannot break commands.
        """
        ids = [str(i) for i in (msg.get("mentioned_ids") or []) if i]
        if len(ids) != 1:
            return "", ""
        return ids[0], self._display_name(chat_id, ids[0])

    # ── Check-in ──────────────────────────────────────────────────

    def _checkin(self, chat_id: str, sender_id: str, sender_name: str,
                 msg: dict) -> str:
        """Explicit 签到: award (or confirm) today's check-in bonus."""
        message_id = str(msg.get("message_id") or "")
        # Use the message clock so a command processed just after
        # midnight is filed under the day it was sent.
        ts = int(msg.get("timestamp") or time.time())
        day = local_day(ts)

        # The router scores the message before commands.  A 「签到」
        # message therefore already carries daily_first — report it.
        points = self._store.event_points(
            chat_id, message_id, "daily_first",
        )
        if not points and not self._store.has_reason_on_day(
            chat_id, sender_id, "daily_first", day,
        ):
            # The message itself was not scored (rate limit / repeated
            # text): award the check-in explicitly if still due.
            if self._engine is not None:
                points = self._engine.check_in(
                    chat_id, sender_id, sender_name, message_id,
                    now_ts=ts,
                )

        if points:
            logger.info(
                "Check-in: %s (%s) +%d in %s",
                sender_name, sender_id, points, chat_id[:20],
            )
            return f"@{sender_name} 签到成功，积分+{points}"
        if self._store.has_reason_on_day(
            chat_id, sender_id, "daily_first", day,
        ):
            logger.info(
                "Check-in: %s (%s) already signed today in %s",
                sender_name, sender_id, chat_id[:20],
            )
            return f"@{sender_name} 今天已经签到过啦，明天再来~"
        logger.warning(
            "Check-in failed: %s (%s) in %s (message=%s)",
            sender_name, sender_id, chat_id[:20], message_id[:12],
        )
        return f"@{sender_name} 签到失败，请稍后再试"

    # ── Personal views ────────────────────────────────────────────

    def _my_info(self, chat_id: str, sender_id: str,
                 sender_name: str) -> str:
        stats = self._store.member_stats(chat_id, sender_id, sender_name)
        power = self._duels.power(chat_id, sender_id)
        title = self._store.equipped_title(chat_id, sender_id)
        prefix = f"【{title}】" if title else ""
        realm = realm_info(self._store.realm_of(chat_id, sender_id))
        return (
            f"{prefix}{stats.sender_name} 积分：{stats.earned} "
            f"余额：{stats.balance} 武力值：{power.total}"
            f" 境界：{realm['title']}"
        )

    def _member_card(self, chat_id: str, sender_id: str,
                     sender_name: str) -> str:
        stats = self._store.member_stats(chat_id, sender_id, sender_name)
        power = self._duels.power(chat_id, sender_id)
        level = self._levels.for_points(stats.earned)
        nxt = self._levels.next_for(stats.earned)
        title = self._store.equipped_title(chat_id, sender_id)
        realm = realm_info(power.realm)
        ach = self._achievements.evaluate(chat_id, sender_id)

        lines = [
            f"{stats.sender_name} 积分：{stats.earned} "
            f"· Lv{level['level']} {level['title']}"
            + (f" · 称号「{title}」" if title else ""),
            f"余额 {stats.balance} · 本月 {stats.season_points}"
            + (f"（第{stats.season_rank}名）" if stats.season_rank else "")
            + f" · 今日 {stats.day_points}",
            f"武力值 {power.total}（基础 {power.base} + 装备 {power.equipment}"
            f" + 境界 {power.realm_bonus}）· 境界 {realm['title']}",
        ]
        if nxt is not None:
            lines.append(
                f"距 Lv{nxt['level']} {nxt['title']} 还差 "
                f"{max(0, int(nxt['min_points']) - stats.earned)} 分"
            )
        if stats.streak_days:
            lines.append(f"连续活跃 {stats.streak_days} 天")
        if ach["new"]:
            lines.append(
                "新解锁成就：" + "、".join(
                    f"{item['name']}（称号「{item['title']}」）"
                    for item in ach["new"]
                )
            )
        if self._fishing is not None:
            fish = self._fishing.stats(chat_id, sender_id)
            if fish["catches"]:
                level = fish.get("level") or {}
                lines.append(
                    f"钓鱼 Lv{level.get('level', 1)} {level.get('title', '')}"
                    f" · {fish['catches']} 条 · 入账 "
                    f"{fish['total_value']}（只加余额，不计积分）"
                )
        return "\n".join(lines)

    def _query(self, chat_id: str, raw: str, sender_id: str = "",
               sender_name: str = "") -> str | None:
        name = _strip_invisible(raw).lstrip("@").strip()
        if not name:
            return None
        if name in ("我", "自己"):
            if sender_id:
                return self._my_info(chat_id, sender_id, sender_name)
            return None

        sid, display, ambiguous = self._resolve_name(chat_id, name)
        if ambiguous:
            names = "、".join(
                self._display_name(chat_id, i) for i in ambiguous[:3]
            )
            return f"本群有多个同名成员（{names}），请用更完整的昵称查询。"
        if not sid:
            # Weak command: stay silent so normal chat is not interrupted.
            return None

        stats = self._store.member_stats(chat_id, sid, display)
        power = self._duels.power(chat_id, sid)
        return (
            f"{stats.sender_name} 积分：{stats.earned} "
            f"余额：{stats.balance} "
            f"武力值：{power.total}"
        )

    # ── Boards ────────────────────────────────────────────────────

    def _board(self, chat_id: str, group_name: str, scope: str) -> str:
        size = int(getattr(self._config, "points_board_size", 10))
        rows = self._store.board(chat_id, scope, size)
        group = _strip_invisible(group_name) or chat_id

        if scope == "season":
            now = int(time.time())
            season = time.strftime("%Y-%m", time.localtime(now))
            header = (
                f"群积分榜 · {group}（{season} 赛季"
                f"，{season_end_day(season)[5:]} 截止）"
            )
        elif scope == "day":
            header = f"群积分榜 · {group}（今日）"
        else:
            header = f"群积分榜 · {group}（总榜）"

        lines = [header]
        if not rows:
            lines.append("本群还没有积分记录，先聊几句吧。")
            return "\n".join(lines)

        for row in rows:
            level = self._levels.for_points(row["points"])
            lines.append(
                f"{row['rank']}. {row['sender_name']} {row['points']}分 "
                f"Lv{level['level']}{level['title']} · 余额{row['balance']}"
            )

        if scope == "season":
            lines.append(f"距赛季结束还有 {days_until_season_end()} 天")
            prev = previous_season()
            winner = self._store.close_season(chat_id, prev)
            if winner:
                lines.append(
                    f"上月冠军：{winner['winner_name']} "
                    f"{winner['winner_points']}分"
                )
        else:
            participants = self._store.participants(chat_id)
            today_total = self._store.day_total(chat_id, local_day())
            lines.append(
                f"本群共 {participants} 人参与 · 今日产生 {today_total} 分"
            )
        return "\n".join(lines)

    # ── Shop / equipment ──────────────────────────────────────────

    def _shop(self, chat_id: str, sender_id: str) -> str:
        if not getattr(self._config, "equipment_enabled", False):
            return "装备系统未开启。"
        catalog = self._catalog.load()
        owned = {item["item_id"] for item in
                 self._store.owned_items(chat_id, sender_id)}
        balance = self._store.balance_of(chat_id, sender_id)

        lines = [f"装备商店 · 余额 {balance}"]
        for slot in catalog["slots"]:
            items = [i for i in catalog["items"] if i["slot"] == slot["id"]]
            if not items:
                continue
            rendered = []
            for item in items:
                mark = " ✓" if item["id"] in owned else ""
                rendered.append(
                    f"{item['name']} {item['price']}分(+{item['power']}){mark}"
                )
            lines.append(f"【{slot['name']}】" + " · ".join(rendered))
        lines.append("发送「购买 名称」即可；「我的装备」查看背包。")
        return "\n".join(lines)

    def _buy(self, chat_id: str, sender_id: str, sender_name: str,
             raw: str) -> str | None:
        if not raw:
            return "用法：购买 装备名（先发「商店」看清单）"
        if not getattr(self._config, "equipment_enabled", False):
            return "装备系统未开启。"
        item = self._catalog.find(raw)
        if item is None:
            return f"商店里没有「{raw}」，发「商店」看看有哪些装备。"

        ok, code, balance, equipped = self._store.buy_item(
            chat_id, sender_id, sender_name, item,
            self._catalog.same_slot_ids(item),
        )
        if code == "insufficient":
            return (
                f"余额不足：{item['name']} 需要 {item['price']} 分，"
                f"你只有 {balance} 分。"
            )
        if code == "owned":
            return f"你已经拥有 {item['name']} 了。"

        power = self._duels.power(chat_id, sender_id)
        suffix = (
            f"，已装备到{self._catalog.slot_name(item['slot'])}"
            if equipped else "，已放入背包（「装备 名称」可穿上）"
        )
        return (
            f"购买成功：{item['name']}（武力 +{item['power']}），"
            f"花费 {item['price']} 积分，余额 {balance}{suffix}。\n"
            f"当前武力值 {power.total}。"
        )

    def _equip(self, chat_id: str, sender_id: str, raw: str,
               equip: bool) -> str | None:
        if not raw:
            return (
                "用法：装备 名称 / 卸下 槽位"
                if equip else "用法：卸下 武器|护甲|饰品"
            )
        item = self._catalog.find(raw)

        if not equip:
            # Accept either an item name or a slot name.
            slot = self._find_slot(raw)
            if slot is not None and item is None:
                for owned in self._store.owned_items(chat_id, sender_id):
                    candidate = self._catalog.by_id(owned["item_id"])
                    if (candidate and candidate["slot"] == slot["id"]
                            and owned.get("equipped")):
                        item = candidate
                        break
                if item is None:
                    return f"你的{slot['name']}槽位没有装备。"
            if item is None:
                return f"没有找到「{raw}」。"
            if not self._store.owns(chat_id, sender_id, item["id"]):
                return f"你还没有 {item['name']}。"
            self._store.equip_item(
                chat_id, sender_id, item["id"],
                self._catalog.same_slot_ids(item), equip=False,
            )
            power = self._duels.power(chat_id, sender_id)
            return (
                f"已卸下 {item['name']}（{self._catalog.slot_name(item['slot'])}）"
                f"，当前武力值 {power.total}。"
            )

        if item is None:
            return f"没有找到「{raw}」，发「商店」查看装备名。"
        if not self._store.owns(chat_id, sender_id, item["id"]):
            return f"你还没有 {item['name']}，先「购买 {item['name']}」。"
        self._store.equip_item(
            chat_id, sender_id, item["id"],
            self._catalog.same_slot_ids(item), equip=True,
        )
        power = self._duels.power(chat_id, sender_id)
        return (
            f"已装备 {item['name']}（{self._catalog.slot_name(item['slot'])}，"
            f"武力 +{item['power']}），当前武力值 {power.total}。"
        )

    def _find_slot(self, raw: str) -> dict | None:
        key = _strip_invisible(raw)
        for slot in self._catalog.slots():
            if key in (slot["id"], slot["name"]):
                return slot
        return None

    def _bag(self, chat_id: str, sender_id: str, sender_name: str) -> str:
        if not getattr(self._config, "equipment_enabled", False):
            return "装备系统未开启。"
        owned = self._store.owned_items(chat_id, sender_id)
        power = self._duels.power(chat_id, sender_id)
        if not owned:
            return (
                f"{sender_name} 还没有装备。武力值 {power.total}，"
                "发「商店」看看能买什么。"
            )

        by_slot: dict[str, list[str]] = {}
        for row in owned:
            item = self._catalog.by_id(row["item_id"])
            if item is None:
                continue
            level = int(row.get("enhance_level", 0) or 0)
            mark = "（已装备）" if row.get("equipped") else ""
            suffix = f"(+{level})" if level else ""
            by_slot.setdefault(item["slot"], []).append(
                f"{item['name']} +{effective_power(item, level)}"
                f"{suffix}{mark}"
            )

        lines = [f"{sender_name} 的装备 · 武力值 {power.total}"]
        for slot in self._catalog.slots():
            items = by_slot.get(slot["id"])
            if items:
                lines.append(f"【{slot['name']}】" + " · ".join(items))
        balance = self._store.balance_of(chat_id, sender_id)
        lines.append(f"余额 {balance}")
        return "\n".join(lines)

    def _power(self, chat_id: str, sender_id: str,
               sender_name: str) -> str:
        power = self._duels.power(chat_id, sender_id)
        parts = [f"基础 {power.base}（Lv{power.level} {power.title}）"]
        for item in power.items:
            level = int(item.get("enhance_level", 0) or 0)
            suffix = f"(+{level})" if level else ""
            parts.append(
                f"{self._catalog.slot_name(item['slot'])} {item['name']} "
                f"+{item.get('effective_power', item['power'])}{suffix}"
            )
        if power.realm_bonus:
            parts.append(
                f"境界 {realm_info(power.realm)['title']} "
                f"+{power.realm_bonus}"
            )
        lines = [
            f"{sender_name} 的武力值：{power.total}",
            " + ".join(parts),
        ]

        now = int(time.time())
        day = local_day(now)
        attempts = self._store.duel_attempts(chat_id, sender_id, day)
        limit = int(getattr(self._config, "duel_daily_attempts", 10))
        balance = self._store.balance_of(chat_id, sender_id)
        lines.append(f"今日切磋 {attempts}/{limit} · 余额 {balance}")

        shield = self._store.shield_expires_at(chat_id, sender_id, now)
        if shield:
            lines.append(f"免战中，剩余 {self._format_duration(shield - now)}")

        record = self._duels.record(chat_id, sender_id)
        rank = record["rank"]
        lines.append(
            f"切磋段位 {rank['title']} · 生涯 {record['wins']}胜"
            f"{record['losses']}负"
        )
        return "\n".join(lines)

    def _power_board(self, chat_id: str, group_name: str) -> str:
        size = int(getattr(self._config, "points_board_size", 10))
        rows = []
        for member in self._store.members(chat_id):
            power = self._duels.power(chat_id, member["sender_id"])
            rows.append((power.total, member["sender_id"]))
        rows.sort(key=lambda item: (-item[0], item[1]))
        group = _strip_invisible(group_name) or chat_id
        lines = [f"战力榜 · {group}"]
        if not rows:
            lines.append("本群还没有成员记录。")
            return "\n".join(lines)
        for rank, (total, sid) in enumerate(rows[:size], 1):
            lines.append(f"{rank}. {self._display_name(chat_id, sid)} {total}")
        return "\n".join(lines)

    # ── Duel ──────────────────────────────────────────────────────

    def _duel(self, msg: dict, chat_id: str, sender_id: str,
              sender_name: str, rest: str, text: str) -> str | None:
        if not getattr(self._config, "duel_enabled", False):
            return "切磋功能未开启。"

        # Reply target comes from the RAW content: _clean_for_commands
        # strips the "wxid_xxx:" prefix that identifies the quoted member.
        target_id, target_name = self._reply_target(
            chat_id, str(msg.get("content", "")), sender_id,
        )
        if not target_id:
            target_id, target_name = self._mention_target(
                chat_id, msg, sender_id,
            )
        if not target_id:
            name = _strip_invisible(rest).lstrip("@").strip()
            name = re.sub(rf"{_WS}*切磋$", "", name).strip()
            if not name:
                return "用法：切磋 @某人，或引用对方消息后发「切磋」。"
            target_id, target_name, ambiguous = self._resolve_name(
                chat_id, name,
            )
            if ambiguous:
                names = "、".join(
                    self._display_name(chat_id, i) for i in ambiguous[:3]
                )
                return (
                    f"本群有多个「{name}」（{names}），"
                    "请改用引用回复来指定。"
                )
            if not target_id:
                return f"没找到成员「{name}」，确认一下昵称。"

        target_name = target_name or self._display_name(chat_id, target_id)
        result = self._duels.challenge(
            msg, target_id, defender_name=target_name,
        )
        return self._format_duel(result, target_name, sender_name)

    def _format_duel(self, result, defender_name: str,
                     attacker_name: str) -> str:
        if not result.ok:
            return result.reply or "切磋失败。"

        prob = round(result.win_prob * 100)
        lines = [
            f"{attacker_name} 武力{result.attacker_power} · "
            f"{defender_name} 武力{result.defender_power} · 胜率{prob}%",
        ]
        if result.attacker_win:
            if result.points_moved > 0:
                lines.append(
                    f"结果：切磋成功！从 {defender_name} 处抢到 "
                    f"{result.points_moved} 积分"
                )
            else:
                lines.append(
                    f"结果：切磋成功！但 {defender_name} 已经没什么可抢的了。"
                )
        else:
            lines.append(
                "结果：惜败。失败不扣分，"
                f"{self._format_duration(safe_int(
                    getattr(self._config, 'duel_loss_cooldown_sec', 120)))}"
                "后可再次发起。"
            )
        lines.append(
            f"余额 {result.balance_after} · "
            f"今日 {result.attempts_used}/{result.attempts_limit} 次 · "
            f"已抢 {result.stolen_today}/{result.steal_cap}"
        )
        return "\n".join(lines)

    def _duel_log(self, chat_id: str, sender_id: str) -> str:
        rows = [
            row for row in self._store.recent_duels(chat_id, 50)
            if sender_id in (row["attacker_id"], row["defender_id"])
        ][:10]
        if not rows:
            return "你还没有切磋记录。"
        lines = ["最近的切磋记录"]
        for row in rows:
            when = time.strftime("%m-%d %H:%M",
                                 time.localtime(int(row["created_at"])))
            if row["shield_blocked"]:
                outcome = "被免战挡下"
            elif row["attacker_win"]:
                outcome = f"抢到 {row['points_moved']} 分"
            else:
                outcome = "落败"
            lines.append(
                f"{when} {row['attacker_name']} vs "
                f"{row['defender_name']}：{outcome}"
            )
        return "\n".join(lines)

    def _duel_record(self, chat_id: str, sender_id: str,
                     sender_name: str) -> str:
        if not getattr(self._config, "duel_enabled", False):
            return "切磋功能未开启。"
        record = self._duels.record(chat_id, sender_id)
        rank = record["rank"]
        power = self._duels.power(chat_id, sender_id)
        win_rate = (
            f"{round(record['wins'] / record['total'] * 100)}%"
            if record["total"] else "—"
        )
        lines = [
            f"{sender_name} 切磋战绩 · {rank['title']}段（战力 {power.total}）",
            f"生涯 {record['total']} 战 · 胜 {record['wins']} · "
            f"负 {record['losses']} · 胜率 {win_rate} · "
            f"累计抢 {record['stolen']} 分",
        ]
        nxt = rank.get("next")
        if nxt:
            lines.append(
                f"距 {nxt['title']}段 还差 {rank['wins_to_next']} 胜"
            )
        else:
            lines.append("已达最高段位：战神")
        return "\n".join(lines)

    def _duel_board(self, chat_id: str, group_name: str) -> str:
        if not getattr(self._config, "duel_enabled", False):
            return "切磋功能未开启。"
        size = int(getattr(self._config, "points_board_size", 10))
        rows = self._duels.leaderboard(chat_id, size)
        group = _strip_invisible(group_name) or chat_id
        lines = [f"战神榜 · {group}（按生涯胜场）"]
        if not rows:
            lines.append("本群还没有切磋记录，先发「@某人 切磋」试试。")
            return "\n".join(lines)
        for index, row in enumerate(rows, 1):
            wins = int(row.get("wins", 0))
            total = int(row.get("total", 0))
            losses = max(0, total - wins)
            rank = row["rank"]
            lines.append(
                f"{index}. {row['sender_name']} {rank['title']}段 · "
                f"{wins}胜{losses}负 · 抢 {row['stolen']} 分"
            )
        return "\n".join(lines)

    def _buy_shield(self, chat_id: str, sender_id: str,
                    sender_name: str) -> str:
        if not getattr(self._config, "duel_enabled", False):
            return "切磋功能未开启，暂时不需要免战券。"
        price = int(getattr(self._config, "shield_price", 20))
        duration = int(getattr(self._config, "shield_duration_sec", 14400))
        maximum = int(getattr(self._config, "shield_max_sec", 86400))
        ok, code, expires, balance = self._store.buy_shield(
            chat_id, sender_id, sender_name, price, duration, maximum,
        )
        if not ok:
            return f"余额不足：免战券需要 {price} 分，你只有 {balance} 分。"
        remaining = max(0, expires - int(time.time()))
        return (
            f"免战券生效：{self._format_duration(remaining)}内不会被切磋，"
            f"花费 {price} 积分，余额 {balance}。"
        )

    # ── Fishing ───────────────────────────────────────────────────

    def _fishing_available(self) -> bool:
        return self._fishing is not None and bool(
            getattr(self._config, "fishing_enabled", False))

    def _fish(self, msg: dict, chat_id: str, sender_id: str,
              sender_name: str, raw: str) -> str:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        count = 1
        raw = _strip_invisible(raw)
        if raw:
            parsed = self._parse_fish_count(raw)
            if parsed is None:
                limit = int(getattr(
                    self._config, "fishing_max_batch", 10))
                return f"用法：钓鱼 或 钓鱼 5（一次最多 {limit} 竿）"
            count = parsed
        result = self._fishing.cast(msg, count=count)
        return self._format_fishing(result, sender_name)

    @staticmethod
    def _parse_fish_count(raw: str) -> int | None:
        value: int | None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = _CN_NUMBERS.get(str(raw or "").strip())
        if value is None or value < 1:
            return None
        return value

    def _format_fishing(self, result, sender_name: str) -> str:
        if not result.ok:
            return result.reply or "钓鱼失败。"

        if len(result.casts) == 1:
            catch = result.casts[0]
            if not catch.species_id:
                lines = [f"{sender_name} 空竿了，鱼儿跑了。"]
            else:
                rarity = RARITY_LABELS.get(catch.rarity, catch.rarity)
                lines = [
                    f"{sender_name} 钓到 {catch.species_name}"
                    f"（{format_weight(catch.weight_g)}，{rarity}），"
                    f"余额 +{catch.amount}。",
                ]
        else:
            lines = [f"{sender_name} 抛了 {len(result.casts)} 竿："]
            for index, catch in enumerate(result.casts, 1):
                lines.append(f"{index}. {self._format_catch_text(catch)}")

        footer = (
            f"本次入账 +{result.total_value} · 余额 {result.balance_after}"
            f" · 本小时 {result.attempts_used}/{result.attempts_limit} 次"
        )
        if result.daily_cap:
            footer += f" · 收益 {result.earned_today}/{result.daily_cap}"
        lines.append(footer)

        # Immediate acknowledgement, then the catch report after the
        # rarity-based delay.  The ack goes through the normal reply path.
        count = len(result.casts)
        ack = (
            f"{sender_name} 钓鱼中，请稍等…" if count == 1
            else f"{sender_name} 正在钓 {count} 竿，请稍等…"
        )
        return DelayedReply(
            "\n".join(lines), getattr(result, "delay_sec", 0),
            ack_text=ack,
        )

    @staticmethod
    def _format_catch_text(catch) -> str:
        if not catch.species_id:
            return "空竿，鱼儿跑了"
        rarity = RARITY_LABELS.get(catch.rarity, catch.rarity)
        return (
            f"{catch.species_name}（{format_weight(catch.weight_g)}，"
            f"{rarity}）+{catch.amount}"
        )

    @staticmethod
    def _pct(value) -> str:
        return f"{round(float(value or 0) * 100)}%"

    def _fish_rods(self, chat_id: str, sender_id: str) -> str:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        catalog = self._fishing.catalog
        inventory = {
            row["item_id"] for row in
            self._store.fishing_inventory(chat_id, sender_id)
            if row["kind"] == "rod"
        }
        equipped = self._fishing.rod_of(chat_id, sender_id)["id"]
        balance = self._store.balance_of(chat_id, sender_id)

        lines = [f"鱼竿商店 · 余额 {balance}"]
        for rod in catalog.rods():
            price = "默认" if int(rod["price"]) <= 0 else f"{rod['price']}分"
            if rod["id"] == equipped:
                mark = " · 已装备"
            elif rod["id"] in inventory:
                mark = " · 已拥有"
            else:
                mark = ""
            lines.append(
                f"{rod['name']} {price} · "
                f"稀有+{self._pct(rod['rare_bonus'])} "
                f"价值+{self._pct(rod['value_bonus'])} "
                f"空竿-{self._pct(rod['miss_reduction'])}{mark}"
            )
        lines.append(
            "发送「买鱼竿 名称」购买（自动装备），"
            "「换鱼竿 名称」切换。"
        )
        return "\n".join(lines)

    def _buy_rod(self, chat_id: str, sender_id: str, sender_name: str,
                 raw: str) -> str | None:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        raw = _strip_invisible(raw)
        if not raw:
            return "用法：买鱼竿 名称（先发「鱼竿」看清单）"
        rod = self._fishing.catalog.find_rod(raw)
        if rod is None:
            return f"没有找到鱼竿「{raw}」，发「鱼竿」看看清单。"
        if rod.get("default"):
            return "竹竿是默认鱼竿，无需购买。"

        ok, code, balance = self._store.buy_fishing_rod(
            chat_id, sender_id, sender_name, rod,
        )
        if code == "insufficient":
            return (
                f"余额不足：{rod['name']} 需要 {rod['price']}，"
                f"你只有 {balance}。"
            )
        if code == "owned":
            return f"你已经拥有 {rod['name']} 了。"
        return (
            f"购买成功：{rod['name']}（稀有+{self._pct(rod['rare_bonus'])} "
            f"价值+{self._pct(rod['value_bonus'])}），"
            f"花费 {rod['price']} 余额，余额 {balance}，已自动装备。"
        )

    def _equip_rod(self, chat_id: str, sender_id: str,
                   raw: str) -> str | None:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        raw = _strip_invisible(raw)
        if not raw:
            return "用法：换鱼竿 名称（竹竿可写「换鱼竿 竹竿」）"
        rod = self._fishing.catalog.find_rod(raw)
        if rod is None:
            return f"没有找到鱼竿「{raw}」，发「鱼竿」看看清单。"
        if rod.get("default"):
            self._store.equip_fishing_rod(chat_id, sender_id, "")
            return "已换回默认竹竿。"
        if not self._store.owns_fishing_item(
            chat_id, sender_id, "rod", rod["id"],
        ):
            return f"你还没有 {rod['name']}，先「买鱼竿 {rod['name']}」。"
        self._store.equip_fishing_rod(chat_id, sender_id, rod["id"])
        return f"已装备 {rod['name']}。"

    def _fish_baits(self, chat_id: str, sender_id: str) -> str:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        catalog = self._fishing.catalog
        quantities = {
            row["item_id"]: int(row["quantity"])
            for row in self._store.fishing_inventory(chat_id, sender_id)
            if row["kind"] == "bait"
        }
        balance = self._store.balance_of(chat_id, sender_id)

        lines = [f"鱼饵商店 · 余额 {balance}"]
        for bait in catalog.baits():
            lines.append(
                f"{bait['name']} {bait['price']}余额/个 · "
                f"稀有+{self._pct(bait['rare_bonus'])} "
                f"价值+{self._pct(bait['value_bonus'])} · "
                f"持有 {quantities.get(bait['id'], 0)}"
            )
        lines.append(
            "发送「买鱼饵 红虫 10」购买（默认 10 个）；"
            "每次钓鱼自动消耗 1 个，价格低的优先。"
        )
        return "\n".join(lines)

    def _parse_bait_order(self, raw: str):
        """Parse ``[名称] [数量]`` (either order) into (bait, qty, error)."""
        catalog = self._fishing.catalog
        baits = sorted(catalog.baits(), key=lambda item: item["price"])
        default_bait = baits[0] if baits else None
        tokens = [t for t in re.split(_WS, _strip_invisible(raw)) if t]
        if not tokens:
            return default_bait, 10, ""

        name = ""
        quantity_raw = ""
        if tokens[0].isdigit():
            quantity_raw = tokens[0]
            if len(tokens) > 1:
                name = tokens[1]
        else:
            name = tokens[0]
            if len(tokens) > 1:
                quantity_raw = tokens[1]

        bait = catalog.find_bait(name) if name else default_bait
        if bait is None:
            return None, 0, f"没有找到鱼饵「{name}」。"
        if quantity_raw:
            try:
                quantity = int(quantity_raw)
            except ValueError:
                return None, 0, "鱼饵数量必须是整数。"
        else:
            quantity = 10
        if not (1 <= quantity <= 99):
            return None, 0, "一次最多买 99 个鱼饵。"
        return bait, quantity, ""

    def _buy_bait(self, chat_id: str, sender_id: str, sender_name: str,
                  raw: str) -> str | None:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        bait, quantity, error = self._parse_bait_order(raw)
        if error:
            return error
        if bait is None:
            return "鱼饵商店是空的。"

        ok, code, balance = self._store.buy_fishing_bait(
            chat_id, sender_id, sender_name, bait, quantity,
        )
        total = int(bait["price"]) * quantity
        if code == "insufficient":
            return (
                f"余额不足：{bait['name']}×{quantity} 需要 {total}，"
                f"你只有 {balance}。"
            )
        stock = sum(
            int(row["quantity"]) for row in
            self._store.fishing_inventory(chat_id, sender_id)
            if row["kind"] == "bait" and row["item_id"] == bait["id"]
        )
        return (
            f"购买成功：{bait['name']}×{quantity}，花费 {total} 余额，"
            f"余额 {balance}，当前持有 {stock} 个。"
        )

    def _fish_bag(self, chat_id: str, sender_id: str,
                  sender_name: str) -> str:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        stats = self._fishing.stats(chat_id, sender_id)
        rod = stats["rod"]

        lines = [f"{sender_name} 的鱼篓"]
        summary = (
            f"本小时 {stats['attempts']}/{stats['attempts_limit']} 次"
            f" · 余额 {stats['balance']}"
        )
        reset_in = int(stats.get("attempts_reset_in", 0))
        if reset_in:
            summary += (
                f" · {max(1, (reset_in + 59) // 60)} 分钟后重置"
            )
        if stats["daily_cap"]:
            summary += (
                f" · 今日收益 {stats['earned_today']}/{stats['daily_cap']}"
            )
        lines.append(summary)
        lines.append(
            f"鱼竿：{rod['name']}（稀有+{self._pct(rod['rare_bonus'])} "
            f"价值+{self._pct(rod['value_bonus'])}）"
        )
        level = stats.get("level") or {}
        if level:
            lines.append(
                f"钓鱼等级 Lv{level['level']} {level['title']}"
                f"（熟练 稀有+{self._pct(level.get('rare_bonus'))} "
                f"价值+{self._pct(level.get('value_bonus'))}）"
            )
            nxt = level.get("next")
            if nxt:
                lines.append(
                    f"距 Lv{nxt['level']} {nxt['title']} 还差 "
                    f"{level.get('catches_to_next', 0)} 条"
                )
            else:
                lines.append("已达最高钓鱼等级：渔神")
        if stats["baits"]:
            lines.append("鱼饵：" + " · ".join(
                f"{bait['name']}×{bait['quantity']}"
                for bait in stats["baits"]
            ))
        else:
            lines.append("鱼饵：无（仍可钓鱼，只是稀有概率较低）")
        lines.append(
            f"累计 {stats['catches']} 条 · 入账 {stats['total_value']}"
            f" · 最大 {format_weight(stats['biggest_weight']) if stats['biggest_weight'] else '—'}"
        )
        lines.append(
            f"图鉴 {len(stats['caught_species'])}/{stats['species_total']}"
        )

        recent = self._store.fishing_recent(chat_id, sender_id, 5)
        if recent:
            lines.append("最近：" + " · ".join(
                f"{row['species_name']} "
                f"{format_weight(row['weight_g'])}+{row['amount']}"
                for row in recent
            ))
        return "\n".join(lines)

    def _fish_book(self, chat_id: str, sender_id: str,
                   sender_name: str) -> str:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        species = self._fishing.catalog.species()
        caught = set(self._store.fishing_collection(chat_id, sender_id))
        totals = self._store.fishing_totals(chat_id, sender_id)
        level = fishing_level(int(totals.get("catches", 0)))

        lines = [
            f"{sender_name} 的钓鱼图鉴 {len(caught)}/{len(species)}"
            f" · Lv{level['level']} {level['title']}",
        ]
        for rarity in RARITIES:
            group = [item for item in species if item["rarity"] == rarity]
            if not group:
                continue
            entries = [
                f"{'✓' if item['id'] in caught else '·'}{item['name']}"
                f"({item['base_value']})"
                for item in group
            ]
            lines.append(f"【{RARITY_LABELS[rarity]}】" + " ".join(entries))
        lines.append("图例：✓ 已钓到 · 未钓到，括号内为基础价值。")
        return "\n".join(lines)

    def _fish_board(self, chat_id: str, group_name: str,
                    scope: str = "value") -> str:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        size = int(getattr(self._config, "points_board_size", 10))
        group = _strip_invisible(group_name) or chat_id

        if scope == "biggest":
            rows = self._store.fishing_board(
                chat_id, "biggest", limit=size)
            lines = [f"大鱼榜 · {group}"]
            if not rows:
                lines.append("本群还没有钓鱼记录，先发「钓鱼」试试。")
                return "\n".join(lines)
            for rank, row in enumerate(rows, 1):
                lines.append(
                    f"{rank}. {row['sender_name']} {row['species_name']} "
                    f"{format_weight(row['weight_g'])} +{row['amount']}"
                )
            return "\n".join(lines)

        board_scope = "count" if scope == "count" else "value"
        day = local_day() if scope == "today" else None
        rows = self._store.fishing_board(
            chat_id, board_scope, day=day, limit=size,
        )
        label = "今日" if scope == "today" else "总"
        metric = "条数" if scope == "count" else "收益"
        lines = [f"钓鱼榜 · {group}（{label}·按{metric}）"]
        if not rows:
            lines.append("本群还没有钓鱼记录，先发「钓鱼」试试。")
            return "\n".join(lines)
        for rank, row in enumerate(rows, 1):
            lines.append(
                f"{rank}. {row['sender_name']} {row['value']}余额"
                f" · {row['catches']}条"
                f" · 最大 {format_weight(row['biggest'])}"
            )
        return "\n".join(lines)

    def _fish_log(self, chat_id: str, sender_id: str) -> str:
        if not self._fishing_available():
            return "钓鱼功能未开启。"
        rows = self._store.fishing_recent(chat_id, sender_id, 10)
        if not rows:
            return "你还没有钓鱼记录。"
        lines = ["最近的钓鱼记录"]
        for row in rows:
            when = time.strftime(
                "%m-%d %H:%M", time.localtime(int(row["created_at"])))
            rarity = RARITY_LABELS.get(row["rarity"], row["rarity"])
            lines.append(
                f"{when} {row['species_name']}"
                f"（{format_weight(row['weight_g'])}，{rarity}）"
                f"+{row['amount']}"
            )
        return "\n".join(lines)

    # ── Endgame: enhance / realm / boss / achievements ────────────

    def _enhance(self, msg: dict, chat_id: str, sender_id: str,
                 sender_name: str, raw: str) -> str:
        if not getattr(self._config, "equipment_enabled", False):
            return "装备系统未开启。"
        name = _strip_invisible(raw).strip()
        if not name:
            return f"用法：强化 装备名（最高 +{ENHANCE_MAX}）"
        item = self._catalog.find(name)
        if item is None:
            return f"没有找到「{name}」，发「商店」查看装备名。"
        if not self._store.owns(chat_id, sender_id, item["id"]):
            return f"你还没有 {item['name']}，先「购买 {item['name']}」。"

        level = self._store.item_enhance_level(
            chat_id, sender_id, item["id"])
        if level >= ENHANCE_MAX:
            return f"{item['name']} 已经强化到 +{ENHANCE_MAX} 了。"
        cost = enhance_cost(item, level)
        balance = self._store.balance_of(chat_id, sender_id)
        if balance < cost:
            return (
                f"余额不足：把 {item['name']} 强化到 "
                f"+{level + 1} 需要 {cost} 余额，你只有 {balance}。"
            )

        ok, code, new_level, balance = self._store.enhance_item(
            chat_id, sender_id, sender_name, item["id"], cost,
            ENHANCE_MAX, f"enhance:{msg.get('message_id') or ''}",
        )
        if code == "max":
            return f"{item['name']} 已经强化到 +{ENHANCE_MAX} 了。"
        if code == "insufficient":
            return (
                f"余额不足：需要 {cost} 余额，你只有 {balance}。"
            )
        if not ok:
            return "强化失败，请稍后再试。"

        power = self._duels.power(chat_id, sender_id)
        return (
            f"强化成功：{item['name']} +{new_level}"
            f"（武力 +{effective_power(item, new_level)}，"
            f"强化加成 +{new_level * 2}），花费 {cost} 余额，"
            f"余额 {balance}，当前战力 {power.total}。"
        )

    def _realm_command(self, chat_id: str, sender_id: str,
                       sender_name: str) -> str:
        realm = self._store.realm_of(chat_id, sender_id)
        info = realm_info(realm)
        power = self._duels.power(chat_id, sender_id)
        lines = [
            f"{sender_name} 境界：{info['title']}（武力 +{info['power_bonus']}）"
            f" · 当前战力 {power.total}",
        ]
        if info["next"] is not None:
            max_level = self._levels.load()[-1]
            earned = self._store.earned_of(chat_id, sender_id)
            lines.append(
                f"下一境界：{info['next_title']}"
                f"（武力 +{realm_info(info['next'])['power_bonus']}）"
            )
            lines.append(
                f"突破消耗 {info['next_cost']} 余额 · "
                f"需要 Lv{max_level['level']} {max_level['title']}"
                f"（当前累计 {earned}/{max_level['min_points']}）"
            )
            lines.append("发送「突破」即可尝试。")
        else:
            lines.append("已达最高境界：渡劫。")
        return "\n".join(lines)

    def _breakthrough(self, msg: dict, chat_id: str, sender_id: str,
                      sender_name: str) -> str:
        realm = self._store.realm_of(chat_id, sender_id)
        if realm >= REALM_MAX:
            return "你已经达到最高境界：渡劫。"
        max_level = self._levels.load()[-1]
        earned = self._store.earned_of(chat_id, sender_id)
        if earned < int(max_level["min_points"]):
            return (
                f"境界突破需要先达到 Lv{max_level['level']} "
                f"{max_level['title']}（累计 {max_level['min_points']} 分），"
                f"你当前 {earned} 分。"
            )
        cost = realm_cost(realm)
        balance = self._store.balance_of(chat_id, sender_id)
        if balance < cost:
            return (
                f"余额不足：突破到 {realm_info(realm + 1)['title']} "
                f"需要 {cost} 余额，你只有 {balance}。"
            )
        ok, code, new_realm, balance = self._store.breakthrough(
            chat_id, sender_id, sender_name, cost, REALM_MAX,
            f"realm:{msg.get('message_id') or ''}",
        )
        if code == "max":
            return "你已经达到最高境界：渡劫。"
        if code == "insufficient":
            return f"余额不足：需要 {cost} 余额，你只有 {balance}。"
        if not ok:
            return "突破失败，请稍后再试。"

        info = realm_info(new_realm)
        self._achievements.evaluate(chat_id, sender_id)
        power = self._duels.power(chat_id, sender_id)
        return (
            f"突破成功！境界 → {info['title']}，"
            f"武力 +{info['power_bonus']}，花费 {cost} 余额，"
            f"余额 {balance}，当前战力 {power.total}。"
        )

    def _boss_attack(self, msg: dict, chat_id: str, sender_id: str,
                     sender_name: str) -> str:
        result = self._boss.attack(msg)
        if not result.get("ok"):
            return result.get("reply") or "挑战失败，请稍后再试。"

        self._achievements.evaluate(chat_id, sender_id)
        ready = (
            f"{self._format_duration(result['cooldown_left'])}后可再次挑战"
            if result.get("cooldown_left") else "可再次挑战"
        )
        lines = [
            f"{sender_name} 对 {result['boss_name']} 造成 "
            f"{result['damage']} 伤害"
            + ("（暴击！）" if result.get("crit") else ""),
            f"Boss 剩余 {result['hp_left']}/{result['hp_total']}",
            f"今日第 {result['attempts_used']} 次 · {ready}"
            f" · 我的累计伤害 {result['my_damage']}",
        ]
        if result.get("killed"):
            lines.append(
                f"Boss 已被击败！{result['reward_pool']} 余额奖励"
                "已按伤害占比发放。"
            )
            mine = next(
                (row for row in result.get("rewards", [])
                 if row["sender_id"] == sender_id),
                None,
            )
            if mine:
                lines.append(f"你获得 {mine['amount']} 余额。")
        return "\n".join(lines)

    def _boss_status(self, chat_id: str, group_name: str,
                     sender_id: str) -> str:
        state = self._boss.status(chat_id, group_name, sender_id)
        if not state.get("ok"):
            return state.get("reply") or "群 Boss 玩法未开启。"
        lines = [
            f"本周 Boss：{state['boss_name']}（{state['season']}）",
            f"血量 {state['hp_left']}/{state['hp_total']}"
            f" · 参与 {state['participants']} 人"
            f" · 总伤害 {state['total_damage']}",
            f"我的伤害 {state['my_damage']}"
            f" · 今日 {state['attempts_used']} 次"
            f" · 剩余 {state['days_left']} 天",
            f"击杀奖励池 {state['reward_pool']} 余额（按伤害分配）",
        ]
        if state.get("cooldown_left"):
            lines.append(
                f"挑战冷却：{self._format_duration(state['cooldown_left'])}"
                "后可再次挑战"
            )
        else:
            lines.append("现在可以挑战")
        if state.get("killed"):
            lines.append("本周 Boss 已击杀，下周刷新新目标。")
        else:
            lines.append("发送「挑战Boss」参与，发「Boss榜」看伤害排行。")
        return "\n".join(lines)

    def _boss_board(self, chat_id: str, group_name: str) -> str:
        state = self._boss.status(chat_id, group_name, "")
        if not state.get("ok"):
            return state.get("reply") or "群 Boss 玩法未开启。"
        group = _strip_invisible(group_name) or chat_id
        lines = [f"Boss 伤害榜 · {group}（{state['boss_name']}）"]
        rows = state.get("board") or []
        if not rows:
            lines.append("本周还没有人出手，先发「挑战Boss」。")
            return "\n".join(lines)
        for index, row in enumerate(rows, 1):
            lines.append(
                f"{index}. {row['sender_name']} {row['damage']} 伤害"
                f" · {row['attacks']} 次"
            )
        return "\n".join(lines)

    # ── Achievements / titles ─────────────────────────────────────

    def _achievement_progress(self, definition: dict,
                              snapshot: dict) -> str:
        kind = definition.get("kind", "")
        if kind == "species_complete":
            return (f"{snapshot.get('species_count', 0)}/"
                    f"{snapshot.get('species_total', 0)} 种")
        current = int(snapshot.get(kind, 0) or 0)
        target = int(definition.get("value", 0) or 0)
        return f"{min(current, target)}/{target}"

    def _achievements_command(self, chat_id: str, sender_id: str,
                              sender_name: str) -> str:
        result = self._achievements.evaluate(chat_id, sender_id)
        unlocked = self._achievements.unlocked(chat_id, sender_id)
        snapshot = result["snapshot"]
        lines = [
            f"{sender_name} 的成就 {len(unlocked)}/{len(ACHIEVEMENTS)}",
        ]
        if result["new"]:
            lines.append(
                "新解锁：" + "、".join(
                    f"{item['name']}（称号「{item['title']}」，"
                    f"余额 +{item['reward']}）"
                    for item in result["new"]
                )
            )
        if unlocked:
            lines.append(
                "已解锁称号：" + "、".join(
                    item["title"] for item in unlocked)
            )

        locked = [
            item for item in ACHIEVEMENTS
            if item["id"] not in result["unlocked"]
        ]
        if locked:
            lines.append("── 未解锁 ──")
            for item in locked[:6]:
                lines.append(
                    f"· {item['name']}：{item['desc']}"
                    f"（{self._achievement_progress(item, snapshot)}）"
                )
        lines.append("发「称号」查看/佩戴称号。")
        return "\n".join(lines)

    def _titles_command(self, chat_id: str, sender_id: str,
                        sender_name: str) -> str:
        unlocked = self._achievements.unlocked(chat_id, sender_id)
        equipped = self._store.equipped_title(chat_id, sender_id)
        lines = [f"{sender_name} 的称号（已解锁 {len(unlocked)} 个）"]
        if not unlocked:
            lines.append("还没有称号，先发「成就」看看能解锁什么。")
            return "\n".join(lines)
        for item in unlocked:
            mark = " ← 佩戴中" if item["title"] == equipped else ""
            lines.append(f"· {item['title']}（{item['name']}）{mark}")
        lines.append("发送「佩戴称号 名称」切换，例如：佩戴称号 战神")
        return "\n".join(lines)

    def _equip_title(self, chat_id: str, sender_id: str,
                     sender_name: str, raw: str) -> str:
        name = _strip_invisible(raw).strip()
        if not name:
            return "用法：佩戴称号 名称（先发「称号」看已解锁列表）"
        item = achievement_by_title(name)
        if item is None:
            return f"没有称号「{name}」，发「称号」查看可佩戴称号。"
        unlocked = self._store.achievements_of(chat_id, sender_id)
        if item["id"] not in unlocked:
            return f"你还没有解锁称号「{name}」，先发「成就」看看进度。"
        self._store.set_equipped_title(chat_id, sender_id, item["title"])
        return f"{sender_name} 已佩戴称号「{item['title']}」。"

    # ── Admin ─────────────────────────────────────────────────────

    def _admin_adjust(self, chat_id: str, text: str,
                      is_admin: bool) -> str | None:
        if not is_admin:
            return "仅管理员可以调整积分。"
        m = _ADMIN_ADJUST_RE.match(text)
        if not m:
            return "用法：积分调整 名字 +20 [理由]"
        name, delta_raw, note = m.group(1), m.group(2), m.group(3) or ""
        delta = int(delta_raw)
        if delta == 0:
            return "调整分值不能为 0。"

        sid, display, ambiguous = self._resolve_name(chat_id, name)
        if ambiguous:
            return f"本群有多个「{name}」，请改用 wxid 或更完整的昵称。"
        if not sid:
            return f"没找到成员「{name}」。"

        balance = self._store.adjust(
            chat_id, sid, display, delta,
            note=note.strip(),
        )
        sign = "+" if delta > 0 else ""
        suffix = f"（{note.strip()}）" if note.strip() else ""
        return (
            f"已调整 {display} 的积分 {sign}{delta}{suffix}，"
            f"当前余额 {balance}。"
        )

    def _rebuild(self, chat_id: str, is_admin: bool) -> str | None:
        if not is_admin:
            return "仅管理员可以重算积分。"
        if self._engine is None:
            return "积分引擎未初始化，无法重算。"
        result = self._engine.rebuild(chat_id, days=90)
        return (
            f"积分重算完成：扫描 {result['messages']} 条消息，"
            f"清理 {result['removed']} 条旧记录，"
            f"重新计入 {result['restored']} 分。"
        )

    # ── Rules text ────────────────────────────────────────────────

    def _rules(self) -> str:
        cfg = self._config
        return (
            "积分规则\n"
            f"· 每条消息 +{cfg.points_base_score}\n"
            f"· 长度 ≥{cfg.points_length_min_chars} 字 "
            f"+{cfg.points_length_bonus}，"
            f"≥{cfg.points_long_length_min_chars} 字 "
            f"+{cfg.points_long_length_bonus}\n"
            f"· 图片/语音/视频 +{cfg.points_media_bonus}\n"
            f"· 当天首条 +{cfg.points_daily_first_bonus}\n"
            f"· 被引用回复 +{cfg.points_reply_bonus}\n"
            f"· 单条封顶 {cfg.points_per_message_cap} 分，"
            f"每日封顶 {cfg.points_daily_cap} 分\n"
            "· 积分分两类：累计获得决定等级与总榜，"
            "余额用于购买装备和免战券\n"
            f"· 切磋每日 {cfg.duel_daily_attempts} 次，"
            f"单次最多抢 {cfg.duel_per_fight_cap} 分；生涯胜场决定段位\n"
            "· 钓鱼只增加余额，不影响累计积分、等级和榜单\n"
            "· 累计钓鱼条数提升钓鱼等级，额外增加稀有与价值加成\n"
            "· 装备可用「强化 名称」提升，最高 +15（每级 +2 武力）\n"
            "· 满级后可花余额「突破」境界，每重境界永久 +10 武力\n"
            "· 群 Boss 每周刷新，伤害按战力结算，击杀奖励按伤害分配"
        )

    @staticmethod
    def _format_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} 秒"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes / 60
        if hours < 24:
            return f"{hours:.1f} 小时"
        return f"{hours / 24:.1f} 天"


def _ends_with_keyword(text: str, keyword: str) -> bool:
    cleaned = re.sub(rf"{_WS}+$", "", text)
    return cleaned.endswith(keyword)


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
