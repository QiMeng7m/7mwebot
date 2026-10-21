"""Pure scoring-rule tests."""

import unittest

from src.points.rules import (
    MSG_IMAGE,
    MSG_SYSTEM,
    MSG_TEXT,
    clean_command_text,
    normalize_text,
    reply_target,
    score_components,
    strip_reply_prefix,
)


def _long_text(length: int = 120) -> str:
    """Build a text long enough for the length bonuses with variety."""
    base = "今天天气不错我们一起出去走走吧顺便吃个饭看看电影聊聊最近的事情"
    return (base * ((length // len(base)) + 1))[:length]


class ReplyPrefixTests(unittest.TestCase):
    def test_extracts_wxid(self):
        self.assertEqual(reply_target("wxid_abc123:\n好的"), "wxid_abc123")

    def test_no_prefix(self):
        self.assertIsNone(reply_target("好的"))
        self.assertIsNone(reply_target(""))

    def test_sender_prefix_is_not_a_reply(self):
        # WCDB prefixes every message with the sender's wxid.
        self.assertIsNone(
            reply_target("wxid_abc123:\n切磋", sender_id="wxid_abc123")
        )
        self.assertEqual(
            reply_target("wxid_abc123:\n切磋", sender_id="wxid_other"),
            "wxid_abc123",
        )

    def test_strip_prefix(self):
        self.assertEqual(
            strip_reply_prefix("wxid_abc:\n" + _long_text(20)),
            _long_text(20),
        )

    def test_custom_wechat_id_prefix(self):
        """Members with a custom WeChat ID use it as the message prefix."""
        self.assertEqual(reply_target("zy-519:\n好的"), "zy-519")
        self.assertEqual(
            reply_target("zy-519:\n切磋", sender_id="zy-519"), None,
        )
        self.assertEqual(
            strip_reply_prefix("canglan1992:\n" + _long_text(20)),
            _long_text(20),
        )

    def test_clean_command_text_strips_custom_id(self):
        self.assertEqual(
            clean_command_text("zy-519:\n@柒梦 签到", "柒梦"), "签到",
        )
        self.assertEqual(
            clean_command_text("canglan1992:\n积分"), "积分",
        )

    def test_normalize_removes_punctuation_and_space(self):
        self.assertEqual(normalize_text("你好， 世界！"), "你好世界")
        self.assertEqual(normalize_text("Hello, World!"), "helloworld")


class ScoreComponentTests(unittest.TestCase):
    def test_short_text_only_base(self):
        self.assertEqual(score_components("好的", MSG_TEXT), [("base", 1)])

    def test_medium_text_gets_length_bonus(self):
        text = "今天一起去吃饭吧顺便看看最近的新电影怎么样"
        self.assertGreaterEqual(len(text), 20)
        components = dict(score_components(text, MSG_TEXT))
        self.assertEqual(components["base"], 1)
        self.assertEqual(components["length"], 1)
        self.assertNotIn("length_long", components)

    def test_long_text_gets_both_bonuses(self):
        components = dict(score_components(_long_text(120), MSG_TEXT))
        self.assertEqual(components["length"], 1)
        self.assertEqual(components["length_long"], 1)

    def test_repeated_characters_do_not_earn_length(self):
        components = dict(score_components("哈" * 200, MSG_TEXT))
        self.assertNotIn("length", components)
        self.assertNotIn("length_long", components)

    def test_media_bonus(self):
        components = dict(score_components("", MSG_IMAGE))
        self.assertEqual(components["base"], 1)
        self.assertEqual(components["media"], 1)

    def test_daily_first_bonus(self):
        components = dict(score_components(
            "好的", MSG_TEXT, is_daily_first=True,
        ))
        self.assertEqual(components["daily_first"], 10)
        self.assertEqual(sum(components.values()), 11)

    def test_daily_bonus_is_exempt_from_message_cap(self):
        components = dict(score_components(
            "好的", MSG_TEXT, per_message_cap=5, is_daily_first=True,
        ))
        self.assertEqual(components["base"], 1)
        self.assertEqual(components["daily_first"], 10)

    def test_cap_drops_bonuses(self):
        components = score_components(
            _long_text(120), MSG_TEXT,
            daily_first_bonus=2, per_message_cap=4, is_daily_first=True,
        )
        chat_points = sum(p for reason, p in components
                          if reason != "daily_first")
        self.assertLessEqual(chat_points, 4)
        self.assertEqual(components[0], ("base", 1))
        self.assertEqual(components[1], ("daily_first", 2))

    def test_tiny_cap_only_limits_chat_points(self):
        components = dict(score_components(
            _long_text(120), MSG_TEXT,
            per_message_cap=1, is_daily_first=True,
        ))
        self.assertEqual(components, {"base": 1, "daily_first": 10})

    def test_system_messages_not_scored_by_caller(self):
        # rules itself is stateless; engine filters system messages.
        self.assertEqual(MSG_SYSTEM, 10000)


if __name__ == "__main__":
    unittest.main()
