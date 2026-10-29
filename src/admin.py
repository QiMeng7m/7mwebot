"""Admin command handler — parses and executes admin-only commands.

Commands are only accepted from the configured admin wxid (checked in router).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AdminCommandHandler:
    """Parse and execute admin commands from the bot's admin user.

    Commands (admin only):
      改名 wxid_xxx = 新昵称    → add/update nickname mapping
      删除昵称 wxid_xxx         → remove nickname mapping
      刷新昵称                   → reload from JSON

    Commands (any user):
      帮助 / help / 命令        → show the play list / help text

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
        if content in ("帮助", "help", "命令"):
            return self._help_text(requester_name, config, is_admin)

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

    def _help_text(self, requester_name: str, config,
                   is_admin: bool) -> str:
        """Build the play list shown by 帮助 / help / 命令."""
        duel_attempts = self._cfg_int(config, "duel_daily_attempts", 10)
        duel_cap = self._cfg_int(config, "duel_per_fight_cap", 20)
        shield_price = self._cfg_int(config, "shield_price", 20)
        shield_hours = self._hours(
            self._cfg_int(config, "shield_duration_sec", 14400),
        )
        shield_max_hours = self._hours(
            self._cfg_int(config, "shield_max_sec", 86400),
        )
        fish_hourly = self._cfg_int(config, "fishing_hourly_attempts", 30)
        fish_batch = self._cfg_int(config, "fishing_max_batch", 10)
        fish_daily = self._cfg_int(config, "fishing_daily_cap", 120)
        lottery_cost = self._cfg_int(config, "lottery_cost", 100)
        lottery_attempts = self._cfg_int(
            config, "lottery_daily_attempts", 3)
        boss_cooldown_min = max(
            1, self._cfg_int(config, "boss_cooldown_sec", 1800) // 60,
        )
        boss_max_attempts = max(
            1, self._cfg_int(config, "boss_max_attempts", 3),
        )

        points_on = self._flag(config, "points_enabled")
        lines = [
            f"@{requester_name} 游玩清单（未标 @我 的直接发）",
            "━━━━━━━━━━━━━━",
        ]

        daily: list[str] = []
        if points_on:
            daily.append("  签到                       领取每日签到修炼值")
        if self._flag(config, "fun_enabled"):
            daily.append("  抽签 / 解签                每日一签，AI 帮你解签")
        if points_on and self._flag(config, "lottery_enabled"):
            daily.append(
                f"  抽奖 / 奖池                每日首抽免费，之后 "
                f"{lottery_cost} 灵石/次（每日 {lottery_attempts} 次）"
            )
        if daily:
            lines.append("【每日】")
            lines.extend(daily)
            lines.append("")

        if points_on:
            lines.extend([
                "【修炼值与排行】",
                "  修炼值                       我的修炼值卡",
                "  我的信息                   等级、累计修炼值、灵石",
                "  修炼规则                   修炼值怎么算",
                "  修炼榜 / 月榜 / 今日榜      总榜 / 本赛季 / 今日榜",
                "  查询 昵称                  查看某人的修炼卡",
                "",
            ])

        gear: list[str] = []
        if self._flag(config, "equipment_enabled"):
            gear.extend([
                "  商店                       装备商店",
                "  购买 铁剑                  购买装备",
                "  装备 铁剑 / 卸下 铁剑       穿戴 / 卸下",
                "  出售 铁剑 / 出售 碳素竿      按原价 50% 回收装备或鱼竿",
                "  背包                       我的装备",
                "  战力 / 战力榜              攻击、防御与战力排行",
            ])
        if self._flag(config, "duel_enabled"):
            gear.extend([
                f"  @某人 切磋                  发起切磋（每日 {duel_attempts} 次，"
                f"单次最多抢 {duel_cap} 灵石）",
                "  切磋记录 / 切磋战绩          最近记录 / 段位与生涯战绩",
                "  战神榜                     生涯胜场排行",
                f"  免战                       买免战券（{shield_price} 灵石 / "
                f"{shield_hours} 小时，最多叠到 {shield_max_hours} 小时）",
            ])
        if points_on and gear:
            lines.append("【装备与切磋】")
            lines.extend(gear)
            lines.append("")

        if points_on and self._flag(config, "fishing_enabled"):
            fish_cap_note = (
                f"，收益上限 {fish_daily}/天" if fish_daily else ""
            )
            lines.extend([
                "【钓鱼】",
                f"  钓鱼 / 钓鱼 5              抛竿（每小时 {fish_hourly} 竿，"
                f"一次最多 {fish_batch} 竿{fish_cap_note}）",
                "  鱼竿 / 买鱼竿 碳素竿        鱼竿商店 / 购买",
                "  换鱼竿 碳素竿              切换鱼竿",
                "  鱼饵 / 买鱼饵 神秘饵        鱼饵商店 / 购买",
                "  鱼篓 / 图鉴                 鱼获、钓鱼等级与图鉴",
                "  钓鱼榜 / 钓鱼榜 今日 / 钓鱼榜 大鱼",
                "  钓鱼记录                   我的钓鱼记录",
                "",
            ])

        advanced: list[str] = []
        if self._flag(config, "equipment_enabled"):
            advanced.append("  强化 装备名                 强化装备（最高 +15）")
        if self._flag(config, "boss_enabled"):
            advanced.extend([
                f"  Boss / 挑战Boss / 打Boss    挑战本周群 Boss"
                f"（每 {boss_cooldown_min} 分钟恢复 1 次，"
                f"最多存 {boss_max_attempts} 次）",
                "  Boss状态                   血量、伤害与剩余时间",
                "  Boss榜                     本周伤害排行",
            ])
        advanced.extend([
            "  境界 / 突破                 境界进度 / 修炼值达标突破",
            "  成就 / 称号                 成就进度 / 已解锁称号",
            "  佩戴称号 名称               切换显示称号",
        ])
        if points_on and advanced:
            lines.append("【进阶挑战】")
            lines.extend(advanced)
            lines.append("")

        if points_on:
            lines.extend([
                "【功法与打坐】",
                "  打坐 / 打坐 5 / 打坐5      打坐 10 分钟（可指定 1~60 分钟）",
                "  功法                       查看功法、层数与效果",
                "  学习功法 吐纳诀            花费 500 灵石学习功法",
                "  切换功法 吐纳诀            选择打坐积累进度的功法",
                "  突破功法                   进度满后突破（失败减半，5 次掉层）",
                "",
            ])

        if self._flag(config, "todo_enabled"):
            lines.extend([
                "【待办】",
                "  记一下 周五聚餐             添加待办",
                "  查看待办                   未完成列表",
                "  搞定 3 / 删掉 2             完成 / 删除",
                "  已完成列表                 看已经做完的事",
                "",
            ])

        lines.append("【AI 助手】")
        if self._flag(config, "summarize_enabled"):
            lines.append("  @我 总结一下                总结最近聊天")
        lines.append("  @我 随便聊                 直接对话")

        if is_admin:
            lines.extend([
                "",
                "【管理（仅管理员）】",
                "  改名 wxid_xxx = 昵称       添加 / 修改昵称映射",
                "  删除昵称 wxid_xxx          删除昵称映射",
                "  刷新昵称                   重新加载昵称缓存",
                "  修炼值重算                   重算本群修炼值",
            ])

        lines.append("━━━━━━━━━━━━━━")
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
