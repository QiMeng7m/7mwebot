"""Null-object backend used when AI is switched off.

``AI_ENABLED=false`` routes every AI entry point here instead of a real
provider.  Nothing in this module constructs an SDK client or touches the
network, so the bot can run with an expired / missing API key without
incurring any cost.

Callers should check ``config.ai_enabled`` first to give users a friendly
message; the exceptions below are a defense-in-depth safety net.
"""

from .base import AbstractSummarizer
from .models import SummaryResult


class AIDisabledError(RuntimeError):
    """Raised when an AI operation is attempted while AI is switched off."""


class DisabledSummarizer(AbstractSummarizer):
    """No-op summarizer: never calls an AI provider."""

    is_disabled = True

    # ── Conversational chat ────────────────────────────────────────

    def chat(self, message: str, **kwargs) -> str:
        raise AIDisabledError("AI 功能已关闭 (AI_ENABLED=false)")

    def proactive_chat(self, mode, context_messages: list[dict],
                       **kwargs) -> str:
        # Proactive participation is silent by design; never speak.
        return ""

    def _call_chat_api(self, system_prompt: str,
                       messages: list[dict]) -> str:
        raise AIDisabledError("AI 功能已关闭 (AI_ENABLED=false)")

    # ── Summarization ──────────────────────────────────────────────

    def summarize(self, messages: list[dict],
                  requester_name: str) -> SummaryResult:
        raise AIDisabledError("AI 功能已关闭 (AI_ENABLED=false)")

    def _summarize_direct(self, messages: list[dict],
                          requester_name: str) -> SummaryResult:
        raise AIDisabledError("AI 功能已关闭 (AI_ENABLED=false)")

    def _summarize_chunk(self, chunk: list[dict], chunk_num: int,
                         total: int, requester_name: str) -> str:
        raise AIDisabledError("AI 功能已关闭 (AI_ENABLED=false)")

    def _merge_chunk_summaries(self, chunk_summaries: list[str],
                               requester_name: str) -> SummaryResult:
        raise AIDisabledError("AI 功能已关闭 (AI_ENABLED=false)")

    # ── Memory consolidation ───────────────────────────────────────

    def consolidate_memory(self, existing_memory: str,
                           new_messages: list[dict]) -> str:
        # Keep whatever memory already exists; no API call.
        return existing_memory
