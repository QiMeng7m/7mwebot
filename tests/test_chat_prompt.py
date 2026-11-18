"""AI chat prompt rendering: 星盘/八字 context injection."""

import unittest

from src.summarize.base import AbstractSummarizer


class _CapturingSummarizer(AbstractSummarizer):
    """Minimal backend that records the system prompt instead of calling AI."""

    def __init__(self):
        self.system_prompt = ""

    def _call_chat_api(self, system_prompt, messages):
        self.system_prompt = system_prompt
        return "好的"

    # Unused abstract methods — keep the subclass instantiable.
    def _summarize_direct(self, messages, requester_name):
        return None

    def _summarize_chunk(self, chunk, chunk_num, total, requester_name):
        return ""

    def _merge_chunk_summaries(self, chunk_summaries, requester_name):
        return None

    def consolidate_memory(self, existing_memory, new_messages):
        return existing_memory


class ChatPromptKoiTests(unittest.TestCase):
    def setUp(self):
        self.summarizer = _CapturingSummarizer()

    def test_koi_snapshot_is_injected_into_system_prompt(self):
        snapshot = (
            "身份：本命星 天蝎座｜INFP\n"
            "八字：四柱 乙亥｜金旺｜青龙命\n"
            "今日签：中吉「云开见月」｜宜：聊天"
        )

        self.summarizer.chat(
            "根据我的运势，今天适合做什么",
            requester_name="柒梦",
            group_name="摸鱼大王来啦",
            koi_context=snapshot,
        )

        prompt = self.summarizer.system_prompt
        self.assertIn("根据我的运势，今天适合做什么", prompt)
        self.assertIn("本命星 天蝎座", prompt)
        self.assertIn("今日签：中吉", prompt)
        self.assertNotIn("{koi_section}", prompt)

    def test_prompt_tells_ai_the_astrology_features_are_its_own(self):
        self.summarizer.chat("你好")

        prompt = self.summarizer.system_prompt
        self.assertIn("星盘玩法", prompt)
        self.assertIn("直接根据下面的星盘数据回答", prompt)
        self.assertIn("绝对不要说「我不会看八字」", prompt)

    def test_missing_snapshot_renders_placeholder(self):
        self.summarizer.chat("你好", koi_context=None)

        self.assertIn("（无）", self.summarizer.system_prompt)

    def test_braces_in_snapshot_do_not_break_formatting(self):
        self.summarizer.chat(
            "看看我的运势",
            koi_context="异常数据 {weird} {0}",
        )

        self.assertIn("异常数据 {weird} {0}", self.summarizer.system_prompt)


if __name__ == "__main__":
    unittest.main()
