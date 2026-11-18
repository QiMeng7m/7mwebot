"""LangChain agent support for webot.

Public surface:
    from src.agent import resolve_persona, render_persona_block
    from src.agent.runner import AgentRunner

``personas`` has no third-party imports so it is safe to use even when
LangChain is not installed.  Heavy imports live in ``factory``/``runner``
and are performed lazily.
"""

from .personas import (
    BUILTIN_PERSONAS,
    CUSTOM_PERSONA_ID,
    Persona,
    persona_choices,
    render_persona_block,
    resolve_persona,
)

__all__ = [
    "BUILTIN_PERSONAS",
    "CUSTOM_PERSONA_ID",
    "Persona",
    "persona_choices",
    "render_persona_block",
    "resolve_persona",
]
