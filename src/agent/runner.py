"""AgentRunner — the tool-calling chat path with a safe fallback.

Design rules:
- The agent only replaces the *free chat* path (@bot mention).  Explicit
  commands (抽签/签到/总结/…) stay on the deterministic router.
- Any failure (missing dependency, API error, timeout, empty output) falls
  back to the classic ``summarizer.chat()`` so users always get a reply.
- One agent instance per chat_id; tools are bound to that group.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading

from .personas import Persona, render_persona_block, resolve_persona

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 20.0


class AgentRunner:
    """Runs the LangChain tool-calling agent for @mention chats."""

    def __init__(self, *, config, summarizer, store=None,
                 group_memory_fn=None):
        self._config = config
        self._summarizer = summarizer
        self._store = store
        self._group_memory_fn = group_memory_fn

        self._persona: Persona = resolve_persona(
            getattr(config, "bot_persona", "default"),
            getattr(config, "bot_persona_prompt", ""),
        )
        self._persona_block = render_persona_block(self._persona)

        self._model = None
        self._model_lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="agent",
        )

    # ── Introspection ────────────────────────────────────────────

    @property
    def persona(self) -> Persona:
        return self._persona

    @property
    def persona_block(self) -> str:
        """Rendered persona section; also used by the classic path."""
        return self._persona_block

    @property
    def enabled(self) -> bool:
        """True only when the agent is switched on and AI is available."""
        return self._is_enabled(self._summarizer)

    @property
    def timeout_sec(self) -> float:
        try:
            value = float(
                getattr(self._config, "agent_timeout_sec", DEFAULT_TIMEOUT_SEC)
            )
        except (TypeError, ValueError):
            value = DEFAULT_TIMEOUT_SEC
        return max(5.0, min(value, 120.0))

    # ── Public API ───────────────────────────────────────────────

    def chat(self, *, chat_id: str, message: str,
             context_messages=None, requester_name: str = "",
             bot_name: str = "群聊小助手", group_name: str = "群聊",
             group_memory: str = "", koi_context: str | None = None,
             conversation_turns: list[dict] | None = None,
             summarizer=None) -> str:
        """Answer an @mention, using tools when enabled.

        ``summarizer`` lets the caller inject its *current* summarizer
        (the router's reference can be swapped at runtime, e.g. in tests
        or after a backend reload).
        """
        summarizer = summarizer or self._summarizer
        kwargs = dict(
            message=message,
            context_messages=context_messages,
            requester_name=requester_name,
            bot_name=bot_name,
            group_name=group_name,
            group_memory=group_memory,
            koi_context=koi_context,
            conversation_turns=conversation_turns,
        )

        if not self._is_enabled(summarizer):
            return self._fallback(summarizer=summarizer, **kwargs)

        try:
            text = self._run_agent(
                chat_id=chat_id, summarizer=summarizer, **kwargs,
            )
            if text:
                return text
            logger.warning("Agent returned empty reply; falling back to chat()")
        except Exception as e:
            logger.warning("Agent chat failed (%s); falling back to chat()", e)
            logger.debug("Agent failure detail", exc_info=True)

        return self._fallback(summarizer=summarizer, **kwargs)

    # ── Internals ────────────────────────────────────────────────

    def _is_enabled(self, summarizer) -> bool:
        # ``is True`` avoids treating MagicMock/str values as enabled.
        if getattr(self._config, "agent_enabled", False) is not True:
            return False
        return not bool(getattr(summarizer, "is_disabled", False))

    def _fallback(self, *, summarizer=None, **kwargs) -> str:
        summarizer = summarizer or self._summarizer
        return summarizer.chat(
            persona_section=self._persona_block, **kwargs,
        )

    def _run_agent(self, *, chat_id: str, message: str,
                   context_messages=None, requester_name: str = "",
                   bot_name: str = "群聊小助手", group_name: str = "群聊",
                   group_memory: str = "", koi_context: str | None = None,
                   conversation_turns: list[dict] | None = None,
                   summarizer=None) -> str:
        summarizer = summarizer or self._summarizer
        system_prompt, user_prompt = summarizer.build_chat_prompts(
            message=message,
            context_messages=context_messages,
            requester_name=requester_name,
            bot_name=bot_name,
            group_name=group_name,
            group_memory=group_memory,
            koi_context=koi_context,
            persona_section=self._persona_block,
            conversation_turns=conversation_turns,
        )

        agent = self._build_agent(chat_id=chat_id, system_prompt=system_prompt)

        future = self._executor.submit(
            agent.invoke,
            {"messages": [{"role": "user", "content": user_prompt}]},
        )
        try:
            result = future.result(timeout=self.timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError(
                f"agent timed out after {self.timeout_sec:.0f}s"
            )

        from .factory import extract_final_text
        return extract_final_text(result)

    def _build_agent(self, *, chat_id: str, system_prompt: str):
        from .factory import build_agent
        from .tools import build_tools

        return build_agent(
            config=self._config,
            system_prompt=system_prompt,
            tools=build_tools(
                store=self._store,
                chat_id=chat_id,
                group_memory_fn=self._group_memory_fn,
            ),
            temperature=self._persona.temperature,
        )
