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
TOP_N = 10
MATRIX_MAX_DAYS = 15
MAX_DORMANT = 8
BAR_MAX_WIDTH = 16
MAX_REPORT_CHARS = 4000
NAME_MAX_WIDTH = 14

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


def _pad_display(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


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


def _bar(value: int, maximum: int) -> str:
    if value <= 0 or maximum <= 0:
        return ""
    width = max(1, round(value / maximum * BAR_MAX_WIDTH))
    return " " + "█" * width


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
        f"@{requester} 群活跃报告",
        f"群：{group}",
        f"范围：{start_day:%m-%d} ~ {end_day:%m-%d}（共{days}天）",
    ]

    if total <= 0:
        lines.append("统计范围内暂无消息。")
        return "\n".join(lines)

    lines.append(
        f"消息：{total} 条 | 活跃成员：{len(senders)} 人 | "
        f"日均：{round(total / days)} 条"
    )
    lines.append("")

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

    top = senders[:TOP_N]

    # ── Ranking ──────────────────────────────────────────────
    lines.append(f"一、活跃排行 TOP{len(top)}")
    for rank, sender in enumerate(top, 1):
        sender_id = sender["sender_id"]
        count = int(sender["message_count"])
        pct = count * 100.0 / total
        last = format_relative_time(int(sender["last_ts"]), now_ts)
        lines.append(
            f"{rank}. {names[sender_id]} {count}条({pct:.1f}%) 最后 {last}"
        )
    if len(senders) > TOP_N:
        rest = senders[TOP_N:]
        rest_count = sum(int(s["message_count"]) for s in rest)
        rest_last = max(int(s["last_ts"]) for s in rest)
        lines.append(
            f"（其余 {len(rest)} 人共 {rest_count} 条，"
            f"最近发言 {format_relative_time(rest_last, now_ts)}）"
        )
    lines.append("")

    # ── Daily matrix (most recent MATRIX_MAX_DAYS days) ──────
    matrix_days = min(days, MATRIX_MAX_DAYS)
    first_day = date.fromtimestamp(now_ts) - timedelta(days=matrix_days - 1)
    day_list = [first_day + timedelta(days=i) for i in range(matrix_days)]
    title = f"近{matrix_days}天" if matrix_days != days else f"{days}天"
    lines.append(f"二、每日发言（{title}，列=日期）")
    lines.append(
        " " * (NAME_MAX_WIDTH + 1)
        + "".join(f"{day.day:>3d}" for day in day_list)
    )
    for rank, sender in enumerate(top, 1):
        sender_id = sender["sender_id"]
        cells = "".join(
            f"{int(daily.get(sender_id, {}).get(day.isoformat(), 0)):>3d}"
            for day in day_list
        )
        label = _pad_display(f"{rank}.{names[sender_id]}", NAME_MAX_WIDTH + 1)
        lines.append(label + cells)
    if len(senders) > TOP_N:
        cells = []
        for day in day_list:
            key = day.isoformat()
            count = sum(
                int(daily.get(s["sender_id"], {}).get(key, 0))
                for s in senders[TOP_N:]
            )
            cells.append(f"{count:>3d}")
        lines.append(_pad_display("其他", NAME_MAX_WIDTH + 1) + "".join(cells))
    lines.append("")

    # ── Hourly distribution ──────────────────────────────────
    bucket_counts = [
        sum(hours[lo:hi + 1]) if lo < len(hours) else 0
        for lo, hi in _HOUR_BUCKETS
    ]
    peak = max(bucket_counts) if bucket_counts else 0
    lines.append("三、活跃时段（4小时）")
    for (lo, hi), count in zip(_HOUR_BUCKETS, bucket_counts):
        lines.append(
            f"{lo:02d}-{hi:02d} {count:>5d} 条{_bar(count, peak)}"
        )
    if peak > 0:
        peak_index = bucket_counts.index(peak)
        lo, hi = _HOUR_BUCKETS[peak_index]
        lines.append(
            f"最活跃：{lo:02d}-{hi:02d} 点（{peak * 100.0 / total:.1f}%）"
        )
    lines.append("")

    # ── Members with no messages in the window ───────────────
    if dormant:
        lines.append(f"四、窗口内未发言（{len(dormant)} 人）")
        for member in dormant[:MAX_DORMANT]:
            sender_id = member["sender_id"]
            name = names.get(sender_id) or _clean_display_name(
                member.get("sender_name") or sender_id
            )
            last_ts = int(member.get("last_ts") or 0)
            if last_ts:
                last_day = time.strftime("%m-%d", time.localtime(last_ts))
                days_ago = max(0, (int(now_ts) - last_ts) // 86400)
                lines.append(f"{name}（最后发言 {last_day}，{days_ago}天前）")
            else:
                lines.append(f"{name}（无发言记录）")
        if len(dormant) > MAX_DORMANT:
            lines.append(f"（另有 {len(dormant) - MAX_DORMANT} 人未列出）")

    report = "\n".join(lines).strip()
    if len(report) > MAX_REPORT_CHARS:
        report = (
            report[:MAX_REPORT_CHARS].rsplit("\n", 1)[0]
            + "\n（报告过长，已截断）"
        )
    return report
