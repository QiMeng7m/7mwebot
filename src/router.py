"""Message router — receives standardized messages and dispatches to handlers.

Routes messages to four response modes:
1. Admin commands: @bot mention + admin wxid → AdminCommandHandler
2. Summary requests: keyword trigger → summarizer.summarize()
3. AI chat: @bot mention (non-summary) → summarizer.chat()
4. Proactive chat: ambient participation via rate-based gating → summarizer.proactive_chat()
"""

import logging
import re
import time
from typing import Optional

from .proactive.gate import ProactiveGate
from .proactive.sticky import StickyMentionTracker
from .memory.consolidator import MemoryConsolidator
from .todo.store import TodoStore
from .todo.handler import TodoHandler, format_todo_reply
from .activity import (
    build_activity_report,
    clean_command_text,
    parse_activity_query,
)
from .points.util import local_day

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────
CHAT_CONTEXT_WINDOW_SEC = 600      # fetch last N seconds of chat as context for @mentions
MAX_CONTENT_LENGTH = 997           # max chars per message sent to AI (997 + "..." = 1000)
MAX_CONTENT_LINES = 20             # max context lines fed to AI chat prompt
AT_MENTION_MAX_AGE_SEC = 300       # ignore @mentions older than 5 minutes (startup safety)
WELCOME_MAX_AGE_SEC = 300          # ignore join events older than 5 minutes (startup safety)

# Markdown patterns to strip before sending to WeChat.
# These regexes may miss edge cases like nested formatting or asterisks at
# line boundaries.  AI output typically uses simple bold/italic/code/
# strikethrough — these patterns handle 99% of cases.
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_STRIKE = re.compile(r"~~(.+?)~~")
_MD_CODE = re.compile(r"`(.+?)`")


