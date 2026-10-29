"""Tests for the 帮助 / help play list built by AdminCommandHandler."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.admin import AdminCommandHandler, is_help_command


def make_config(**overrides):
    values = {
        "points_enabled": True,
        "fun_enabled": True,
        "equipment_enabled": True,
        "duel_enabled": True,
        "fishing_enabled": True,
        "todo_enabled": True,
        "summarize_enabled": True,
        "duel_daily_attempts": 10,
        "duel_per_fight_cap": 20,
        "shield_price": 20,
        "shield_duration_sec": 14400,
        "shield_max_sec": 86400,
        "fishing_hourly_attempts": 30,
        "fishing_max_batch": 10,
        "fishing_daily_cap": 120,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class AdminHelpTests(unittest.TestCase):
    def setUp(self):
        self.handler = AdminCommandHandler(MagicMock())

    def _help(self, config=None, is_admin=False, text="帮助"):
        return self.handler.handle(
            text, "张三", config=config, is_admin=is_admin,
        )

    def test_help_is_a_compact_overview(self):
        reply = self._help(make_config())

        self.assertTrue(reply.startswith("@张三 游玩清单"))
        for section in (
            "【每日】", "【修炼】", "【装备】", "【切磋】", "【钓鱼】",
            "【进阶挑战】", "【功法】", "【待办】", "【AI】",
        ):
            self.assertIn(section, reply)
        self.assertIn("签到", reply)
        self.assertIn("抽签 / 解签", reply)
        self.assertIn("看具体用法：帮助", reply)
        # One line per section keeps the menu short.
        self.assertLessEqual(len(reply.splitlines()), 14)
        # Admin commands are hidden from regular members.
        self.assertNotIn("管理（仅管理员）", reply)
        self.assertNotIn("修炼值调整", reply)

    def test_help_section_shows_command_details(self):
        reply = self._help(make_config(), text="帮助 钓鱼")

        self.assertIn("【钓鱼】用法", reply)
        self.assertIn("每小时 30 竿", reply)
        self.assertIn("一次最多 10 竿", reply)
        self.assertIn("收益上限 120/天", reply)
        self.assertIn("钓鱼榜", reply)
        # Only the requested section is shown.
        self.assertNotIn("【修炼】", reply)

    def test_help_section_uses_configured_limits(self):
        config = make_config(
            duel_daily_attempts=7,
            duel_per_fight_cap=15,
            shield_price=25,
            shield_duration_sec=7200,
            shield_max_sec=43200,
            fishing_hourly_attempts=12,
            fishing_max_batch=4,
            fishing_daily_cap=80,
        )

        duel = self._help(config, text="帮助 切磋")
        self.assertIn("每日 7 次", duel)
        self.assertIn("单次最多抢 15 灵石", duel)
        self.assertIn("免战券（25 灵石 / 2 小时，最多叠到 12 小时）", duel)

        fishing = self._help(config, text="帮助 钓鱼")
        self.assertIn("每小时 12 竿", fishing)
        self.assertIn("一次最多 4 竿", fishing)
        self.assertIn("收益上限 80/天", fishing)

    def test_help_hides_disabled_features(self):
        config = make_config(
            points_enabled=False,
            fun_enabled=False,
            equipment_enabled=False,
            duel_enabled=False,
            fishing_enabled=False,
            todo_enabled=False,
            summarize_enabled=False,
        )
        reply = self._help(config)

        for hidden in (
            "【每日】", "签到", "抽签", "【修炼】",
            "【装备】", "商店", "【切磋】", "切磋", "【钓鱼】",
            "【待办】", "@我 总结一下", "帮助 钓鱼",
        ):
            self.assertNotIn(hidden, reply)
        # Chat is always available on @mention.
        self.assertIn("@我 随便聊", reply)
        self.assertIn("看具体用法：帮助 AI", reply)
        # Asking for a disabled section falls back to the overview.
        self.assertIn("游玩清单", self._help(config, text="帮助 钓鱼"))

    def test_help_keeps_section_when_only_one_sub_feature_is_on(self):
        config = make_config(
            equipment_enabled=False,
            duel_enabled=True,
        )
        reply = self._help(config)

        self.assertIn("【切磋】", reply)
        self.assertNotIn("装备", reply)
        self.assertNotIn("商店", reply)
        self.assertNotIn("购买", reply)

        detail = self._help(config, text="帮助 切磋")
        self.assertIn("免战", detail)
        self.assertNotIn("装备", detail)

    def test_help_shows_admin_section_for_admin(self):
        reply = self._help(make_config(), is_admin=True)

        self.assertIn("【管理（仅管理员）】", reply)
        self.assertIn("改名", reply)
        self.assertNotIn("修炼值调整", reply)
        self.assertNotIn("灵石调整", reply)

        detail = self._help(make_config(), is_admin=True, text="帮助 管理")
        self.assertIn("改名 wxid_xxx = 昵称", detail)
        self.assertIn("修炼值重算", detail)

    def test_admin_section_hidden_for_regular_member(self):
        reply = self._help(make_config(), text="帮助 管理")

        self.assertNotIn("【管理", reply)
        self.assertIn("游玩清单", reply)

    def test_help_aliases(self):
        for alias in ("帮助", "help", "命令"):
            with self.subTest(alias=alias):
                reply = self.handler.handle(
                    alias, "张三", config=make_config(),
                )
                self.assertIsNotNone(reply)
                self.assertIn("游玩清单", reply)

    def test_help_section_aliases(self):
        for text in ("帮助 每日", "help 钓鱼", "命令 修炼"):
            with self.subTest(text=text):
                reply = self.handler.handle(
                    text, "张三", config=make_config(),
                )
                self.assertIn("】用法", reply)

    def test_help_without_config_shows_all_sections(self):
        reply = self.handler.handle("帮助", "张三")

        self.assertIn("【修炼】", reply)
        self.assertIn("【钓鱼】", reply)
        self.assertIn("【待办】", reply)
        self.assertIn(
            "每日 10 次", self.handler.handle("帮助 切磋", "张三"),
        )
        self.assertIn(
            "每小时 30 竿", self.handler.handle("帮助 钓鱼", "张三"),
        )

    def test_unknown_content_is_not_a_command(self):
        self.assertIsNone(self.handler.handle("随便聊聊", "张三"))
        self.assertIsNone(self.handler.handle("帮助我", "张三"))

    def test_is_help_command(self):
        for text in ("帮助", "help", "命令", "帮助 钓鱼", " 帮助   装备 "):
            self.assertTrue(is_help_command(text))
        for text in ("随便聊聊", "帮助我", "帮助 钓鱼 多了", ""):
            self.assertFalse(is_help_command(text))


if __name__ == "__main__":
    unittest.main()
