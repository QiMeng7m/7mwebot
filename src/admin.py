"""Admin command handler — parses and executes admin-only commands.

Commands are only accepted from the configured admin wxid (checked in router).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Words that trigger the play list.  A section name may follow, e.g. 帮助 钓鱼.
HELP_WORDS = ("帮助", "help", "命令")


def is_help_command(content: str) -> bool:
    """Return True for 帮助 / help / 命令, optionally followed by one section."""
    parts = content.strip().split()
    return bool(parts) and parts[0] in HELP_WORDS and len(parts) <= 2


class AdminCommandHandler:
    """Parse and execute admin commands from the bot's admin user.

    Commands (admin only):
      改名 wxid_xxx = 新昵称    → add/update nickname mapping
      删除昵称 wxid_xxx         → remove nickname mapping
      刷新昵称                   → reload from JSON

    Commands (any user):
      帮助 / help / 命令        → compact play list
      帮助 钓鱼                  → details for one play section

    Usage:
        handler = AdminCommandHandler(nickname_service)
        reply = handler.handle("改名 wxid_abc = 张三", "AdminName",
                               config=config, is_admin=True)
    """

    def __init__(self, nickname_service):
        """
        Args:
            nickname_service: NicknameService instance for persistence.
        """
        self._nicks = nickname_service

    def handle(self, content: str, requester_name: str,
               config=None, is_admin: bool = False) -> str | None:
        """Parse and execute an admin command. Returns reply text or None.

        Args:
            content: Cleaned message content (no @bot prefix).
            requester_name: Display name of the admin user.
            config: BotConfig used to hide disabled features from the
                help text.  ``None`` shows every section.
            is_admin: Whether the requester is the configured admin, so
                the admin-only command section is included.

        Returns:
            Reply text if the content was a recognized command, else None.
        """
        content = content.strip()

        # ── Help command (any user) ─────────────────────────────
        if is_help_command(content):
            parts = content.split()
            section = parts[1] if len(parts) > 1 else ""
            return self._help_text(requester_name, config, is_admin, section)

        # ── 改名 command ────────────────────────────────────────
        if content.startswith("改名 "):
            return self._cmd_rename(content, requester_name)

        # ── 删除昵称 command ────────────────────────────────────
        if content.startswith("删除昵称 "):
            return self._cmd_delete_nickname(content, requester_name)

        # ── 刷新昵称 command ────────────────────────────────────
        if content.strip() == "刷新昵称":
            self._nicks.load(force=True)
            count = len(self._nicks.load())
            return f"@{requester_name} 昵称缓存已刷新，当前 {count} 条"

        return None

    # ── Help text ──────────────────────────────────────────────────

    @staticmethod
    def _flag(config, name: str) -> bool:
        """Feature flag lookup; missing config means "enabled"."""
        if config is None:
            return True
        return bool(getattr(config, name, True))

    @staticmethod
    def _cfg_int(config, name: str, default: int) -> int:
        if config is None:
            return default
        try:
            return int(getattr(config, name, default))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _hours(cls, seconds: int) -> str:
        hours = seconds / 3600
        return f"{hours:g}"

    @classmethod
    def _sections(cls, config, is_admin: bool) -> list[dict]:
        """Build the enabled play sections.

        Each section dict has ``title`` (shown heading), ``keys`` (aliases
        accepted by ``帮助 <key>``), ``menu_key`` (suggested alias in the
        overview footer) and ``items`` ([(command, description), ...]).
        """
        sections: list[dict] = []
        points_on = cls._flag(config, "points_enabled")

        # ── 每日 ──────────────────────────────────────────────
        daily: list[tuple[str, str]] = []
        if points_on:
            daily.append(("签到", "领取每日签到修炼值"))
        if cls._flag(config, "fun_enabled"):
            daily.append(("抽签 / 解签", "每日一签，AI 帮你解签"))
        if points_on and cls._flag(config, "lottery_enabled"):
            cost = cls._cfg_int(config, "lottery_cost", 100)
            attempts = cls._cfg_int(config, "lottery_daily_attempts", 3)
            daily.append((
                "抽奖 / 奖池",
                f"每日首抽免费，之后 {cost} 灵石/次（每日 {attempts} 次）",
            ))
        if daily:
            sections.append({
                "title": "每日",
                "keys": ("每日", "日常", "daily"),
                "menu_key": "每日",
                "items": daily,
            })

        # ── 修炼 ──────────────────────────────────────────────
        if points_on:
            sections.append({
                "title": "修炼",
                "keys": ("修炼", "排行", "榜"),
                "menu_key": "修炼",
                "items": [
                    ("修炼值", "我的修炼值卡"),
                    ("我的信息", "等级、累计修炼值、灵石"),
                    ("修炼规则", "修炼值怎么算"),
                    ("修炼榜 / 月榜 / 今日榜", "总榜 / 本赛季 / 今日榜"),
                    ("查询 昵称", "查看某人的修炼卡"),
                ],
            })

        # ── 装备 / 切磋 ───────────────────────────────────────
        gear: list[tuple[str, str]] = [
            ("商店", "装备商店"),
            ("购买", "购买装备（如 购买 铁剑）"),
            ("装备 / 卸下", "穿戴或卸下装备（如 装备 铁剑、卸下 铁剑）"),
            ("出售", "按原价 50% 回收装备或鱼竿（如 出售 铁剑）"),
            ("背包", "我的装备"),
            ("战力 / 战力榜", "攻击、防御与战力排行"),
        ]
        if points_on and cls._flag(config, "equipment_enabled"):
            sections.append({
                "title": "装备",
                "keys": ("装备",),
                "menu_key": "装备",
                "items": gear,
            })

        if points_on and cls._flag(config, "duel_enabled"):
            attempts = cls._cfg_int(config, "duel_daily_attempts", 10)
            cap = cls._cfg_int(config, "duel_per_fight_cap", 20)
            price = cls._cfg_int(config, "shield_price", 20)
            hours = cls._hours(
                cls._cfg_int(config, "shield_duration_sec", 14400),
            )
            max_hours = cls._hours(
                cls._cfg_int(config, "shield_max_sec", 86400),
            )
            sections.append({
                "title": "切磋",
                "keys": ("切磋", "对决"),
                "menu_key": "切磋",
                "items": [
                    ("切磋",
                     f"@某人 切磋 发起切磋（每日 {attempts} 次，"
                     f"单次最多抢 {cap} 灵石）"),
                    ("切磋记录 / 切磋战绩", "最近记录 / 段位与生涯战绩"),
                    ("战神榜", "生涯胜场排行"),
                    ("免战",
                     f"买免战券（{price} 灵石 / {hours} 小时，"
                     f"最多叠到 {max_hours} 小时）"),
                ],
            })

        # ── 钓鱼 ──────────────────────────────────────────────
        if points_on and cls._flag(config, "fishing_enabled"):
            hourly = cls._cfg_int(config, "fishing_hourly_attempts", 30)
            batch = cls._cfg_int(config, "fishing_max_batch", 10)
            daily_cap = cls._cfg_int(config, "fishing_daily_cap", 120)
            cap_note = f"，收益上限 {daily_cap}/天" if daily_cap else ""
            sections.append({
                "title": "钓鱼",
                "keys": ("钓鱼", "fish"),
                "menu_key": "钓鱼",
                "items": [
                    ("钓鱼", f"抛竿，可跟竿数（如 钓鱼 5）；每小时 {hourly} 竿，"
                             f"一次最多 {batch} 竿{cap_note}"),
                    ("鱼竿", "鱼竿商店；买鱼竿 碳素竿 购买，"
                             "换鱼竿 碳素竿 切换"),
                    ("鱼饵", "鱼饵商店；买鱼饵 神秘饵 购买"),
                    ("鱼篓 / 图鉴", "鱼获、钓鱼等级与图鉴"),
                    ("钓鱼榜", "总榜 / 今日 / 大鱼"
                               "（钓鱼榜 今日、钓鱼榜 大鱼）"),
                    ("钓鱼记录", "我的钓鱼记录"),
                ],
            })

        # ── 进阶挑战 ──────────────────────────────────────────
        if points_on:
            challenge: list[tuple[str, str]] = []
            if cls._flag(config, "equipment_enabled"):
                challenge.append(("强化", "强化 装备名，最高 +15"))
            if cls._flag(config, "boss_enabled"):
                cooldown = max(
                    1, cls._cfg_int(config, "boss_cooldown_sec", 1800) // 60,
                )
                attempts = max(
                    1, cls._cfg_int(config, "boss_max_attempts", 3),
                )
                challenge.extend([
                    ("Boss", f"Boss / 挑战Boss / 打Boss 挑战本周群 Boss"
                             f"（每 {cooldown} 分钟恢复 1 次，"
                             f"最多存 {attempts} 次）"),
                    ("Boss状态", "血量、伤害与剩余时间"),
                    ("Boss榜", "本周伤害排行"),
                ])
            challenge.extend([
                ("境界 / 突破", "境界进度 / 修炼值达标突破"),
                ("成就 / 称号", "成就进度 / 已解锁称号"),
                ("佩戴称号", "佩戴称号 名称，切换显示称号"),
            ])
            sections.append({
                "title": "进阶挑战",
                "keys": ("挑战", "进阶", "boss"),
                "menu_key": "挑战",
                "items": challenge,
            })

        # ── 功法 ──────────────────────────────────────────────
        if points_on:
            sections.append({
                "title": "功法",
                "keys": ("功法", "打坐"),
                "menu_key": "功法",
                "items": [
                    ("打坐", "打坐 10 分钟（可跟 1~60 分钟，如 打坐 5）"),
                    ("功法", "查看功法、层数与效果"),
                    ("学习功法", "学习功法 吐纳诀，花费 500 灵石"),
                    ("切换功法", "切换功法 吐纳诀，选择攒进度的功法"),
                    ("突破功法", "进度满后突破（失败减半，5 次掉层）"),
                ],
            })

        # ── 待办 ──────────────────────────────────────────────
        if cls._flag(config, "todo_enabled"):
            sections.append({
                "title": "待办",
                "keys": ("待办", "todo"),
                "menu_key": "待办",
                "items": [
                    ("记一下", "添加待办（如 记一下 周五聚餐）"),
                    ("查看待办", "未完成列表"),
                    ("搞定 / 删掉", "完成 / 删除（如 搞定 3、删掉 2）"),
                    ("已完成列表", "看已经做完的事"),
                ],
            })

        # ── AI ────────────────────────────────────────────────
        ai: list[tuple[str, str]] = []
        if cls._flag(config, "summarize_enabled"):
            ai.append(("@我 总结一下", "总结最近聊天"))
        ai.append(("@我 随便聊", "直接对话"))
        sections.append({
            "title": "AI",
            "keys": ("ai", "助手"),
            "menu_key": "AI",
            "items": ai,
        })

        # ── 管理 ──────────────────────────────────────────────
        if is_admin:
            sections.append({
                "title": "管理（仅管理员）",
                "keys": ("管理", "admin"),
                "menu_key": "管理",
                "items": [
                    ("改名", "改名 wxid_xxx = 昵称，添加 / 修改昵称映射"),
                    ("删除昵称", "删除昵称 wxid_xxx，删除昵称映射"),
                    ("刷新昵称", "重新加载昵称缓存"),
                    ("修炼值重算", "重算本群修炼值"),
                ],
            })

        return sections

    def _help_text(self, requester_name: str, config,
                   is_admin: bool, section: str = "") -> str:
        """Build the play list (or one section's details)."""
        sections = self._sections(config, is_admin)

        wanted = section.strip().lower()
        if wanted:
            for entry in sections:
                names = (entry["title"], *entry["keys"])
                if wanted in (name.lower() for name in names):
                    return self._section_text(requester_name, entry)

        return self._menu_text(requester_name, sections)

    @staticmethod
    def _menu_text(requester_name: str, sections: list[dict]) -> str:
        """Compact one-screen overview: one line per play section."""
        lines = [
            f"@{requester_name} 游玩清单（直接发指令）",
            "━━━━━━━━━━━━━━",
        ]
        for entry in sections:
            labels = " · ".join(label for label, _ in entry["items"])
            lines.append(f"【{entry['title']}】 {labels}")
        lines.append("━━━━━━━━━━━━━━")
        keys = " / ".join(entry["menu_key"] for entry in sections)
        lines.append(f"看具体用法：帮助 {keys}")
        return "\n".join(lines)

    @staticmethod
    def _section_text(requester_name: str, entry: dict) -> str:
        """Detailed command list for a single play section."""
        lines = [
            f"@{requester_name}【{entry['title']}】用法",
            "━━━━━━━━━━━━━━",
        ]
        for label, desc in entry["items"]:
            lines.append(f"  {label} — {desc}")
        lines.append("━━━━━━━━━━━━━━")
        lines.append("完整清单：发送 帮助")
        return "\n".join(lines)

    # ── Command implementations ────────────────────────────────────

    def _cmd_rename(self, content: str, requester_name: str) -> str | None:
        """Handle the '改名' command."""
        rest = content[3:].strip()

        # Parse: "wxid_xxx = 新昵称"  or  "wxid_xxx 新昵称"
        if "=" in rest:
            parts = rest.split("=", 1)
        else:
            parts = rest.split(None, 1)  # split on first whitespace

        if len(parts) != 2:
            return None

        wxid = parts[0].strip()
        nickname = parts[1].strip()

        if not wxid or not nickname:
            return None

        self._nicks.update(wxid, nickname)
        return f"@{requester_name} 已更新：{wxid} → {nickname}"

    def _cmd_delete_nickname(self, content: str,
                             requester_name: str) -> str | None:
        """Handle the '删除昵称' command."""
        wxid = content[5:].strip()
        if not wxid:
            return None

        self._nicks.remove(wxid)
        return f"@{requester_name} 已删除：{wxid} 的昵称"