class MessageRouter:
    """Routes incoming WeChat messages to the correct handler.

    Usage:
        router = MessageRouter(
            store=message_store,
            detector=trigger_detector,
            summarizer=summarizer,
            admin_handler=admin_handler,
            nickname_service=nickname_service,
            config=bot_config,
        )

        def on_message(msg: dict) -> str | None:
            return router.handle(msg)
    """

    def __init__(self, store, detector, summarizer, admin_handler,
                 nickname_service, config, feishu_export_service=None):
        """
        Args:
            store: MessageStore instance for persistence and queries.
            detector: TriggerDetector instance for keyword matching.
            summarizer: AbstractSummarizer instance for AI responses.
            admin_handler: AdminCommandHandler instance.
            nickname_service: NicknameService instance.
            config: BotConfig instance.
            feishu_export_service: Optional FeishuExportService instance.
        """
        self._store = store
        self._detector = detector
        self._summarizer = summarizer
        self._admin = admin_handler
        self._nicks = nickname_service
        self._config = config
        self._feishu_export = feishu_export_service
        self._proactive = ProactiveGate(config)
        self._sticky = StickyMentionTracker(
            ttl_sec=config.sticky_mention_ttl_sec,
        ) if config.sticky_mention_enabled else None
        self._memory = MemoryConsolidator(store, summarizer)
        # Todo: init store and handler if feature is enabled
        self._todo_store: Optional[TodoStore] = None
        self._todo_handler: Optional[TodoHandler] = None
        if config.todo_enabled:
            self._todo_store = TodoStore(db_path=config.db_path)
            self._todo_handler = TodoHandler(
                self._todo_store, config,
            )

        # Points / equipment / duels: share the message store's connection
        # so everything runs on one serialized SQLite connection.
        self._points_store = None
        self._points_engine = None
        self._points_handler = None
        if getattr(config, "points_enabled", False) and \
                getattr(store, "conn", None) is not None:
            try:
                from .points.store import PointStore
                from .points.engine import PointEngine
                from .points.levels import LevelTable
                from .points.equipment import EquipmentCatalog
                from .points.duel import DuelService
                from .points.handler import PointHandler

                self._points_store = PointStore(
                    store.conn, lock=getattr(store, "lock", None),
                )
                levels = LevelTable(
                    getattr(config, "points_levels_path",
                            "data/point_levels.json")
                )
                catalog = EquipmentCatalog(
                    getattr(config, "equipment_path", "data/equipment.json")
                )
                self._points_engine = PointEngine(self._points_store, config)
                duels = DuelService(
                    self._points_store, config, levels, catalog,
                )
                self._points_handler = PointHandler(
                    self._points_store, config, levels, catalog, duels,
                    engine=self._points_engine,
                    nickname_service=nickname_service,
                )
            except Exception:
                logger.exception("Points system initialization failed")
                self._points_store = None
                self._points_engine = None
                self._points_handler = None

        # Health monitoring: count unique messages processed (post-dedup)
        self.messages_processed: int = 0

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove markdown formatting characters that WeChat can't render."""
        text = _MD_BOLD.sub(r"\1", text)
        text = _MD_ITALIC.sub(r"\1", text)
        text = _MD_STRIKE.sub(r"\1", text)
        text = _MD_CODE.sub(r"\1", text)
        return text.strip()

    def handle(self, msg: dict) -> Optional[str]:
        """Process an incoming group chat message.

        Returns reply text if a reply should be sent, or None.
        """
        # Skip messages from the bot itself (prevent infinite loops).
        # Use a forgiving match — WeChat display names can vary slightly
        # (extra spaces, punctuation, emoji suffixes) from what's in .env.
        bot_name = self._config.bot_display_name.strip()
        if bot_name and (
            msg["sender_name"].strip() == bot_name
            or bot_name in msg["sender_name"]
        ):
            return None

        # Always persist the message.
        # Wrap in try/except so a DB error (e.g. malformed system message)
        # doesn't block welcome or other downstream processing.
        try:
            stored = self._store.insert_message(msg)
        except Exception:
            logger.exception(
                "Failed to persist message (msg_id=%s, sender=%s)",
                msg.get("message_id", "?"), msg.get("sender_id", "?"),
            )
            stored = False

        if not stored:
            # If it's a join event, still try to welcome — don't let a
            # DB write failure suppress the welcome message.
            if msg.get("is_system_join") and self._config.welcome_enabled:
                msg_age = int(time.time()) - msg.get("timestamp", 0)
                if msg_age <= WELCOME_MAX_AGE_SEC:
                    return self._handle_welcome(msg)
                else:
                    logger.info(
                        "Ignoring stale join event for '%s' (age=%ds, max=%ds)",
                        msg.get("new_member_id", "?"), msg_age, WELCOME_MAX_AGE_SEC,
                    )
            return None  # Duplicate or DB error — nothing more to do
        self.messages_processed += 1

        # ── Award activity points ─────────────────────────────────
        # Runs for every fresh message; idempotent via the ledger's
        # UNIQUE(message_id, reason) and guarded by a staleness check.
        if self._points_engine is not None:
            try:
                self._points_engine.award_for_message(msg)
            except Exception:
                logger.exception(
                    "Point award failed (msg_id=%s, chat=%s)",
                    msg.get("message_id", "?"), msg.get("chat_id", "?"),
                )

        # Check memory consolidation trigger (fast no-op unless threshold hit)
        self._memory.check_and_consolidate(msg["chat_id"])

        # ── Welcome new member ────────────────────────────────────
        if msg.get("is_system_join") and self._config.welcome_enabled:
            msg_age = int(time.time()) - msg.get("timestamp", 0)
            if msg_age <= WELCOME_MAX_AGE_SEC:
                return self._handle_welcome(msg)
            else:
                logger.info(
                    "Ignoring stale join event for '%s' (age=%ds, max=%ds)",
                    msg.get("new_member_id", "?"), msg_age, WELCOME_MAX_AGE_SEC,
                )

        # ── Route: @mention vs proactive ─────────────────────────
        # Sticky mention: if the user previously sent an empty @mention,
        # their next message is treated as if it were @mentioned (one-shot).
        is_at = msg["is_at_mentioned"] or (
            self._sticky is not None
            and self._sticky.consume(msg["chat_id"], msg["sender_id"])
        )

        # ── Activity query (bare keyword or @bot, anyone) ─────────
        # Handled before the @mention split so "活跃查询" works without @.
        # The report is plain text; returning directly skips the markdown
        # stripper so user names are never mangled.
        if msg.get("msg_type") != 10000:
            activity_days = parse_activity_query(
                clean_command_text(msg.get("content", ""), bot_name)
            )
            if activity_days is not None:
                now_ts = int(time.time())
                msg_age_sec = now_ts - msg.get("timestamp", 0)
                if msg_age_sec > AT_MENTION_MAX_AGE_SEC:
                    logger.info(
                        "Ignoring stale activity query from '%s' "
                        "(age=%ds, max=%ds)",
                        msg["sender_name"], msg_age_sec,
                        AT_MENTION_MAX_AGE_SEC,
                    )
                    return None

                requester_name = self._nicks.resolve_name(msg["sender_id"])
                if requester_name == msg["sender_id"]:
                    requester_name = msg["sender_name"]

                logger.info(
                    "Activity query in %s by '%s': days=%d",
                    msg["chat_id"][:20], msg["sender_name"], activity_days,
                )
                try:
                    return build_activity_report(
                        store=self._store,
                        nickname_service=self._nicks,
                        chat_id=msg["chat_id"],
                        group_name=msg.get("group_name") or msg["chat_id"],
                        requester_name=requester_name,
                        days=activity_days,
                        now_ts=now_ts,
                        exclude_message_id=msg.get("message_id"),
                    )
                except Exception:
                    logger.exception("Activity report failed")
                    return (
                        f"@{msg['sender_name']} "
                        "活跃报告生成失败，请稍后再试。"
                    )

        # ── Points / equipment / duel commands (bare text) ────────
        # Weak command: the handler returns None when the text is not
        # actually a command (e.g. "查询一下天气") so normal routing
        # continues untouched.
        if self._points_handler is not None and msg.get("msg_type") != 10000:
            from .points.handler import looks_like_command
            command_text = clean_command_text(msg.get("content", ""), bot_name)
            if looks_like_command(command_text):
                msg_age_sec = int(time.time()) - msg.get("timestamp", 0)
                if msg_age_sec > AT_MENTION_MAX_AGE_SEC:
                    logger.info(
                        "Ignoring stale points command from '%s' "
                        "(age=%ds, max=%ds)",
                        msg["sender_name"], msg_age_sec,
                        AT_MENTION_MAX_AGE_SEC,
                    )
                    return None
                logger.info(
                    "Points command from '%s': %s",
                    msg["sender_name"], command_text[:30],
                )
                try:
                    reply = self._points_handler.handle(
                        msg.get("content", ""), msg,
                    )
                except Exception:
                    logger.exception("Points command failed")
                    reply = None
                if reply is not None:
                    return reply

        if is_at:
            # ── @mention path (existing logic) ───────────────────

            # Guard: ignore stale @mentions, e.g. historical messages
            # replayed on startup when the dedup cache is cold.
            # WeChat message timestamps are Unix seconds.
            msg_age_sec = int(time.time()) - msg.get("timestamp", 0)
            if msg_age_sec > AT_MENTION_MAX_AGE_SEC:
                logger.info(
                    "Ignoring stale @mention from '%s' (age=%ds, max=%ds)",
                    msg["sender_name"], msg_age_sec, AT_MENTION_MAX_AGE_SEC,
                )
                return None

            logger.info(
                "Trigger in %s by '%s': %s",
                msg["chat_id"], msg["sender_name"], msg["content"][:80],
            )

            # Strip the WCDB sender/reply prefix and the bot @mention.
            # The prefix is the member's username, which may be a custom
            # WeChat ID (zy-519, canglan1992, …) rather than wxid_*.
            clean_content = clean_command_text(
                msg["content"], self._config.bot_display_name,
            )

            reply: Optional[str] = None

            # ── Empty @mention → sticky listening mode ──────────────
            # User sent @bot but nothing else.  Register a sticky so
            # their next message (without @mention) still reaches the bot.
            if not clean_content.strip() and self._sticky is not None:
                self._sticky.register(msg["chat_id"], msg["sender_id"])
                logger.info(
                    "Empty @mention from '%s' in %s — sticky listening active for %ds",
                    msg["sender_name"], msg["chat_id"][:20],
                    self._config.sticky_mention_ttl_sec,
                )

            if (
                reply is None
                and self._feishu_export is not None
                and self._feishu_export.is_export_command(clean_content)
            ):
                try:
                    export_msg = dict(msg)
                    export_msg["content"] = clean_content
                    result = self._feishu_export.export_recent_chat(export_msg)
                    reply = result.reply_text
                except Exception:
                    logger.exception("Manual Feishu knowledge sync failed")
                    reply = (
                        f"@{msg['sender_name']} 飞书同步失败了："
                        "AI 总结或飞书写入临时不可用，稍后再试一次。"
                    )

            if reply is None and clean_content.strip() in ("帮助", "help", "命令"):
                reply = self._admin.handle(clean_content, msg["sender_name"])

            if reply is None:
                reply = self._handle_fun_command(msg)

            if reply is None and (
                self._config.admin_wxid
                and msg["sender_id"] == self._config.admin_wxid
            ):
                reply = self._admin.handle(clean_content, msg["sender_name"])

            # ── Todo: group todo commands ───────────────────
            if (
                reply is None
                and self._todo_handler is not None
                and self._is_todo_group(msg["chat_id"])
            ):
                is_admin = (
                    bool(self._config.admin_wxid)
                    and msg["sender_id"] == self._config.admin_wxid
                )
                try:
                    result = self._todo_handler.handle(
                        clean_content,
                        msg["chat_id"],
                        msg["sender_id"],
                        msg["sender_name"],
                        is_admin,
                    )
                    if result is not None:
                        reply = format_todo_reply(result, msg["sender_name"])
                    # 触发自动清理
                    self._todo_store.cleanup(
                        msg["chat_id"],
                        self._config.todo_completed_retention_days,
                        self._config.todo_deleted_retention_days,
                    )
                except Exception:
                    logger.exception("Todo command failed")

            if reply is None and self._detector.is_trigger(
                content=clean_content,
                is_at_mentioned=False,
                sender_name=msg["sender_name"],
            ):
                reply = self._handle_summary(msg)

            if reply is None and clean_content:
                reply = self._handle_chat(msg, clean_content)

        else:
            # ── Bare keyword trigger (no @mention) ────────────────
            # The UI and README promise that sending a trigger keyword
            # (e.g. "总结一下") is enough; only stale messages replayed
            # on startup are ignored.
            msg_age_sec = int(time.time()) - msg.get("timestamp", 0)
            if (
                msg_age_sec <= AT_MENTION_MAX_AGE_SEC
                and self._config.summarize_enabled
                and self._detector.is_trigger(
                    content=msg.get("content", ""),
                    is_at_mentioned=False,
                    sender_name=msg["sender_name"],
                )
            ):
                logger.info(
                    "Bare keyword trigger in %s by '%s'",
                    msg["chat_id"][:20], msg["sender_name"],
                )
                reply = self._handle_summary(msg)
                if reply:
                    return self._strip_markdown(reply)

            if self._feishu_export is not None:
                try:
                    self._feishu_export.maybe_auto_export(msg)
                except Exception:
                    logger.exception("Automatic Feishu knowledge sync failed")

            # ── 抽签 / 解签 (bare text, no @ needed) ──────────────
            if (
                self._config.fun_enabled
                and msg_age_sec <= AT_MENTION_MAX_AGE_SEC
            ):
                fun_reply = self._handle_fun_command(msg)
                if fun_reply:
                    return self._strip_markdown(fun_reply)

            # ── Proactive path (rate-based ambient participation) ─
            should_speak, mode, reason = self._proactive.should_speak(msg)
            if should_speak and mode is not None:
                reply = self._handle_proactive_chat(msg, mode)
            else:
                return None

        # ── Strip markdown — WeChat can't render it ──────────────
        return self._strip_markdown(reply) if reply else None

    # ── Todo helpers ────────────────────────────────────────────

    _group_names_cache: dict[str, str] | None = None
    _group_names_loaded: bool = False

    def _load_group_names(self) -> dict[str, str]:
        """Load chat_id → display_name mapping from disk (lazy + cached)."""
        if not self._group_names_loaded:
            self._group_names_loaded = True
            import json
            from pathlib import Path
            path = Path("data/group_names.json")
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    # group_names.json stores {chat_id: {name, member_count}}
                    # (older versions stored {chat_id: name}); normalise to
                    # chat_id -> display name so callers can compare strings.
                    names: dict[str, str] = {}
                    for chat_id, value in raw.items():
                        if isinstance(value, dict):
                            value = value.get("name", "")
                        if value:
                            names[chat_id] = str(value)
                    self._group_names_cache = names
                except (json.JSONDecodeError, OSError):
                    self._group_names_cache = {}
        return self._group_names_cache or {}

    def _is_todo_group(self, chat_id: str) -> bool:
        """Check if the given chat_id is in the todo allowed-groups list.

        Uses group_names.json (persisted by WcdbBackend) to match
        configured group names against actual chat_ids and their
        display names.
        """
        groups = self._config.todo_groups
        if not groups or groups == ["*"]:
            return True
        # Load display name mapping (chat_id → display_name)
        name_map = self._load_group_names()
        display_name = name_map.get(chat_id, "")
        for g in groups:
            g_lower = g.lower()
            # Exact match against chat_id or display name
            if g == chat_id or g == display_name:
                return True
            # Substring match against chat_id or display name
            if g_lower in chat_id.lower():
                return True
            if display_name and g_lower in display_name.lower():
                return True
        return False

    # ── Memory helper ────────────────────────────────────────────

    def _get_group_memory(self, chat_id: str) -> str:
        """Return the group's memory text, or empty string if none."""
        mem = self._store.get_group_memory(chat_id)
        return mem["memory_text"] if mem else ""

    # ── Summary handler ──────────────────────────────────────────

    def _handle_summary(self, msg: dict) -> str | None:
        """Generate a chat summary for the requester.

        Summary range: from requester's last message to @bot trigger message.
        Requester's own messages are excluded from the summary."""
        if not self._config.summarize_enabled:
            return None

        trigger_ts = msg.get("timestamp", int(time.time()))
        sender_id = msg["sender_id"]
        sender_name = msg["sender_name"]
        chat_id = msg["chat_id"]

        # Find the requester's last message BEFORE this trigger.
        # Uses the messages table directly so the current @bot trigger
        # is excluded. If the most recent prior message is very close
        # (≤30s) it is skipped in favour of an earlier boundary.
        since_ts = self._store.get_user_previous_timestamp(
            chat_id, sender_id, trigger_ts,
        )
        min_window_sec = self._config.fallback_window_hours * 3600

        if since_ts is None:
            since_ts = int(time.time()) - min_window_sec
            logger.info(
                "No prior message from '%s'. Using fallback: last %dh.",
                sender_name, self._config.fallback_window_hours,
            )
        else:
            # Start AFTER the boundary message (exclude the message itself)
            since_ts += 1

        # Safety net: if the resulting window is smaller than min_window,
        # expand it to guarantee a minimum amount of context for the summary.
        actual_window = trigger_ts - since_ts
        if actual_window < min_window_sec:
            expanded_since = trigger_ts - min_window_sec
            logger.info(
                "Summary window too small (%dmin), expanding to %dh minimum.",
                actual_window // 60, self._config.fallback_window_hours,
            )
            since_ts = expanded_since

        raw_messages = self._store.get_messages_since(
            chat_id, since_ts, until_ts=trigger_ts,
            limit=self._config.max_messages_for_summary,
        )

        if len(raw_messages) == 0:
            logger.info("No messages to summarize for %s", chat_id)
            return f"@{sender_name} 这段时间没有新消息。"

        # Exclude requester's own messages from summary content
        messages = [m for m in raw_messages if m["sender_id"] != sender_id]

        if len(messages) == 0:
            logger.info("Only requester's own messages in window for %s", chat_id)
            return f"@{sender_name} 你上条消息之后还没有人说话～"

        msg_count = len(messages)
        time_span_min = (trigger_ts - since_ts) // 60
        logger.info(
            "Summarizing %d messages (excl. requester) over %dmin for '%s' in %s",
            msg_count, time_span_min, sender_name, chat_id,
        )

        try:
            # Pre-resolve wxids and trim long messages
            for m in messages:
                # Resolve custom nickname from file using sender_id (raw wxid).
                # Using sender_name here is wrong: some backends may have
                # already resolved it to a WeChat default name like "暴富蘑菇",
                # and NicknameService can only look up wxid keys, not display names.
                custom = self._nicks.resolve_name(m["sender_id"])
                if custom != m["sender_id"]:
                    m["sender_name"] = custom
                content = self._nicks.resolve_wxids(m.get("content", ""))
                # Trim single messages over 1000 chars to save tokens.
                #
                # Sanitise lone surrogates (U+D800–U+DFFF) that leak from
                # the Windows clipboard.  These corrupt JSON serialization
                # and confuse LLMs.  Always clean — not just for long
                # messages — because even short surrogate-corrupted text
                # breaks the AI API call.
                content = content.encode(
                    "utf-8", errors="surrogateescape",
                ).decode("utf-8", errors="replace")
                if len(content) > MAX_CONTENT_LENGTH:
                    content = content[:MAX_CONTENT_LENGTH] + "..."
                m["content"] = content

            result = self._summarizer.summarize(messages, sender_name)
            reply = self._summarizer.format_summary_for_reply(result, sender_name)
            reply = self._nicks.resolve_wxids(reply)

            logger.info("Summary sent to %s (%d chars)", chat_id, len(reply))
            return reply
        except Exception as e:
            logger.error("Summarization failed: %s", e)
            return (
                f"@{sender_name} "
                f"抱歉，生成总结时出错了，请稍后再试。"
            )

    # ── AI Chat handler ──────────────────────────────────────────

    def _handle_chat(self, msg: dict, clean_content: str) -> str | None:
        """Handle a conversational @bot mention."""
        # Resolve custom nickname from file (via sender_id=wxid),
        # not sender_name which may already be a backend-resolved default.
        display_name = self._nicks.resolve_name(msg["sender_id"])
        if display_name == msg["sender_id"]:
            display_name = msg["sender_name"]

        logger.info(
            "AI chat: '%s' asks '%s'",
            display_name, clean_content[:60],
        )

        # Always fetch recent chat context for @mentions.
        # The bot is @mentioned inside a group conversation — the surrounding
        # chat is almost always relevant.  Keyword-based gating (e.g. "刚才",
        # "之前") is too brittle: natural language has countless ways to
        # reference prior chat without those specific words ("挑一件事评价一下",
        # "怎么看", "那件事", etc.).
        since = int(time.time()) - CHAT_CONTEXT_WINDOW_SEC
        context = self._store.get_messages_since(
            msg["chat_id"], since, limit=20,
        )
        if context:
            for m in context:
                custom = self._nicks.resolve_name(m["sender_id"])
                if custom != m["sender_id"]:
                    m["sender_name"] = custom
            logger.info(
                "Chat context: %d messages for '%s'",
                len(context), display_name,
            )

        try:
            ai_reply = self._summarizer.chat(
                message=clean_content,
                context_messages=context,
                requester_name=display_name,
                bot_name=self._config.bot_display_name,
                group_name=msg.get("group_name", msg.get("chat_id", "群聊")),
                group_memory=self._get_group_memory(msg["chat_id"]),
            )
            ai_reply = self._nicks.resolve_wxids(ai_reply)
            # Guard against empty AI reply — sending a bare @mention is confusing
            if not ai_reply or not ai_reply.strip():
                logger.warning(
                    "AI chat returned empty for '%s' in %s",
                    display_name, msg.get("group_name", msg["chat_id"][:20]),
                )
                return None
            return f"@{display_name} {ai_reply}"
        except Exception as e:
            logger.error("AI chat failed: %s", e)
            return f"@{display_name} 大脑短路了，稍等再试～"

    # ── Fun commands (抽签 / 解签) ───────────────────────────────

    def _msg_display_name(self, msg: dict) -> str:
        """Nickname → sender_id → backend name fallback chain."""
        name = self._nicks.resolve_name(msg["sender_id"])
        if not name or name == msg["sender_id"]:
            name = msg.get("sender_name") or msg["sender_id"]
        return name

    def _handle_fun_command(self, msg: dict) -> str | None:
        """Handle 「抽签」/「解签」 with or without an @mention."""
        if not getattr(self._config, "fun_enabled", False):
            return None
        command = clean_command_text(
            msg.get("content", ""), self._config.bot_display_name,
        )
        if command == "抽签":
            return self._handle_draw(msg)
        if command == "解签":
            return self._handle_read_lot(msg)
        return None

    def _handle_draw(self, msg: dict) -> str:
        from .fun import draw_lot, format_lot

        name = self._msg_display_name(msg)
        day = local_day()

        try:
            existing = self._store.get_fun_draw(
                msg["chat_id"], msg["sender_id"],
            )
        except Exception:
            logger.exception("Loading fortune lot failed")
            existing = None

        if existing and existing.get("day") == day:
            hint = (
                "今天已经解过签啦，明天再来"
                if existing.get("interpreted_at")
                else "发「解签」可以听听 AI 怎么解读"
            )
            return (
                f"{format_lot(name, existing)}\n"
                f"今天已经抽过一签啦，明天再来；{hint}"
            )

        lot = draw_lot()
        try:
            self._store.save_fun_draw(
                msg["chat_id"], msg["sender_id"], name,
                lot["level"], lot.get("emoji", ""), lot["phrase"],
                day=day,
            )
        except Exception:
            logger.exception("Saving fortune lot failed")

        logger.info("Draw lots: %s got %s", name, lot["level"])
        return (
            f"{format_lot(name, lot)}\n"
            "发「解签」可以听听 AI 怎么解读这支签"
        )

    def _handle_read_lot(self, msg: dict) -> str:
        name = self._msg_display_name(msg)
        day = local_day()
        try:
            lot = self._store.get_fun_draw(
                msg["chat_id"], msg["sender_id"],
            )
        except Exception:
            logger.exception("Loading fortune lot failed")
            lot = None

        if not lot or lot.get("day") != day:
            return f"@{name} 今天还没有抽签，先发「抽签」吧"

        try:
            claimed = self._store.claim_fun_reading(
                msg["chat_id"], msg["sender_id"], day,
            )
        except Exception:
            logger.exception("Claiming lot reading failed")
            claimed = False
        if not claimed:
            return f"@{name} 今天已经解过签啦，明天再来~"

        since = int(time.time()) - CHAT_CONTEXT_WINDOW_SEC
        context = self._store.get_messages_since(
            msg["chat_id"], since, limit=20,
        )
        if context:
            for m in context:
                custom = self._nicks.resolve_name(m["sender_id"])
                if custom != m["sender_id"]:
                    m["sender_name"] = custom

        prompt = (
            f"我今天抽到的是「{lot['level']}」签，签文是：{lot['phrase']}。\n"
            "请结合最近的群聊内容，用 2-3 句话帮我解签："
            "说说这支签对今天的工作、心情或人际相处有什么提醒，语气轻松一点。"
        )

        try:
            reading = self._summarizer.chat(
                message=prompt,
                context_messages=context,
                requester_name=name,
                bot_name=self._config.bot_display_name,
                group_name=msg.get("group_name", msg.get("chat_id", "群聊")),
                group_memory=self._get_group_memory(msg["chat_id"]),
            )
            reading = self._nicks.resolve_wxids(reading or "").strip()
            if not reading:
                raise ValueError("empty AI reading")
            logger.info("Lot reading for %s (%d chars)", name, len(reading))
            return f"@{name} {reading}"
        except Exception as e:
            logger.error("Lot reading failed: %s", e)
            # Let the member retry today if the model was unavailable.
            try:
                self._store.release_fun_reading(
                    msg["chat_id"], msg["sender_id"], day,
                )
            except Exception:
                logger.exception("Releasing lot reading failed")
            return (
                f"@{name} 解签先生暂时掉线了。你这支是"
                f"{lot.get('emoji', '')} {lot['level']}：{lot['phrase']}"
            )

    # ── Welcome handler ─────────────────────────────────────────

    def _handle_welcome(self, msg: dict) -> str | None:
        """Send a welcome message for a new group member.

        Resolves the appropriate welcome template for the group,
        replaces ``{new_member}`` with the new member's identifier,
        and returns the final text.  Returns None if the group has
        explicitly disabled welcome or no template matches.
        """
        from .welcome import get_welcome_manager

        new_member = msg.get("new_member_id", "")
        if not new_member:
            logger.warning("Welcome: missing new_member_id in join event")
            return None

        chat_id = msg["chat_id"]
        wm = get_welcome_manager()
        welcome_text = wm.resolve_message(chat_id, new_member)

        if not welcome_text:
            logger.info(
                "Welcome: skipped for '%s' in %s (disabled or no template)",
                new_member, msg.get("group_name", chat_id[:20]),
            )
            return None

        logger.info(
            "Welcome: new member '%s' in %s → '%s'",
            new_member, msg.get("group_name", chat_id[:20]), welcome_text[:40],
        )
        return welcome_text

    # ── Proactive chat handler ────────────────────────────────────

    def _handle_proactive_chat(self, msg: dict, mode) -> str | None:
        """Generate a spontaneous reply based on recent chat context.

        The mode (ProactiveMode) determines:
          - context_count: how many recent messages to fetch
          - max_chars: hard cap on reply length
          - label/description/instruction: injected into AI prompt

        The AI may return an empty string if it judges the conversation
        inappropriate for interjection — no message is sent in that case.
        """
        now = int(time.time())

        # Fetch recent messages — limit to mode's context window
        window_start = now - self._config.proactive_rate_window_sec
        context = self._store.get_messages_since(
            msg["chat_id"], window_start, limit=mode.context_count,
        )

        if not context:
            logger.debug("Proactive: no context available for %s", msg["chat_id"])
            return None

        # Resolve nicknames
        for m in context:
            custom = self._nicks.resolve_name(m["sender_id"])
            if custom != m["sender_id"]:
                m["sender_name"] = custom

        logger.info(
            "Proactive chat: mode=%s context=%d msgs chat=%s",
            mode.name, len(context), msg.get("group_name", msg["chat_id"][:20]),
        )

        try:
            ai_reply = self._summarizer.proactive_chat(
                mode=mode,
                context_messages=context,
                bot_name=self._config.bot_display_name,
                group_name=msg.get("group_name", msg.get("chat_id", "群聊")),
                group_memory=self._get_group_memory(msg["chat_id"]),
            )
        except Exception as e:
            logger.error("Proactive chat API failed: %s", e)
            return None

        if not ai_reply:
            self._proactive.record_eval(msg["chat_id"])
            self._proactive.record_silence(msg["chat_id"])
            consecutive = self._proactive.get_consecutive_silence(msg["chat_id"])
            log_level = logging.WARNING if consecutive >= 3 else logging.INFO
            logger.log(
                log_level,
                "Proactive: AI chose silence (mode=%s consecutive=%d chat=%s)",
                mode.name, consecutive, msg["chat_id"][:20],
            )
            return None

        self._proactive.record_speech(msg["chat_id"])
        ai_reply = self._nicks.resolve_wxids(ai_reply)
        logger.info(
            "Proactive reply: mode=%s len=%d → '%s'",
            mode.name, len(ai_reply), ai_reply[:40],
        )
        # No @prefix — bot speaks as a natural group member
        return ai_reply
