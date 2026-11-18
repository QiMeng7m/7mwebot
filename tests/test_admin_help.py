"""Tests for the 星盘-only 帮助 play list."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.admin import AdminCommandHandler, is_help_command


def make_config(**overrides):
    values = {
        "koi_enabled": True,
        "todo_enabled": True,
        "summarize_enabled": True,
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

    def test_help_is_star_game_only(self):
        reply = self._help(make_config())
        self.assertTrue(reply.startswith("@张三 游玩清单"))
        self.assertIn("【星盘】", reply)
        self.assertIn("求签", reply)
        self.assertIn("观星", reply)
        self.assertIn("放生", reply)
        self.assertIn("转运", reply)
        self.assertIn("看具体用法：帮助", reply)
        self.assertNotIn("【修炼】", reply)
        self.assertNotIn("【装备】", reply)
        self.assertNotIn("签到", reply)
        self.assertNotIn("管理（仅管理员）", reply)

    def test_help_section_shows_details(self):
        reply = self._help(make_config(), text="帮助 星盘")
        self.assertIn("【星盘】用法", reply)
        self.assertIn("求签 / 解签", reply)
        self.assertIn("观星", reply)
        self.assertIn("四象榜 / 气运榜", reply)
        self.assertNotIn("【待办】", reply)

    def test_help_unknown_section_returns_menu(self):
        reply = self._help(make_config(), text="帮助 未知")
        self.assertIn("游玩清单", reply)

    def test_help_hides_disabled_todo(self):
        cfg = make_config(todo_enabled=False, summarize_enabled=False)
        reply = self._help(cfg)
        self.assertNotIn("【待办】", reply)
        self.assertIn("【星盘】", reply)
        # 直接对话始终可用，所以 AI 区块仍会显示。
        self.assertIn("【AI】", reply)

    def test_admin_sees_admin_section(self):
        reply = self._help(make_config(), is_admin=True)
        self.assertIn("【管理（仅管理员）】", reply)
        self.assertIn("改名", reply)

    def test_help_command_detection(self):
        for text in ("帮助", "help", "命令", "帮助 星盘", " 帮助   待办 "):
            self.assertTrue(is_help_command(text), text)
        for text in ("随便聊聊", "帮助我", "帮助 星盘 多了", ""):
            self.assertFalse(is_help_command(text), text)


if __name__ == "__main__":
    unittest.main()
