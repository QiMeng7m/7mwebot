"""LangChain agent construction.

The agent paradigm is **tool calling** (``create_agent`` in LangChain 1.x,
which compiles to a LangGraph state machine).  We deliberately do not use
the legacy ``AgentExecutor`` / ``AgentType`` enums — they were deprecated
in LangChain 1.0 and removed from ``langchain.agents``.

Model selection reuses the existing .env backend settings, so switching
AI providers does not require any agent-specific configuration.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_CALLS = 4


def build_chat_model(config, temperature: float = 0.7):
    """Create a LangChain chat model from the existing bot config.

    Raises RuntimeError when the selected backend has no API key.
    """
    backend = str(getattr(config, "ai_backend", "") or "").strip().lower()

    if backend == "deepseek":
        from langchain_openai import ChatOpenAI
        api_key = str(getattr(config, "deepseek_api_key", "") or "").strip()
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        return ChatOpenAI(
            model=str(getattr(config, "deepseek_model", "") or "deepseek-chat"),
            api_key=api_key,
            base_url=str(
                getattr(config, "deepseek_base_url", "")
                or "https://api.deepseek.com"
            ),
            temperature=temperature,
            max_retries=0,
            timeout=30,
        )

    if backend == "openai":
        from langchain_openai import ChatOpenAI
        api_key = str(getattr(config, "openai_api_key", "") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return ChatOpenAI(
            model=str(getattr(config, "openai_model", "") or "gpt-4o-mini"),
            api_key=api_key,
            base_url=str(
                getattr(config, "openai_base_url", "")
                or "https://api.openai.com/v1"
            ),
            temperature=temperature,
            max_retries=0,
            timeout=30,
        )

    from langchain_anthropic import ChatAnthropic
    api_key = str(getattr(config, "anthropic_api_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return ChatAnthropic(
        model=str(
            getattr(config, "summarize_model", "") or "claude-haiku-4-5"
        ),
        api_key=api_key,
        base_url=str(
            getattr(config, "anthropic_base_url", "")
            or "https://api.anthropic.com"
        ),
        temperature=temperature,
        max_retries=0,
        timeout=30,
    )


def build_agent(*, config, system_prompt: str, tools: list,
                temperature: float = 0.7):
    """Compile a tool-calling agent for one group."""
    from langchain.agents import create_agent
    from langchain.agents.middleware import (
        ModelCallLimitMiddleware,
        ToolCallLimitMiddleware,
    )

    max_tool_calls = int(
        getattr(config, "agent_max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
        or DEFAULT_MAX_TOOL_CALLS
    )
    max_tool_calls = max(1, min(max_tool_calls, 12))

    model = build_chat_model(config, temperature=temperature)
    middleware = [
        ToolCallLimitMiddleware(run_limit=max_tool_calls),
        # One model call per tool round + a final answer; keep a hard cap
        # so a confused model cannot burn the user's balance.
        ModelCallLimitMiddleware(run_limit=max_tool_calls + 2),
    ]
    return create_agent(
        model=model,
        tools=tools or None,
        system_prompt=system_prompt,
        middleware=middleware,
    )


def extract_final_text(result) -> str:
    """Pull the assistant's final text out of an agent invoke result."""
    messages = []

    if isinstance(result, dict):
        messages = result.get("messages") or []
    elif isinstance(result, (list, tuple)):
        messages = list(result)

    if not messages:
        return ""

    last = messages[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()

    return str(content or "").strip()
