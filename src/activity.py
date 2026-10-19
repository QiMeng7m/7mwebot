"""Group activity report — the 「活跃查询」 command.

A bare (or @bot) message equal to ``活跃查询`` / ``活跃查询 7`` /
``活跃查询7天`` produces a plain-text report for the current group:
ranking, per-person daily counts, hourly distribution and dormant members.
"""

import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 15
MAX_DAYS = 90
DISPLAY_TOP_N = 5
MAX_DAILY_LINES = 5
MAX_TIME_BUCKETS = 2
MAX_DORMANT_NAMES = 3
DAILY_NAME_WIDTH = 10
NAME_MAX_WIDTH = 14
MAX_REPORT_CHARS = 4000

_WS = r"\s\u2005\u200b\ufeff"
_ACTIVITY_RE = re.compile(
    rf"^[{_WS}]*活跃查询(?:[{_WS}:：]*(\d+)[{_WS}]*天?)?[{_WS}]*$"
)

_HOUR_BUCKETS = ((0, 3), (4, 7), (8, 11), (12, 15), (16, 19), (20, 23))


# ── Command parsing ──────────────────────────────────────────────────

def parse_activity_query(content: str) -> int | None:
    """Return the requested day count, or None if this is not a command.

    ``活跃查询`` -> DEFAULT_DAYS; ``活跃查询 7`` / ``活跃查询7天`` -> 7.
    Values are clamped to [1, MAX_DAYS].
    """
    if not content:
        return None
    match = _ACTIVITY_RE.match(str(content))
    if match is None:
        return None
    if match.group(1) is None:
        return DEFAULT_DAYS
    try:
        days = int(match.group(1))
    except ValueError:
        return DEFAULT_DAYS
    return max(1, min(days, MAX_DAYS))


def clean_command_text(content: str, bot_name: str = "") -> str:
    """Strip the @bot mention and WeChat's reply-quote prefix.

    Mirrors the @mention cleanup in MessageRouter.handle so the activity
    command can be detected outside the ``if is_at:`` branch.
    """
    text = str(content or "")
    if bot_name:
        text = re.sub(
            re.escape(f"@{bot_name}") + r"[^\w]*", "", text
        ).strip()
    text = re.sub(r"^\s*wxid_[a-zA-Z0-9]+:\s*", "", text)
    return text.strip()


# ── Time helpers ─────────────────────────────────────────────────────

def activity_window_start(days: int, now_ts: int | None = None) -> int:
    """Local midnight of (today - (days - 1)), as Unix seconds."""
    now = int(time.time()) if now_ts is None else int(now_ts)
    days = max(1, min(int(days), MAX_DAYS))
    start = date.fromtimestamp(now) - timedelta(days=days - 1)
    return int(time.mktime(
        (start.year, start.month, start.day, 0, 0, 0, 0, 0, -1)
    ))


