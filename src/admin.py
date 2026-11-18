"""Admin command handler — parses and executes admin-only commands.

Commands are only accepted from the configured admin wxid (checked in router).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Words that trigger the play list.  A section name may follow, e.g. 帮助 星盘.
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
      帮助 星盘                  → details for one play section

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
        """Help sections for the star-game branch.

        Only the 星盘 gameplay is listed; every old game section is gone.
        """
        sections: list[dict] = [{
            "title": "星盘",
            "keys": ("星盘", "运势", "玩法", "koi"),
            "menu_key": "星盘",
            "items": [
                ("求签 / 解签", "每日一签，得到欧气、签文与今日宜"),
                ("观星", "今日星象、星骰与香火"),
                ("放生", "消耗香火换气运"),
                ("转运", "重抽今天的签（每日 1 次）"),
                ("开光 / 抽符", "塔罗牌鉴，30 抽保底传说牌"),
                ("投池 / 锦鲤池", "投入香火，21:00 开奖"),
                ("点星 青龙", "点亮二十八宿，四象套装与觉醒"),
                ("星盘 / 命格", "本命星盘、四象进度与觉醒"),
                ("周运 / 领取周运", "本周目标与奖励"),
                ("斗星 @某人", "比四象星数，赢 10 香火"),
                ("合盘 @某人", "星座/MBTI 契合度"),
                ("四象榜 / 气运榜", "阵营周榜 / 气运排行"),
                ("群像 / CP榜", "本群 MBTI/星座分布、契合度榜"),
                ("签谱 / 分享", "签文收集 / 今日签分享卡"),
                ("设置八字 1995-08-15", "四柱、五行、天圆地方与四象"),
                ("八字 / 批命", "命盘 / 每日命理解读"),
                ("起卦 / 卦谱", "六十四卦收集"),
                ("我是 天蝎座", "设置本命星（也可 设置生日 10-24）"),
                ("我是 INFP", "设置 MBTI"),
            ],
        }]

        if cls._flag(config, "todo_enabled"):
            sections.append({
                "title": "待办",
                "keys": ("待办", "todo"),
                "menu_key": "待办",
                "items": [
                    ("记一下", "添加待办（如 记一下 周五聚餐）"),
                    ("查看待办", "未完成列表"),
                    ("搞定 / 删掉", "完成 / 删除（如 搞定 3）"),
                    ("已完成列表", "已完成事项"),
                ],
            })

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

        if is_admin:
            sections.append({
                "title": "管理（仅管理员）",
                "keys": ("管理", "admin"),
                "menu_key": "管理",
                "items": [
                    ("改名", "改名 wxid_xxx = 昵称（新增/修改）"),
                    ("删除昵称", "删除昵称 wxid_xxx"),
                    ("刷新昵称", "重新加载昵称缓存"),
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
