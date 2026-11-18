"""Tests for the persona system and the LangChain agent runner."""

import sys
import os
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.personas import (  # noqa: E402
    BUILTIN_PERSONAS,
    DEFAULT_PERSONA,
    Persona,
    persona_choices,
    render_persona_block,
    resolve_persona,
)
from src.agent.runner import AgentRunner  # noqa: E402
from src.summarize.base import AbstractSummarizer  # noqa: E402


class CapturingSummarizer(AbstractSummarizer):
    """Real prompt rendering, no API call."""

    def __init__(self):
        self.system_prompt = ""

    def _call_chat_api(self, system_prompt, messages):
        self.system_prompt = system_prompt
        return "好的"

    def _summarize_direct(self, messages, requester_name):
        return None

    def _summarize_chunk(self, chunk, chunk_num, total, requester_name):
        return ""

    def _merge_chunk_summaries(self, chunk_summaries, requester_name):
        return None

    def consolidate_memory(self, existing_memory, new_messages):
        return existing_memory


def _config(**overrides):
    base = dict(
        ai_backend="deepseek",
        deepseek_api_key="sk-test",
        deepseek_model="deepseek-v4-flash",
        deepseek_base_url="https://api.deepseek.com",
        openai_api_key="",
        openai_model="gpt-4o-mini",
        openai_base_url="https://api.openai.com/v1",
        anthropic_api_key="",
        anthropic_base_url="https://api.anthropic.com",
        summarize_model="claude-haiku-4-5",
        bot_persona="default",
        bot_persona_prompt="",
        agent_enabled=False,
        agent_max_tool_calls=4,
        agent_timeout_sec=20.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeSummarizer:
    is_disabled = False

    def __init__(self):
        self.calls = []

    def build_chat_prompts(self, *, message, persona_section="", **kw):
        return ("SYSTEM:" + persona_section, "USER:" + message)

    def chat(self, *, persona_section="", **kw):
        self.calls.append(persona_section)
        return "fallback-reply"


# ── Persona registry ────────────────────────────────────────────────

class PersonaRegistryTests(unittest.TestCase):
    def test_builtin_personas_include_requested_styles(self):
        self.assertIn("grumpy", BUILTIN_PERSONAS)
        self.assertIn("high_eq", BUILTIN_PERSONAS)
        self.assertIn("default", BUILTIN_PERSONAS)

    def test_unknown_persona_falls_back_to_default(self):
        self.assertIs(resolve_persona("does-not-exist"), DEFAULT_PERSONA)

    def test_lookup_is_case_and_space_insensitive(self):
        self.assertEqual(resolve_persona("  GRUMPY ").id, "grumpy")

    def test_custom_persona_uses_prompt(self):
        persona = resolve_persona("custom", "说话像播音员")
        self.assertEqual(persona.id, "custom")
        self.assertIn("播音员", persona.style)

    def test_free_text_wins_over_preset_id(self):
        # The user writes their own persona; the preset id is irrelevant.
        persona = resolve_persona("grumpy", "说话像播音员")
        self.assertEqual(persona.id, "custom")
        self.assertIn("播音员", persona.style)
        self.assertNotIn("暴躁老哥", persona.style)

    def test_custom_without_prompt_falls_back_to_default(self):
        self.assertIs(resolve_persona("custom", "   "), DEFAULT_PERSONA)

    def test_custom_prompt_is_capped(self):
        persona = resolve_persona("custom", "长" * 5000)
        self.assertLessEqual(len(persona.style), 2000)

    def test_choices_are_serializable_and_include_custom(self):
        choices = persona_choices()
        ids = [c["id"] for c in choices]
        self.assertIn("grumpy", ids)
        self.assertIn("custom", ids)
        for choice in choices:
            self.assertIsInstance(choice["name"], str)
            self.assertIsInstance(choice["summary"], str)
            self.assertIn("template", choice)
        by_id = {c["id"]: c for c in choices}
        self.assertIn("暴躁", by_id["grumpy"]["template"])
        self.assertEqual(by_id["default"]["template"], "")


# ── Prompt rendering ────────────────────────────────────────────────

class PersonaRenderingTests(unittest.TestCase):
    def test_default_persona_renders_nothing(self):
        self.assertEqual(render_persona_block(DEFAULT_PERSONA), "")

    def test_grumpy_block_contains_style_and_examples(self):
        block = render_persona_block(resolve_persona("grumpy"))
        self.assertIn("暴躁老哥", block)
        self.assertIn("说话方式", block)
        self.assertIn("示例对话", block)
        # Safety: persona must explicitly defer to the hard rules.
        self.assertIn("硬底线", block)

    def test_high_eq_block_is_distinct(self):
        grumpy = render_persona_block(resolve_persona("grumpy"))
        high_eq = render_persona_block(resolve_persona("high_eq"))
        self.assertNotEqual(grumpy, high_eq)
        self.assertIn("高情商", high_eq)

    def test_empty_persona_is_safe(self):
        self.assertEqual(render_persona_block(Persona(id="x", name="x", summary="")), "")


# ── Chat prompt injection ───────────────────────────────────────────

class ChatPromptPersonaTests(unittest.TestCase):
    def _summarizer(self):
        return CapturingSummarizer()

    def test_persona_block_is_injected(self):
        summarizer = self._summarizer()
        block = render_persona_block(resolve_persona("grumpy"))
        summarizer.chat("你好", persona_section=block)

        prompt = summarizer.system_prompt
        self.assertIn("暴躁老哥", prompt)
        self.assertNotIn("{persona_section}", prompt)

    def test_default_persona_keeps_old_prompt_shape(self):
        summarizer = self._summarizer()
        summarizer.chat("你好")

        self.assertIn("（无额外设定，按上面的默认风格）", summarizer.system_prompt)

    def test_persona_braces_do_not_break_formatting(self):
        summarizer = self._summarizer()
        summarizer.chat("你好", persona_section="人设 {weird} {0}")
        self.assertIn("人设 {weird} {0}", summarizer.system_prompt)


class ProactivePersonaTests(unittest.TestCase):
    def test_persona_is_injected_into_proactive_prompt(self):
        summarizer = CapturingSummarizer()
        mode = SimpleNamespace(
            label="随口", description="低活跃", instruction="接一句就好",
            max_chars=50, context_count=5,
        )
        summarizer.proactive_chat(
            mode,
            [{"sender_name": "小明", "content": "今天好累"}],
            persona_section=render_persona_block(resolve_persona("grumpy")),
        )
        self.assertIn("暴躁老哥", summarizer.system_prompt)
        self.assertNotIn("{persona_section}", summarizer.system_prompt)


# ── AgentRunner ─────────────────────────────────────────────────────

class AgentRunnerTests(unittest.TestCase):
    def test_disabled_by_default_and_uses_classic_chat(self):
        summarizer = FakeSummarizer()
        runner = AgentRunner(config=_config(), summarizer=summarizer)
        self.assertFalse(runner.enabled)

        reply = runner.chat(
            chat_id="chat@1", message="hi", requester_name="小明",
        )
        self.assertEqual(reply, "fallback-reply")
        self.assertEqual(len(summarizer.calls), 1)

    def test_persona_block_is_forwarded_on_classic_path(self):
        summarizer = FakeSummarizer()
        runner = AgentRunner(
            config=_config(bot_persona="grumpy"), summarizer=summarizer,
        )
        runner.chat(chat_id="chat@1", message="hi")
        self.assertIn("暴躁老哥", summarizer.calls[0])

    def test_enabled_but_build_failure_falls_back(self):
        summarizer = FakeSummarizer()
        runner = AgentRunner(
            config=_config(agent_enabled=True, deepseek_api_key=""),
            summarizer=summarizer,
            store=None,
        )
        # Missing API key makes build_chat_model raise inside the agent path.
        reply = runner.chat(chat_id="chat@1", message="hi")
        self.assertEqual(reply, "fallback-reply")

    def test_disabled_summarizer_disables_agent(self):
        summarizer = FakeSummarizer()
        summarizer.is_disabled = True
        runner = AgentRunner(
            config=_config(agent_enabled=True), summarizer=summarizer,
        )
        self.assertFalse(runner.enabled)


    def test_runner_uses_agent_when_enabled(self):
        from unittest.mock import patch
        from langchain_core.language_models.fake_chat_models import (
            FakeMessagesListChatModel,
        )
        from langchain_core.messages import AIMessage

        class ToolFake(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        class RecordingBuildSummarizer(CapturingSummarizer):
            def build_chat_prompts(self, *args, **kwargs):
                prompt = super().build_chat_prompts(*args, **kwargs)
                self.system_prompt = prompt[0]
                return prompt

        summarizer = RecordingBuildSummarizer()
        runner = AgentRunner(
            config=_config(agent_enabled=True),
            summarizer=summarizer,
            store=_FakeStore(),
        )
        model = ToolFake(responses=[AIMessage(content="来自 agent 的回复")])

        with patch("src.agent.factory.build_chat_model", return_value=model):
            reply = runner.chat(
                chat_id="chat@1",
                message="刚才说到哪了",
                conversation_turns=[{"user": "哪个群", "bot": "摸鱼群"}],
            )

        self.assertEqual(reply, "来自 agent 的回复")
        # The prompt passed to the agent must contain the dialogue turns.
        self.assertIn("群友：哪个群", summarizer.system_prompt)
        self.assertIn("你：摸鱼群", summarizer.system_prompt)


# ── Tools ───────────────────────────────────────────────────────────

class _FakeStore:
    def __init__(self):
        self.searched = []
        self.window = []
        self.memory = {"memory_text": "群里每周五开黑"}

    def search_messages(self, chat_id, keyword, since_ts, limit=20):
        self.searched.append((chat_id, keyword))
        return [{
            "timestamp": 1700000000, "sender_name": "小明",
            "content": f"说过 {keyword}", "sender_id": "wxid_1",
        }]

    def get_messages_since(self, chat_id, since_ts, limit=50):
        self.window.append(chat_id)
        return []

    def get_group_memory(self, chat_id):
        return self.memory


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        from src.agent.tools import build_tools
        self.store = _FakeStore()
        self.tools = build_tools(store=self.store, chat_id="chat@1")
        self.by_name = {t.name: t for t in self.tools}

    def test_tool_set_is_read_only_and_bounded(self):
        self.assertEqual(
            set(self.by_name), {"search_messages", "recent_messages", "group_memory"},
        )

    def test_search_is_scoped_to_the_bound_chat(self):
        out = self.by_name["search_messages"].invoke(
            {"keyword": "火锅", "days": 3},
        )
        self.assertEqual(self.store.searched, [("chat@1", "火锅")])
        self.assertIn("火锅", out)
        self.assertIn("小明", out)

    def test_group_memory_reads_store(self):
        out = self.by_name["group_memory"].invoke({})
        self.assertIn("开黑", out)

    def test_recent_messages_reports_empty_window(self):
        out = self.by_name["recent_messages"].invoke({"minutes": 10})
        self.assertIn("没有人说话", out)

    def test_no_store_yields_no_tools(self):
        from src.agent.tools import build_tools
        self.assertEqual(build_tools(store=None, chat_id="x"), [])


# ── Config loading ──────────────────────────────────────────────────

class PersonaConfigTests(unittest.TestCase):
    def _load(self, **env):
        import os
        from unittest.mock import patch
        base = {
            "AI_ENABLED": "true",
            "AI_BACKEND": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
        }
        base.update(env)
        with patch.dict(os.environ, base, clear=False):
            from src.config import load_config
            return load_config()

    def test_persona_and_agent_defaults(self):
        cfg = self._load()
        self.assertEqual(cfg.bot_persona, "default")
        self.assertEqual(cfg.bot_persona_prompt, "")
        self.assertFalse(cfg.agent_enabled)
        self.assertEqual(cfg.agent_max_tool_calls, 4)
        self.assertEqual(cfg.agent_timeout_sec, 20.0)

    def test_persona_and_agent_are_loaded_from_env(self):
        cfg = self._load(
            BOT_PERSONA="grumpy",
            BOT_PERSONA_PROMPT="自定义",
            AGENT_ENABLED="true",
            AGENT_MAX_TOOL_CALLS="3",
            AGENT_TIMEOUT_SEC="15",
        )
        self.assertEqual(cfg.bot_persona, "grumpy")
        self.assertEqual(cfg.bot_persona_prompt, "自定义")
        self.assertTrue(cfg.agent_enabled)
        self.assertEqual(cfg.agent_max_tool_calls, 3)
        self.assertEqual(cfg.agent_timeout_sec, 15.0)

    def test_agent_limits_are_validated(self):
        with self.assertRaises(RuntimeError):
            self._load(AGENT_MAX_TOOL_CALLS="99")
        with self.assertRaises(RuntimeError):
            self._load(AGENT_TIMEOUT_SEC="1")


# ── Conversation memory (follow-up understanding) ───────────────────

class ConversationMemoryTests(unittest.TestCase):
    def setUp(self):
        from src.memory.conversation import ConversationMemory
        self.mem = ConversationMemory(ttl_sec=60, max_turns=2)

    def test_round_trip(self):
        self.mem.add_turn("c1", "u1", "哪个群", "摸鱼群")
        turns = self.mem.recent_turns("c1", "u1")
        self.assertEqual(turns[0]["user"], "哪个群")
        self.assertEqual(turns[0]["bot"], "摸鱼群")

    def test_isolation_between_members(self):
        self.mem.add_turn("c1", "u1", "A", "a")
        self.mem.add_turn("c1", "u2", "B", "b")
        self.assertEqual(self.mem.recent_turns("c1", "u1")[0]["user"], "A")
        self.assertEqual(self.mem.recent_turns("c1", "u2")[0]["user"], "B")
        self.assertEqual(self.mem.recent_turns("c2", "u1"), [])

    def test_max_turns_drops_oldest(self):
        for i in range(4):
            self.mem.add_turn("c1", "u1", f"q{i}", f"a{i}")
        turns = self.mem.recent_turns("c1", "u1")
        self.assertEqual([t["user"] for t in turns], ["q2", "q3"])

    def test_ttl_expiry(self):
        import time as _time
        self.mem.add_turn("c1", "u1", "old", "reply", ts=_time.time() - 120)
        self.assertEqual(self.mem.recent_turns("c1", "u1"), [])

    def test_empty_text_is_ignored(self):
        self.mem.add_turn("c1", "u1", "", "reply")
        self.assertEqual(len(self.mem), 0)


class ConversationPromptTests(unittest.TestCase):
    def test_turns_are_rendered_in_prompt(self):
        summarizer = CapturingSummarizer()
        summarizer.chat(
            "摸鱼群",
            conversation_turns=[
                {"user": "你想查哪个群？", "bot": "你想查哪个群？"},
                {"user": "哪个群", "bot": "你想查哪个群？"},
            ],
        )
        prompt = summarizer.system_prompt
        self.assertIn("群友：哪个群", prompt)
        self.assertIn("你：你想查哪个群？", prompt)
        self.assertIn("不要把对方的回答当成独立的新话题", prompt)
        self.assertNotIn("{conversation_section}", prompt)

    def test_missing_turns_render_placeholder(self):
        summarizer = CapturingSummarizer()
        summarizer.chat("你好")
        self.assertIn("第一次对话", summarizer.system_prompt)


class RouterConversationFlowTests(unittest.TestCase):
    """Ask → answer without @ must carry the bot's question into context."""

    def setUp(self):
        import tempfile
        from unittest.mock import MagicMock
        from src.admin import AdminCommandHandler
        from src.config import BotConfig
        from src.db.schema import initialize_db
        from src.db.store import MessageStore
        from src.router import MessageRouter
        from src.trigger.detector import TriggerDetector

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)
        self.now = int(time.time())
        self.chat = "123@chatroom"
        self.chat_id_counter = 0

        # sticky must be on so an answer without @ reaches _handle_chat
        self.config = BotConfig()
        self.config.koi_enabled = False
        self.config.sticky_mention_enabled = True
        self.config.admin_wxid = "wxid_admin"

        self.nicks = MagicMock()
        self.nicks.resolve_name.side_effect = lambda sid: sid
        self.nicks.resolve_wxids.side_effect = lambda text: text

        self.summarizer = _SequenceSummarizer(["你想查哪个群？", "摸鱼群啊，好的"])
        self.router = MessageRouter(
            store=self.store,
            detector=TriggerDetector(
                keywords=self.config.trigger_keywords,
                bot_display_name=self.config.bot_display_name,
            ),
            summarizer=self.summarizer,
            admin_handler=AdminCommandHandler(self.nicks),
            nickname_service=self.nicks,
            config=self.config,
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _msg(self, content, is_at=False, sender_id="wxid_a"):
        self.chat_id_counter += 1
        return {
            "message_id": f"m{self.chat_id_counter}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": "张三",
            "content": content,
            "msg_type": 1,
            "timestamp": self.now,
            "is_at_mentioned": is_at,
            "is_group": True,
        }

    def test_answer_without_at_keeps_the_question(self):
        first = self.router.handle(self._msg("帮我查个东西", is_at=True))
        self.assertIn("你想查哪个群", first)

        # No @mention — the sticky armed by the question routes it.
        self.router.handle(self._msg("摸鱼群"))

        self.assertEqual(len(self.summarizer.calls), 2)
        turns = self.summarizer.calls[1]["conversation_turns"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user"], "帮我查个东西")
        self.assertIn("你想查哪个群", turns[0]["bot"])

    def test_memory_is_per_sender(self):
        self.router.handle(self._msg("帮我查个东西", is_at=True))
        # A different member replies; they must not inherit the dialogue.
        self.router.handle(self._msg("摸鱼群", is_at=True, sender_id="wxid_b"))
        self.assertEqual(self.summarizer.calls[1]["conversation_turns"], [])


class _SequenceSummarizer:
    """Returns canned replies in order and records chat kwargs."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def chat(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self._replies.pop(0) if self._replies else "好的"

    def summarize(self, *args, **kwargs):
        return None

    def consolidate_memory(self, *args, **kwargs):
        return ""


# ── Agent graph wiring (fake model, no network) ─────────────────────

class AgentWiringTests(unittest.TestCase):
    """Verify create_agent + middleware + tools actually execute."""

    def _fake_model(self, responses):
        from langchain_core.language_models.fake_chat_models import (
            FakeMessagesListChatModel,
        )

        class ToolFake(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        return ToolFake(responses=responses)

    def test_agent_returns_final_text(self):
        from unittest.mock import patch
        from langchain_core.messages import AIMessage
        from src.agent.factory import build_agent, extract_final_text

        model = self._fake_model([AIMessage(content="你好呀")])
        with patch("src.agent.factory.build_chat_model", return_value=model):
            agent = build_agent(
                config=_config(agent_enabled=True),
                system_prompt="你是测试助手",
                tools=[],
            )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(extract_final_text(result), "你好呀")

    def test_agent_can_call_a_tool_then_answer(self):
        from unittest.mock import patch
        from langchain_core.messages import AIMessage
        from langchain_core.tools import tool
        from src.agent.factory import build_agent, extract_final_text

        calls = []

        @tool
        def ping(x: str) -> str:
            """Ping the service."""
            calls.append(x)
            return "pong:" + x

        model = self._fake_model([
            AIMessage(content="", tool_calls=[{
                "name": "ping", "args": {"x": "a"}, "id": "call_1",
            }]),
            AIMessage(content="完成"),
        ])
        with patch("src.agent.factory.build_chat_model", return_value=model):
            agent = build_agent(
                config=_config(agent_enabled=True),
                system_prompt="你是测试助手",
                tools=[ping],
            )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(calls, ["a"])
        self.assertEqual(extract_final_text(result), "完成")


# ── Result extraction ───────────────────────────────────────────────

class ExtractFinalTextTests(unittest.TestCase):
    def setUp(self):
        from src.agent.factory import extract_final_text
        self.extract = extract_final_text

    def test_string_content(self):
        msg = SimpleNamespace(content="你好")
        self.assertEqual(self.extract({"messages": [msg]}), "你好")

    def test_block_list_content(self):
        msg = SimpleNamespace(content=[
            {"type": "text", "text": "你"},
            {"type": "text", "text": "好"},
        ])
        self.assertEqual(self.extract({"messages": [msg]}), "你好")

    def test_empty_result(self):
        self.assertEqual(self.extract({}), "")
        self.assertEqual(self.extract({"messages": []}), "")


if __name__ == "__main__":
    unittest.main()