def format_relative_time(ts: int, now_ts: int) -> str:
    """Human-readable relative time for the last-message column."""
    delta = max(0, int(now_ts) - int(ts))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{delta // 60}分钟前"
    if delta < 86400:
        return f"{delta // 3600}小时前"
    days = delta // 86400
    if days <= 7:
        return f"{days}天前"
    return time.strftime("%m-%d", time.localtime(int(ts)))


# ── Display helpers ──────────────────────────────────────────────────

def _char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _display_width(text: str) -> int:
    return sum(_char_width(ch) for ch in text)


def _truncate_display(text: str, max_width: int) -> str:
    if _display_width(text) <= max_width:
        return text
    out: list[str] = []
    width = 0
    for ch in text:
        w = _char_width(ch)
        if width + w > max_width - 1:
            break
        out.append(ch)
        width += w
    return "".join(out) + "…"


def _clean_display_name(name) -> str:
    text = str(name or "")
    # Clipboard capture on Windows can leak lone surrogates.
    text = text.encode("utf-8", "replace").decode("utf-8")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "未知"
    return _truncate_display(text, NAME_MAX_WIDTH)


def _resolve_display_name(sender_id: str, stored_name: str,
                          nickname_service) -> str:
    name = ""
    if nickname_service is not None:
        try:
            name = nickname_service.resolve_name(sender_id) or ""
        except Exception:
            name = ""
    if not name or name == sender_id:
        name = stored_name or sender_id
    return _clean_display_name(name)


def _unique_name(name: str, used: dict[str, int]) -> str:
    """Disambiguate duplicate display names as 张三(2), 张三(3)…"""
    used[name] = used.get(name, 0) + 1
    n = used[name]
    return name if n == 1 else f"{name}({n})"


# ── Report building ──────────────────────────────────────────────────

def build_activity_report(
    store,
    nickname_service,
    chat_id: str,
    group_name: str,
    requester_name: str,
    days: int = DEFAULT_DAYS,
    now_ts: int | None = None,
    exclude_message_id: str | None = None,
) -> str:
    """Query the store and format an activity report for one chat."""
    now = int(time.time()) if now_ts is None else int(now_ts)
    days = max(1, min(int(days), MAX_DAYS))
    since_ts = activity_window_start(days, now)
    data = store.get_activity_report_data(
        chat_id, since_ts, exclude_message_id=exclude_message_id,
    )
    return format_activity_report(
        data,
        nickname_service,
        chat_id=chat_id,
        group_name=group_name,
        requester_name=requester_name,
        days=days,
        now_ts=now,
        since_ts=since_ts,
    )


def format_activity_report(
    data: dict,
    nickname_service,
    *,
    chat_id: str,
    group_name: str,
    requester_name: str,
    days: int,
    now_ts: int,
    since_ts: int,
) -> str:
    """Format aggregated store data into the plain-text WeChat reply."""
    senders = data.get("senders") or []
    total = int(data.get("total") or 0)
    daily = data.get("daily") or {}
    hours = list(data.get("hours") or [0] * 24)
    dormant = data.get("dormant") or []

    requester = _clean_display_name(requester_name)
    group = _clean_display_name(group_name or chat_id)
    start_day = date.fromtimestamp(since_ts)
    end_day = date.fromtimestamp(now_ts)

    lines = [
        f"@{requester} 群活跃报告 · {group}",
        f"最近{days}天（{start_day:%m-%d}~{end_day:%m-%d}）| {total}条 | "
        f"{len(senders)}人 | 日均{round(total / days)}条",
    ]

    if total <= 0:
        lines.append("统计范围内暂无消息。")
        return "\n".join(lines)

    # Assign stable, de-duplicated display names.
    used: dict[str, int] = {}
    names: dict[str, str] = {}
    for sender in senders:
        sender_id = sender["sender_id"]
        raw = _resolve_display_name(
            sender_id, sender.get("sender_name", ""), nickname_service,
        )
        names[sender_id] = _unique_name(raw, used)
    for member in dormant:
        sender_id = member["sender_id"]
        if sender_id not in names:
            raw = _resolve_display_name(
                sender_id, member.get("sender_name", ""), nickname_service,
            )
            names[sender_id] = _unique_name(raw, used)

    top = senders[:DISPLAY_TOP_N]

    # ── Ranking ──────────────────────────────────────────────
    lines.append("")
    lines.append(f"发言 TOP{len(top)}")
    for rank, sender in enumerate(top, 1):
        sender_id = sender["sender_id"]
        count = int(sender["message_count"])
        pct = count * 100.0 / total
        last = format_relative_time(int(sender["last_ts"]), now_ts)
        lines.append(
            f"{rank}. {names[sender_id]} {count}条({pct:.0f}%) 最后{last}"
        )
    if len(senders) > len(top):
        rest = senders[len(top):]
        rest_count = sum(int(s["message_count"]) for s in rest)
        lines.append(f"其余 {len(rest)} 人共 {rest_count} 条")

    # ── Daily summary: only days that actually have messages ──
    day_totals: dict[str, int] = {}
    for per_day in daily.values():
        for day, count in per_day.items():
            day_totals[day] = day_totals.get(day, 0) + int(count)
    active_days = sorted(day for day, count in day_totals.items() if count > 0)

    if active_days:
        lines.append("")
        lines.append("每日")
        shown_days = active_days[-MAX_DAILY_LINES:]
        top_ids = {s["sender_id"] for s in top}
        for day in shown_days:
            parts = []
            for sender in top:
                sender_id = sender["sender_id"]
                count = int(daily.get(sender_id, {}).get(day, 0))
                if count:
                    parts.append(
                        f"{_truncate_display(names[sender_id], DAILY_NAME_WIDTH)} {count}"
                    )
            other = sum(
                int(daily.get(sender_id, {}).get(day, 0))
                for sender_id in daily
                if sender_id not in top_ids
            )
            if other:
                parts.append(f"其他 {other}")
            lines.append(
                f"{day[5:]} 共{day_totals[day]}条：" + " · ".join(parts)
            )
        if len(active_days) > len(shown_days):
            lines.append(
                f"（更早的 {len(active_days) - len(shown_days)} 个活跃日未显示）"
            )

    # ── Active hours: only non-empty buckets, top 2 ──────────
    bucket_counts = [
        sum(hours[lo:hi + 1]) if lo < len(hours) else 0
        for lo, hi in _HOUR_BUCKETS
    ]
    ranked_buckets = sorted(
        (
            (lo, hi, count)
            for (lo, hi), count in zip(_HOUR_BUCKETS, bucket_counts)
            if count > 0
        ),
        key=lambda item: (-item[2], item[0]),
    )[:MAX_TIME_BUCKETS]
    if ranked_buckets:
        lines.append("")
        lines.append(
            "活跃时段：" + "、".join(
                f"{lo:02d}-{hi:02d}点 {count}条"
                f"({count * 100.0 / total:.0f}%)"
                for lo, hi, count in ranked_buckets
            )
        )

    # ── Members with no messages in the window (compact) ─────
    if dormant:
        shown = []
        for member in dormant[:MAX_DORMANT_NAMES]:
            sender_id = member["sender_id"]
            name = names.get(sender_id) or _clean_display_name(
                member.get("sender_name") or sender_id
            )
            shown.append(_truncate_display(name, DAILY_NAME_WIDTH))
        suffix = "、".join(shown)
        if len(dormant) > MAX_DORMANT_NAMES:
            suffix += " 等"
        lines.append(f"窗口内未发言：{len(dormant)} 人（{suffix}）")

    report = "\n".join(lines).strip()
    if len(report) > MAX_REPORT_CHARS:
        report = (
            report[:MAX_REPORT_CHARS].rsplit("\n", 1)[0]
            + "\n（报告过长，已截断）"
        )
    return report
