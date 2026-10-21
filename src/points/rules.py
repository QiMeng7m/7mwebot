"""Pure scoring rules — no database, no I/O.

The engine feeds one message in and writes the returned awards out.
Keeping this pure makes the rules table-driven and easy to test.
"""

from __future__ import annotations

import re

# Group messages and reply quotes start with ``<username>:`` followed by
# whitespace/newline.  Usernames are not always ``wxid_*`` — members with
# a custom WeChat ID (``zy-519``, ``canglan1992``, …) appear verbatim.
_USERNAME = r"[A-Za-z0-9_@.\-]{1,64}"
_REPLY_PREFIX_RE = re.compile(rf"^\s*({_USERNAME}):\s+")

# WCDB prefixes every group message with the sender's username.
_WCDB_PREFIX_RE = re.compile(rf"^\s*{_USERNAME}:\s+")

# Spaces (incl. the four-per-em and zero-width variants WeChat inserts)
# used to separate a command from its arguments.
INVISIBLE_WS = r"[\s ​﻿]"
_INVISIBLE_RE = re.compile(r"[\s ​﻿]+")

CHECKIN_COMMAND = "签到"

# msg_type values used by the backends.
MSG_TEXT = 1
MSG_IMAGE = 3
MSG_VOICE = 34
MSG_VIDEO = 43
MSG_STICKER = 47
MSG_SYSTEM = 10000

MEDIA_TYPES = frozenset({MSG_IMAGE, MSG_VOICE, MSG_VIDEO})

# Length bonuses require a minimum amount of variety so that
# "哈哈哈哈哈哈哈哈…" cannot farm them.
MIN_DISTINCT_CHARS = 6


def reply_target(content: str, sender_id: str | None = None) -> str | None:
    """Return the wxid quoted by a reply, or None.

    WCDB prefixes every group message with the *sender's* ``wxid_xxx:``,
    which is indistinguishable from a reply quote by shape alone.  When
    *sender_id* is given, a prefix equal to the sender is treated as the
    ordinary message prefix, not a reply.
    """
    match = _REPLY_PREFIX_RE.match(str(content or ""))
    if not match:
        return None
    target = match.group(1)
    if sender_id and target == str(sender_id):
        return None
    return target


def strip_reply_prefix(content: str) -> str:
    """Remove the ``wxid_xxx:`` reply prefix from the body."""
    return _REPLY_PREFIX_RE.sub("", str(content or ""), count=1)


def strip_invisible(text: str) -> str:
    """Collapse whitespace (including zero-width chars) to single spaces."""
    return _INVISIBLE_RE.sub(" ", str(text or "")).strip()


def clean_command_text(content: str, bot_name: str = "",
                       aliases: tuple[str, ...] | list[str] = ()) -> str:
    """Strip WCDB's ``wxid_xxx:`` prefix and the bot's own @mention.

    *aliases* are additional names the bot answers to (e.g. a WeChat
    group nickname); their @mentions are stripped as well.
    """
    text = _WCDB_PREFIX_RE.sub("", str(content or ""))
    for name in (bot_name, *aliases):
        if name:
            text = re.sub(
                re.escape(f"@{name}") + r"[^\w]*", "", text,
            )
    return strip_invisible(text)


def is_checkin_command(content: str, bot_name: str = "",
                       aliases: tuple[str, ...] | list[str] = ()) -> bool:
    """True when the message body is exactly the 「签到」 command."""
    return clean_command_text(content, bot_name, aliases) == CHECKIN_COMMAND


def normalize_text(text: str) -> str:
    """Drop whitespace/punctuation for duplicate detection."""
    return re.sub(r"[\W_]+", "", str(text or ""), flags=re.UNICODE).lower()


def distinct_char_count(text: str) -> int:
    return len(set(normalize_text(text)))


def score_components(
    content: str,
    msg_type: int,
    *,
    base_score: int = 1,
    length_min_chars: int = 20,
    length_bonus: int = 1,
    long_length_min_chars: int = 100,
    long_length_bonus: int = 1,
    media_bonus: int = 1,
    daily_first_bonus: int = 10,
    per_message_cap: int = 5,
    is_daily_first: bool = False,
) -> list[tuple[str, int]]:
    """Compute the components for one message, already capped.

    Returns a list of ``(reason, points)`` pairs.  ``base`` and the
    length/media bonuses are capped by ``per_message_cap``; the daily
    check-in bonus is exempt (it is awarded once per member per day).
    """
    components: list[tuple[str, int]] = []

    if base_score > 0:
        components.append(("base", int(base_score)))

    running = sum(points for _, points in components)

    def add(reason: str, points: int) -> None:
        nonlocal running
        if points <= 0:
            return
        if running + points > per_message_cap:
            return
        components.append((reason, points))
        running += points

    if msg_type == MSG_TEXT:
        body = strip_reply_prefix(content).strip()
        if distinct_char_count(body) >= MIN_DISTINCT_CHARS:
            length = len(re.sub(r"\s+", "", body))
            if length >= length_min_chars:
                add("length", int(length_bonus))
            if length >= long_length_min_chars:
                add("length_long", int(long_length_bonus))
    elif msg_type in MEDIA_TYPES:
        add("media", int(media_bonus))

    # The per-message cap applies to chat activity only.  The daily
    # check-in bonus (once per member per day) is intentionally exempt —
    # otherwise a small cap would swallow it entirely.
    while sum(points for _, points in components) > per_message_cap \
            and len(components) > 1:
        components.pop()
    if components and components[0][0] == "base":
        reason, points = components[0]
        components[0] = (reason, min(points, per_message_cap))

    if is_daily_first and daily_first_bonus > 0:
        index = 1 if components and components[0][0] == "base" else 0
        components.insert(index, ("daily_first", int(daily_first_bonus)))

    return components
