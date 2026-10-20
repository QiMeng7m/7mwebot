"""Group text commands for points, equipment and duels.

All commands are plain text (no @ mention needed) and are returned as raw
text so the router skips markdown stripping.  ``查询`` is a *weak* command:
if the name does not resolve to exactly one member the handler returns
None and the message continues to the normal AI path.
"""

from __future__ import annotations

import logging
import re
import time

from . import rules
from .duel import DuelService
from .equipment import EquipmentCatalog
from .levels import LevelTable
from .models import Command
from .store import PointStore
from .util import (
    days_until_season_end,
    is_group_enabled,
    local_day,
    previous_season,
    season_end_day,
)

logger = logging.getLogger(__name__)

_WS = r"[\s ​﻿]"

EXACT_COMMANDS = {
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
    "买免战券": ("buy_shield", ""),
    "免战": ("buy_shield", ""),
}

PREFIX_COMMANDS = ("购买", "装备", "卸下", "查询", "切磋", "积分调整", "积分重算")

_BOARD_SCOPES = {
    "总": "total", "总榜": "total",
    "月": "season", "月榜": "season", "本月": "season",
    "今日": "day", "今日榜": "day", "今天": "day",
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
    if text.startswith("@") and _ends_with_keyword(text, "切磋"):
        return True
    head = re.split(_WS, text, maxsplit=1)[0]
    return head in PREFIX_COMMANDS


def _strip_invisible(text: str) -> str:
    return re.sub(r"[\s ​﻿]+", " ", str(text or "")).strip()


def _clean_for_commands(content: str, bot_name: str = "") -> str:
    """Strip the bot's own @mention but keep the reply-quote prefix."""
    text = str(content or "")
    if bot_name:
        text = re.sub(
            re.escape(f"@{bot_name}") + r"[^\w]*", "", text,
        )
    return _strip_invisible(text)


def _norm_key(name: str) -> str:
    name = _strip_invisible(name)
    name = name.lstrip("@").strip()
    return re.sub(r"\s+", " ", name).casefold()


class PointHandler:
    """Parses and executes all point-system group commands."""

    def __init__(self, store: PointStore, config,
                 levels: LevelTable, catalog: EquipmentCatalog,
                 duels: DuelService, engine=None,
                 nickname_service=None):
        self._store = store
        self._config = config
        self._levels = levels
        self._catalog = catalog
        self._duels = duels
        self._engine = engine
        self._nicks = nickname_service

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
        if kind == "buy_shield":
            return self._buy_shield(chat_id, sender_id, sender_name)
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
        return self._store.member_name(chat_id, sender_id) or sender_id

    def _candidate_map(self, chat_id: str) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        nick_map: dict[str, str] = {}
        if self._nicks is not None:
            try:
                nick_map = self._nicks.load()
            except Exception:  # noqa: BLE001
                nick_map = {}

        for member in self._store.members(chat_id):
            sid = member["sender_id"]
            names = {member["sender_name"]}
            nick = nick_map.get(sid)
            if nick:
                names.add(nick)
            for name in names:
                key = _norm_key(name)
                if not key:
                    continue
                bucket = mapping.setdefault(key, [])
                if sid not in bucket:
                    bucket.append(sid)
        return mapping

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
            return "", "", []
        if len(ids) > 1:
            return "", "", list(ids)
        sid = ids[0]
        return sid, self._display_name(chat_id, sid), []

    def _reply_target(self, chat_id: str, text: str) -> tuple[str, str]:
        """Use the WeChat reply-quote wxid when present."""
        wxid = rules.reply_target(text)
        if not wxid:
            return "", ""
        return wxid, self._display_name(chat_id, wxid)

    # ── Personal views ────────────────────────────────────────────

    def _my_info(self, chat_id: str, sender_id: str,
                 sender_name: str) -> str:
        stats = self._store.member_stats(chat_id, sender_id, sender_name)
        power = self._duels.power(chat_id, sender_id)
        return (
            f"{stats.sender_name} 积分：{stats.earned} "
            f"余额：{stats.balance} 武力值：{power.total}"
        )

    def _member_card(self, chat_id: str, sender_id: str,
                     sender_name: str) -> str:
        stats = self._store.member_stats(chat_id, sender_id, sender_name)
        power = self._duels.power(chat_id, sender_id)
        level = self._levels.for_points(stats.earned)
        nxt = self._levels.next_for(stats.earned)

        lines = [
            f"{stats.sender_name} 积分：{stats.earned} "
            f"· Lv{level['level']} {level['title']}",
            f"余额 {stats.balance} · 本月 {stats.season_points}"
            + (f"（第{stats.season_rank}名）" if stats.season_rank else "")
            + f" · 今日 {stats.day_points}",
            f"武力值 {power.total}（基础 {power.base} + 装备 {power.equipment}）",
        ]
        if nxt is not None:
            lines.append(
                f"距 Lv{nxt['level']} {nxt['title']} 还差 "
                f"{max(0, int(nxt['min_points']) - stats.earned)} 分"
            )
        if stats.streak_days:
            lines.append(f"连续活跃 {stats.streak_days} 天")
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
            mark = "（已装备）" if row.get("equipped") else ""
            by_slot.setdefault(item["slot"], []).append(
                f"{item['name']} +{item['power']}{mark}"
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
            parts.append(
                f"{self._catalog.slot_name(item['slot'])} {item['name']} "
                f"+{item['power']}"
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

        target_id, target_name = self._reply_target(chat_id, text)
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
        result = self._duels.challenge(msg, target_id)
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
                    getattr(self._config, 'duel_loss_cooldown_sec', 300)))}"
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
            f"单次最多抢 {cfg.duel_per_fight_cap} 分"
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
