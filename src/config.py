"""Configuration loading from .env file."""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv


def _decode_wechat_groups(raw: str) -> str:
    """Decode URL-encoded group names from .env WECHAT_GROUPS value.

    We store each group name URL-encoded (via encodeURIComponent / urllib.parse.quote)
    so that commas, equals signs, and newlines in real group names don't break the
    .env format or our comma-separated delimiter.  This function reverses that encoding
    with a fallback: if decoding a chunk doesn't change it (or raises), the original
    is kept — for backward compatibility with old unencoded .env files.
    """
    if not raw or raw.strip() == "*":
        return raw.strip() if raw else "*"
    decoded = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            d = unquote(chunk)
            decoded.append(d)
        except Exception:
            decoded.append(chunk)
    return ",".join(decoded) if decoded else "*"


def _sanitize_display_name(name: str) -> str:
    """Remove only truly dangerous characters from a display name.

    This preserves the user's actual name (including quotes, braces, etc.)
    and relies on *usage-point escaping* (``repr()`` in logs, ``_esc()``
    in ``str.format()`` calls) to prevent injection at each call site.

    Only stripped:
    - Control characters (CR/LF → log-line injection)
    - Leading/trailing whitespace + quotes (almost certainly accidental)
    - Excessive length (> 128 chars)
    """
    if not name:
        return "群聊小助手"

    # 1. Strip control chars (except space) — prevents log-line injection
    name = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", name)

    # 2. Collapse whitespace and strip
    name = re.sub(r"\s+", " ", name).strip()

    # 3. Truncate to reasonable length
    if len(name) > 128:
        name = name[:128]

    # 4. Fallback
    if not name:
        return "群聊小助手"

    return name


def _parse_bot_aliases(raw: str) -> list[str]:
    """Parse ``BOT_ALIASES`` (comma-separated) into clean names.

    Supports both ASCII and full-width commas; empty entries are dropped.
    """
    aliases: list[str] = []
    for part in str(raw or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        cleaned = _sanitize_display_name(part)
        if cleaned and cleaned not in aliases:
            aliases.append(cleaned)
    return aliases


def _resolve_project_root() -> Path:
    app_home = os.getenv("WEBOT_APP_HOME", "").strip()
    if app_home:
        return Path(app_home).expanduser().resolve()

    # In a PyInstaller EXE, __file__ resolves inside the temp extraction dir.
    # We want writable data (.env, data/*) in the EXE directory, not in the temp dir.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _resolve_project_root()


def resolve_env_file() -> Path:
    """Return the canonical .env path — the single source of truth.

    Does NOT require the file to exist.  Every read AND write of .env must
    resolve through this function so config never splits across two files
    (the original persistence bug came from reads checking one directory
    while writes created .env in the current working directory).

    Precedence:
      1. WEBOT_ENV_FILE (explicit override, e.g. the macOS bundle)
      2. PROJECT_ROOT / ".env" (EXE dir when frozen, project root in dev)
    """
    explicit_env = os.getenv("WEBOT_ENV_FILE", "").strip()
    if explicit_env:
        return Path(explicit_env).expanduser().resolve()
    return PROJECT_ROOT / ".env"


def find_env_file() -> Path | None:
    """Return the .env path that actually exists, or None.

    The canonical location is resolve_env_file().  For backward
    compatibility with older installs whose .env landed in the working
    directory, a read-only fallback to CWD/.env is kept — but writes always
    go to resolve_env_file().
    """
    canonical = resolve_env_file()
    if canonical.exists():
        return canonical

    legacy = Path.cwd() / ".env"
    if legacy != canonical and legacy.exists():
        return legacy

    return None


_env_path = find_env_file()

if _env_path:
    load_dotenv(_env_path)
else:
    load_dotenv()

# Log which .env was loaded (helpful for debugging EXE packaging issues)
import logging as _logging
_log = _logging.getLogger(__name__)
if _env_path:
    _log.info("Loaded .env from: %s", _env_path)
else:
    _log.warning(
        ".env not found (canonical: %s). Using defaults.", resolve_env_file()
    )


@dataclass
class BotConfig:
    """All configuration for the WeChat summarizer bot."""

    # === AI Backend ===
    # "claude" | "deepseek" | "openai"
    ai_backend: str = "claude"

    # === Claude (Anthropic) ===
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    summarize_model: str = "claude-haiku-4-5-20251001"

    # === DeepSeek ===
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"

    # === OpenAI (compatible) ===
    # Any OpenAI-compatible provider: OpenAI / GLM (Zhipu) / Moonshot / Qwen ...
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # === WeChat Backend ===
    wechat_backend: str = "wcdb"
    # Comma-separated group names to monitor. "*" = auto-discover all groups.
    wechat_groups: str = "*"
    # Custom WeChat data directory. Leave empty to auto-detect from Documents.
    # Set to the parent directory containing wxid_* folders (e.g. D:\WeChatData).
    wechat_data_dir: str = ""

    # === Bot Identity ===
    bot_display_name: str = "群聊小助手"
    # Additional names the bot answers to (e.g. a WeChat group nickname).
    bot_aliases: list[str] = field(default_factory=list)
    # Admin wxid (can manage nicknames and bot settings)
    admin_wxid: str = ""

    # === Trigger Keywords ===
    trigger_keywords: list[str] = field(default_factory=lambda: [
        "总结一下", "之前发了什么", "错过了什么", "summarize",
        "what did i miss", "聊天总结", "帮我总结", "前面说了什么",
        "说了啥", "发生了什么",
    ])

    # === Summarization ===
    summarize_enabled: bool = True
    fallback_window_hours: int = 8
    # API retry attempts (1-10) for summarizer/chat calls.
    max_retries: int = 3

    # === Feishu / Lark Export ===
    feishu_export_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # knowledge, spreadsheet, bitable, or docx
    feishu_export_mode: str = "knowledge"
    feishu_export_window_hours: int = 8
    feishu_auto_sync_enabled: bool = False
    feishu_auto_sync_min_messages: int = 20
    feishu_auto_sync_cooldown_sec: int = 1800
    feishu_knowledge_base_name: str = "webot 群聊沉淀"
    feishu_knowledge_folder_token: str = ""
    feishu_export_trigger_keywords: list[str] = field(default_factory=lambda: [
        "同步到飞书", "导出到飞书", "写到飞书", "沉淀到飞书",
    ])
    feishu_spreadsheet_token: str = ""
    feishu_spreadsheet_range: str = "Sheet1!A:H"
    feishu_bitable_app_token: str = ""
    feishu_bitable_table_id: str = ""
    feishu_doc_folder_token: str = ""

    # === Database ===
    db_path: str = "data/messages.db"

    # === Fun Features ===
    # @抽签 — draw a fortune lot (大吉/中吉/小吉/末吉/凶)
    fun_enabled: bool = True

    # === Proactive Participation ===
    # Master switch — enable autonomous chat participation without @mention
    proactive_enabled: bool = False
    # Rate thresholds for each mode (msgs/min).  When the message rate
    # exceeds a threshold, the bot enters that mode.  Calibrate these
    # by running:  python tools/analyze_chat_rhythm.py
    proactive_rate_window_sec: int = 120  # rate calculation window
    proactive_rate_quiet: float = 1.5     # SLEEP → QUIET  boundary
    proactive_rate_casual: float = 4.0    # QUIET → CASUAL boundary
    proactive_rate_lively: float = 6.5    # CASUAL → LIVELY boundary
    proactive_rate_burst: float = 8.5     # LIVELY → BURST  boundary

    # === Welcome New Member ===
    # Master switch — automatically send a welcome message when a new
    # member joins a group chat.  Welcome templates and per-group settings
    # are stored in data/welcome_templates.json.
    welcome_enabled: bool = False

    # === Inactive Member Kick ===
    # Remove a newly joined member who never speaks within N minutes.
    # Requires the logged-in account to have admin/owner rights.
    inactive_kick_enabled: bool = False
    inactive_kick_minutes: int = 10  # 1-1440

    # === Sticky Mention ===
    # When a user sends @bot with no message text, enter sticky listening
    # mode.  The user's next message in the same group is treated as if it
    # were @mentioned (one-shot).  Set enabled=false to disable entirely.
    sticky_mention_enabled: bool = True
    sticky_mention_ttl_sec: int = 60  # max wait for follow-up message

    # === Group Todo ===
    # Master switch — @bot mention triggers todo management commands.
    todo_enabled: bool = True
    # Comma-separated group names where todo is active. "*" = all groups.
    todo_groups: list[str] = field(default_factory=lambda: ["*"])
    todo_max_per_group: int = 50           # max active todos per group (1-200)
    todo_completed_retention_days: int = 30  # auto-clean completed (0=forever)
    todo_deleted_retention_days: int = 30    # auto-clean deleted (0=forever)
    todo_add_keywords: list[str] = field(default_factory=lambda: [
        "记一下", "添加待办", "新建待办", "帮我记", "待办",
    ])
    todo_complete_keywords: list[str] = field(default_factory=lambda: [
        "搞定", "做完了", "完成", "完成了", "done",
    ])
    todo_delete_keywords: list[str] = field(default_factory=lambda: [
        "删掉", "删除", "取消", "不要了",
    ])

    # === Activity Points ===
    # Master switch — award points for group messages, enable 积分 commands.
    points_enabled: bool = True
    # Comma-separated group names where points are active. "*" = all groups.
    points_groups: list[str] = field(default_factory=lambda: ["*"])
    points_base_score: int = 1
    points_length_min_chars: int = 20
    points_length_bonus: int = 1
    points_long_length_min_chars: int = 100
    points_long_length_bonus: int = 1
    points_media_bonus: int = 1
    points_reply_bonus: int = 2
    points_daily_first_bonus: int = 10
    points_per_message_cap: int = 5
    points_rate_limit_per_min: int = 8
    points_daily_cap: int = 80
    points_max_age_sec: int = 300
    points_board_size: int = 10
    points_levels_path: str = "data/point_levels.json"

    # === Equipment Shop ===
    equipment_enabled: bool = True
    equipment_path: str = "data/equipment.json"

    # === Duels (切磋) ===
    duel_enabled: bool = True
    duel_groups: list[str] = field(default_factory=lambda: ["*"])
    duel_prob_k: float = 1.15
    duel_min_prob: float = 0.08
    duel_max_prob: float = 0.92
    duel_base_steal_ratio: float = 0.08
    duel_reward_k: float = 1.2
    duel_per_fight_cap: int = 20
    duel_daily_attempts: int = 10
    duel_daily_pair_attempts: int = 5
    duel_daily_steal_cap: int = 50
    duel_pair_cooldown_sec: int = 300
    duel_loss_cooldown_sec: int = 120
    duel_novice_protection_points: int = 20
    shield_price: int = 20
    shield_duration_sec: int = 14400
    shield_max_sec: int = 86400

    # === Tuning ===
    poll_interval_sec: float = 1.0
    dedup_window_sec: int = 60
    max_messages_for_summary: int = 5000
    chunk_size: int = 400

    # === Logging ===
    log_level: str = "INFO"
    log_file: str = "data/bot.log"

    # === Voice Recognition ===
    # Master switch (default off — user must opt in)
    voice_asr_enabled: bool = False
    # "local_whisper" (free, offline) | "openai_whisper" (cloud, $0.006/min)
    voice_asr_backend: str = "local_whisper"
    voice_asr_language: str = "zh"
    # OpenAI Whisper API
    voice_openai_api_key: str = ""
    voice_openai_base_url: str = ""
    # Local Whisper model size: tiny / base / small / medium
    voice_local_model: str = "small"
    # Convert traditional Chinese → simplified (opencc t2s)
    voice_asr_to_simplified: bool = True


def _safe_float(raw: str, default: float, label: str) -> float:
    """Parse env value to float, raising RuntimeError with a friendly message on failure."""
    try:
        return float(raw)
    except (ValueError, TypeError):
        raise RuntimeError(
            f"{label} 值 '{raw}' 不是有效的数字，请检查 .env 文件"
        ) from None


def _safe_int(raw: str, default: int, label: str) -> int:
    """Parse env value to int, raising RuntimeError with a friendly message on failure."""
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise RuntimeError(
            f"{label} 值 '{raw}' 不是有效的整数，请检查 .env 文件"
        ) from None


def _validate_config(kwargs: dict) -> None:
    """Validate numeric config values.  Prints clear errors and exits on bad values."""
    errors: list[str] = []

    # poll_interval_sec
    poll_interval_sec = kwargs.get("poll_interval_sec", 1.0)
    if poll_interval_sec < 0.1:
        errors.append(
            f"POLL_INTERVAL_SEC must be >= 0.1, got {poll_interval_sec}"
        )

    # chunk_size
    chunk_size = kwargs.get("chunk_size", 400)
    if not (10 <= chunk_size <= 1000):
        errors.append(
            f"CHUNK_SIZE must be between 10 and 1000, got {chunk_size}"
        )

    # max_messages_for_summary
    max_messages_for_summary = kwargs.get("max_messages_for_summary", 5000)
    if max_messages_for_summary < 10:
        errors.append(
            f"MAX_MESSAGES_FOR_SUMMARY must be >= 10, got {max_messages_for_summary}"
        )

    # fallback_window_hours
    fallback_window_hours = kwargs.get("fallback_window_hours", 8)
    if fallback_window_hours < 1:
        errors.append(
            f"FALLBACK_WINDOW_HOURS must be >= 1, got {fallback_window_hours}"
        )

    # max_retries
    max_retries = kwargs.get("max_retries", 3)
    if not (1 <= max_retries <= 10):
        errors.append(
            f"MAX_RETRIES must be between 1 and 10, got {max_retries}"
        )

    # dedup_window_sec
    dedup_window_sec = kwargs.get("dedup_window_sec", 60)
    if dedup_window_sec < 10:
        errors.append(
            f"DEDUP_WINDOW_SEC must be >= 10, got {dedup_window_sec}"
        )

    # sticky_mention_ttl_sec
    sticky_mention_ttl_sec = kwargs.get("sticky_mention_ttl_sec", 60)
    if not (10 <= sticky_mention_ttl_sec <= 300):
        errors.append(
            f"STICKY_MENTION_TTL_SEC must be between 10 and 300, "
            f"got {sticky_mention_ttl_sec}"
        )

    # inactive_kick_minutes
    inactive_kick_minutes = kwargs.get("inactive_kick_minutes", 10)
    if not (1 <= inactive_kick_minutes <= 1440):
        errors.append(
            f"INACTIVE_KICK_MINUTES must be between 1 and 1440, "
            f"got {inactive_kick_minutes}"
        )

    # proactive_rate_window_sec
    proactive_rate_window_sec = kwargs.get("proactive_rate_window_sec", 120)
    if proactive_rate_window_sec < 30:
        errors.append(
            f"PROACTIVE_RATE_WINDOW_SEC must be >= 30, got {proactive_rate_window_sec}"
        )

    # proactive_rate thresholds: all > 0 and in strict ascending order
    quiet = kwargs.get("proactive_rate_quiet", 1.5)
    casual = kwargs.get("proactive_rate_casual", 4.0)
    lively = kwargs.get("proactive_rate_lively", 6.5)
    burst = kwargs.get("proactive_rate_burst", 8.5)

    rate_names = ("quiet", "casual", "lively", "burst")
    rate_values = (quiet, casual, lively, burst)

    if any(v <= 0 for v in rate_values):
        errors.append(
            "All PROACTIVE_RATE_* values must be > 0, got: "
            + ", ".join(f"{n}={v}" for n, v in zip(rate_names, rate_values))
        )

    if not (quiet < casual < lively < burst):
        errors.append(
            "PROACTIVE_RATE_* values must be in strict ascending order "
            "(quiet < casual < lively < burst), got: "
            + ", ".join(f"{n}={v}" for n, v in zip(rate_names, rate_values))
        )

    # feishu_export_mode
    feishu_export_mode = kwargs.get("feishu_export_mode", "knowledge")
    if feishu_export_mode not in ("knowledge", "spreadsheet", "bitable", "docx"):
        errors.append(
            "FEISHU_EXPORT_MODE must be one of knowledge, spreadsheet, bitable, docx, "
            f"got {feishu_export_mode}"
        )

    # feishu_export_window_hours
    feishu_export_window_hours = kwargs.get("feishu_export_window_hours", 8)
    if not (1 <= feishu_export_window_hours <= 168):
        errors.append(
            "FEISHU_EXPORT_WINDOW_HOURS must be between 1 and 168, "
            f"got {feishu_export_window_hours}"
        )

    feishu_auto_sync_min_messages = kwargs.get("feishu_auto_sync_min_messages", 20)
    if not (1 <= feishu_auto_sync_min_messages <= 500):
        errors.append(
            "FEISHU_AUTO_SYNC_MIN_MESSAGES must be between 1 and 500, "
            f"got {feishu_auto_sync_min_messages}"
        )

    feishu_auto_sync_cooldown_sec = kwargs.get("feishu_auto_sync_cooldown_sec", 1800)
    if not (60 <= feishu_auto_sync_cooldown_sec <= 86400):
        errors.append(
            "FEISHU_AUTO_SYNC_COOLDOWN_SEC must be between 60 and 86400, "
            f"got {feishu_auto_sync_cooldown_sec}"
        )

    # Activity points / duels
    points_per_message_cap = kwargs.get("points_per_message_cap", 5)
    if not (1 <= points_per_message_cap <= 100):
        errors.append(
            "POINTS_PER_MESSAGE_CAP must be between 1 and 100, "
            f"got {points_per_message_cap}"
        )

    points_rate_limit_per_min = kwargs.get("points_rate_limit_per_min", 8)
    if not (1 <= points_rate_limit_per_min <= 120):
        errors.append(
            "POINTS_RATE_LIMIT_PER_MIN must be between 1 and 120, "
            f"got {points_rate_limit_per_min}"
        )

    points_daily_cap = kwargs.get("points_daily_cap", 80)
    if not (1 <= points_daily_cap <= 10000):
        errors.append(
            "POINTS_DAILY_CAP must be between 1 and 10000, "
            f"got {points_daily_cap}"
        )

    duel_min_prob = kwargs.get("duel_min_prob", 0.08)
    duel_max_prob = kwargs.get("duel_max_prob", 0.92)
    if not (0.0 <= duel_min_prob < duel_max_prob <= 1.0):
        errors.append(
            "DUEL_MIN_PROB / DUEL_MAX_PROB must satisfy "
            "0 <= min < max <= 1, got "
            f"{duel_min_prob} / {duel_max_prob}"
        )

    duel_daily_attempts = kwargs.get("duel_daily_attempts", 10)
    if not (1 <= duel_daily_attempts <= 100):
        errors.append(
            "DUEL_DAILY_ATTEMPTS must be between 1 and 100, "
            f"got {duel_daily_attempts}"
        )

    if errors:
        msg = "配置值无效:\n" + "\n".join(f"  - {err}" for err in errors)
        raise RuntimeError(msg)


def load_config() -> BotConfig:
    """Load configuration from environment variables.

    Returns a validated BotConfig instance.
    Raises RuntimeError if required configuration is missing.
    """
    ai_backend = os.getenv("AI_BACKEND", "claude").strip().lower()

    # Validate required API keys based on selected backend
    if ai_backend == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            msg = "DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置或通过引导页完成设置"
            raise RuntimeError(msg)
    elif ai_backend == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            msg = "OPENAI_API_KEY 未设置，请在 .env 文件中配置或通过引导页完成设置"
            raise RuntimeError(msg)
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            msg = "ANTHROPIC_API_KEY 未设置，请在 .env 文件中配置或通过引导页完成设置"
            raise RuntimeError(msg)

    # Parse trigger keywords from comma-separated string
    keywords_str = os.getenv("TRIGGER_KEYWORDS", "").strip()
    trigger_keywords = (
        [kw.strip() for kw in keywords_str.split(",") if kw.strip()]
        if keywords_str
        else None  # let the dataclass default apply
    )
    feishu_keywords_str = os.getenv("FEISHU_EXPORT_TRIGGER_KEYWORDS", "").strip()
    feishu_export_trigger_keywords = (
        [kw.strip() for kw in feishu_keywords_str.split(",") if kw.strip()]
        if feishu_keywords_str
        else None
    )
    todo_add_kw_str = os.getenv("TODO_ADD_KEYWORDS", "").strip()
    todo_add_keywords = (
        [kw.strip() for kw in todo_add_kw_str.split(",") if kw.strip()]
        if todo_add_kw_str else None
    )
    todo_complete_kw_str = os.getenv("TODO_COMPLETE_KEYWORDS", "").strip()
    todo_complete_keywords = (
        [kw.strip() for kw in todo_complete_kw_str.split(",") if kw.strip()]
        if todo_complete_kw_str else None
    )
    todo_delete_kw_str = os.getenv("TODO_DELETE_KEYWORDS", "").strip()
    todo_delete_keywords = (
        [kw.strip() for kw in todo_delete_kw_str.split(",") if kw.strip()]
        if todo_delete_kw_str else None
    )

    kwargs: dict = {
        "ai_backend": ai_backend,
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "anthropic_base_url": os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip(),
        "summarize_model": os.getenv("SUMMARIZE_MODEL", "claude-haiku-4-5-20251001").strip(),
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "deepseek_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        "openai_api_key": os.getenv("OPENAI_API_KEY", "").strip(),
        "openai_base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip(),
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        # deepseek_model handled conditionally below (dataclass default)
        "wechat_backend": os.getenv("WECHAT_BACKEND", "wcdb").strip(),
        "wechat_groups": _decode_wechat_groups(os.getenv("WECHAT_GROUPS", "*")),
        "wechat_data_dir": os.getenv("WECHAT_DATA_DIR", "").strip(),
        "bot_display_name": _sanitize_display_name(os.getenv("BOT_DISPLAY_NAME", "群聊小助手")),
        "bot_aliases": _parse_bot_aliases(os.getenv("BOT_ALIASES", "")),
        "admin_wxid": os.getenv("ADMIN_WXID", "").strip(),
        "db_path": os.getenv("DB_PATH", "data/messages.db").strip(),
        "poll_interval_sec": _safe_float(os.getenv("POLL_INTERVAL_SEC", "1.0"), 1.0, "POLL_INTERVAL_SEC"),
        "dedup_window_sec": _safe_int(os.getenv("DEDUP_WINDOW_SEC", "60"), 60, "DEDUP_WINDOW_SEC"),
        "max_messages_for_summary": _safe_int(os.getenv("MAX_MESSAGES_FOR_SUMMARY", "5000"), 5000, "MAX_MESSAGES_FOR_SUMMARY"),
        "chunk_size": _safe_int(os.getenv("CHUNK_SIZE", "400"), 400, "CHUNK_SIZE"),
        "fallback_window_hours": _safe_int(os.getenv("FALLBACK_WINDOW_HOURS", "8"), 8, "FALLBACK_WINDOW_HOURS"),
        "max_retries": _safe_int(os.getenv("MAX_RETRIES", "3"), 3, "MAX_RETRIES"),
        "feishu_export_enabled": os.getenv("FEISHU_EXPORT_ENABLED", "false").strip().lower() == "true",
        "feishu_app_id": os.getenv("FEISHU_APP_ID", "").strip(),
        "feishu_app_secret": os.getenv("FEISHU_APP_SECRET", "").strip(),
        "feishu_export_mode": os.getenv("FEISHU_EXPORT_MODE", "knowledge").strip().lower(),
        "feishu_export_window_hours": _safe_int(os.getenv("FEISHU_EXPORT_WINDOW_HOURS", "8"), 8, "FEISHU_EXPORT_WINDOW_HOURS"),
        "feishu_auto_sync_enabled": os.getenv("FEISHU_AUTO_SYNC_ENABLED", "false").strip().lower() == "true",
        "feishu_auto_sync_min_messages": _safe_int(os.getenv("FEISHU_AUTO_SYNC_MIN_MESSAGES", "20"), 20, "FEISHU_AUTO_SYNC_MIN_MESSAGES"),
        "feishu_auto_sync_cooldown_sec": _safe_int(os.getenv("FEISHU_AUTO_SYNC_COOLDOWN_SEC", "1800"), 1800, "FEISHU_AUTO_SYNC_COOLDOWN_SEC"),
        "feishu_knowledge_base_name": os.getenv("FEISHU_KNOWLEDGE_BASE_NAME", "webot 群聊沉淀").strip(),
        "feishu_knowledge_folder_token": os.getenv("FEISHU_KNOWLEDGE_FOLDER_TOKEN", "").strip(),
        "feishu_spreadsheet_token": os.getenv("FEISHU_SPREADSHEET_TOKEN", "").strip(),
        "feishu_spreadsheet_range": os.getenv("FEISHU_SPREADSHEET_RANGE", "Sheet1!A:H").strip(),
        "feishu_bitable_app_token": os.getenv("FEISHU_BITABLE_APP_TOKEN", "").strip(),
        "feishu_bitable_table_id": os.getenv("FEISHU_BITABLE_TABLE_ID", "").strip(),
        "feishu_doc_folder_token": os.getenv("FEISHU_DOC_FOLDER_TOKEN", "").strip(),
        "fun_enabled": os.getenv("FUN_ENABLED", "true").strip().lower() == "true",
        "summarize_enabled": os.getenv("SUMMARIZE_ENABLED", "true").strip().lower() == "true",
        "proactive_enabled": os.getenv("PROACTIVE_ENABLED", "false").strip().lower() == "true",
        # proactive_rate_window_sec handled conditionally below (dataclass default)
        "proactive_rate_quiet": _safe_float(os.getenv("PROACTIVE_RATE_QUIET", "1.5"), 1.5, "PROACTIVE_RATE_QUIET"),
        "proactive_rate_casual": _safe_float(os.getenv("PROACTIVE_RATE_CASUAL", "4.0"), 4.0, "PROACTIVE_RATE_CASUAL"),
        "proactive_rate_lively": _safe_float(os.getenv("PROACTIVE_RATE_LIVELY", "6.5"), 6.5, "PROACTIVE_RATE_LIVELY"),
        "proactive_rate_burst": _safe_float(os.getenv("PROACTIVE_RATE_BURST", "8.5"), 8.5, "PROACTIVE_RATE_BURST"),
        "welcome_enabled": os.getenv("WELCOME_ENABLED", "false").strip().lower() == "true",
        "inactive_kick_enabled": os.getenv("INACTIVE_KICK_ENABLED", "false").strip().lower() == "true",
        "inactive_kick_minutes": _safe_int(os.getenv("INACTIVE_KICK_MINUTES", "10"), 10, "INACTIVE_KICK_MINUTES"),
        "sticky_mention_enabled": os.getenv("STICKY_MENTION_ENABLED", "true").strip().lower() == "true",
        "sticky_mention_ttl_sec": _safe_int(os.getenv("STICKY_MENTION_TTL_SEC", "60"), 60, "STICKY_MENTION_TTL_SEC"),
        # Group Todo
        "todo_enabled": os.getenv("TODO_ENABLED", "true").strip().lower() == "true",
        "todo_groups": [g.strip() for g in os.getenv("TODO_GROUPS", "*").split(",") if g.strip()],
        "todo_max_per_group": _safe_int(os.getenv("TODO_MAX_PER_GROUP", "50"), 50, "TODO_MAX_PER_GROUP"),
        "todo_completed_retention_days": _safe_int(os.getenv("TODO_COMPLETED_RETENTION_DAYS", "30"), 30, "TODO_COMPLETED_RETENTION_DAYS"),
        "todo_deleted_retention_days": _safe_int(os.getenv("TODO_DELETED_RETENTION_DAYS", "30"), 30, "TODO_DELETED_RETENTION_DAYS"),
        # Activity points
        "points_enabled": os.getenv("POINTS_ENABLED", "true").strip().lower() == "true",
        "points_groups": [g.strip() for g in os.getenv("POINTS_GROUPS", "*").split(",") if g.strip()],
        "points_base_score": _safe_int(os.getenv("POINTS_BASE_SCORE", "1"), 1, "POINTS_BASE_SCORE"),
        "points_length_min_chars": _safe_int(os.getenv("POINTS_LENGTH_MIN_CHARS", "20"), 20, "POINTS_LENGTH_MIN_CHARS"),
        "points_length_bonus": _safe_int(os.getenv("POINTS_LENGTH_BONUS", "1"), 1, "POINTS_LENGTH_BONUS"),
        "points_long_length_min_chars": _safe_int(os.getenv("POINTS_LONG_LENGTH_MIN_CHARS", "100"), 100, "POINTS_LONG_LENGTH_MIN_CHARS"),
        "points_long_length_bonus": _safe_int(os.getenv("POINTS_LONG_LENGTH_BONUS", "1"), 1, "POINTS_LONG_LENGTH_BONUS"),
        "points_media_bonus": _safe_int(os.getenv("POINTS_MEDIA_BONUS", "1"), 1, "POINTS_MEDIA_BONUS"),
        "points_reply_bonus": _safe_int(os.getenv("POINTS_REPLY_BONUS", "2"), 2, "POINTS_REPLY_BONUS"),
        "points_daily_first_bonus": _safe_int(os.getenv("POINTS_DAILY_FIRST_BONUS", "10"), 10, "POINTS_DAILY_FIRST_BONUS"),
        "points_per_message_cap": _safe_int(os.getenv("POINTS_PER_MESSAGE_CAP", "5"), 5, "POINTS_PER_MESSAGE_CAP"),
        "points_rate_limit_per_min": _safe_int(os.getenv("POINTS_RATE_LIMIT_PER_MIN", "8"), 8, "POINTS_RATE_LIMIT_PER_MIN"),
        "points_daily_cap": _safe_int(os.getenv("POINTS_DAILY_CAP", "80"), 80, "POINTS_DAILY_CAP"),
        "points_max_age_sec": _safe_int(os.getenv("POINTS_MAX_AGE_SEC", "300"), 300, "POINTS_MAX_AGE_SEC"),
        "points_board_size": _safe_int(os.getenv("POINTS_BOARD_SIZE", "10"), 10, "POINTS_BOARD_SIZE"),
        "points_levels_path": os.getenv("POINTS_LEVELS_PATH", "data/point_levels.json").strip(),
        # Equipment
        "equipment_enabled": os.getenv("EQUIPMENT_ENABLED", "true").strip().lower() == "true",
        "equipment_path": os.getenv("EQUIPMENT_PATH", "data/equipment.json").strip(),
        # Duels
        "duel_enabled": os.getenv("DUEL_ENABLED", "true").strip().lower() == "true",
        "duel_groups": [g.strip() for g in os.getenv("DUEL_GROUPS", "*").split(",") if g.strip()],
        "duel_prob_k": _safe_float(os.getenv("DUEL_PROB_K", "1.15"), 1.15, "DUEL_PROB_K"),
        "duel_min_prob": _safe_float(os.getenv("DUEL_MIN_PROB", "0.08"), 0.08, "DUEL_MIN_PROB"),
        "duel_max_prob": _safe_float(os.getenv("DUEL_MAX_PROB", "0.92"), 0.92, "DUEL_MAX_PROB"),
        "duel_base_steal_ratio": _safe_float(os.getenv("DUEL_BASE_STEAL_RATIO", "0.08"), 0.08, "DUEL_BASE_STEAL_RATIO"),
        "duel_reward_k": _safe_float(os.getenv("DUEL_REWARD_K", "1.2"), 1.2, "DUEL_REWARD_K"),
        "duel_per_fight_cap": _safe_int(os.getenv("DUEL_PER_FIGHT_CAP", "20"), 20, "DUEL_PER_FIGHT_CAP"),
        "duel_daily_attempts": _safe_int(os.getenv("DUEL_DAILY_ATTEMPTS", "10"), 10, "DUEL_DAILY_ATTEMPTS"),
        "duel_daily_pair_attempts": _safe_int(os.getenv("DUEL_PAIR_DAILY_ATTEMPTS", "5"), 5, "DUEL_PAIR_DAILY_ATTEMPTS"),
        "duel_daily_steal_cap": _safe_int(os.getenv("DUEL_DAILY_STEAL_CAP", "50"), 50, "DUEL_DAILY_STEAL_CAP"),
        "duel_pair_cooldown_sec": _safe_int(os.getenv("DUEL_PAIR_COOLDOWN_SEC", "300"), 300, "DUEL_PAIR_COOLDOWN_SEC"),
        "duel_loss_cooldown_sec": _safe_int(os.getenv("DUEL_LOSS_COOLDOWN_SEC", "120"), 120, "DUEL_LOSS_COOLDOWN_SEC"),
        "duel_novice_protection_points": _safe_int(os.getenv("DUEL_NOVICE_PROTECTION_POINTS", "20"), 20, "DUEL_NOVICE_PROTECTION_POINTS"),
        "shield_price": _safe_int(os.getenv("SHIELD_PRICE", "20"), 20, "SHIELD_PRICE"),
        "shield_duration_sec": _safe_int(os.getenv("SHIELD_DURATION_SEC", "14400"), 14400, "SHIELD_DURATION_SEC"),
        "shield_max_sec": _safe_int(os.getenv("SHIELD_MAX_SEC", "86400"), 86400, "SHIELD_MAX_SEC"),
        "log_level": os.getenv("LOG_LEVEL", "INFO").strip(),
        "log_file": os.getenv("LOG_FILE", "data/bot.log").strip(),
        # Voice recognition
        "voice_asr_enabled": os.getenv("VOICE_ASR_ENABLED", "false").strip().lower() == "true",
        "voice_asr_backend": os.getenv("VOICE_ASR_BACKEND", "local_whisper").strip(),
        "voice_asr_language": os.getenv("VOICE_ASR_LANGUAGE", "zh").strip(),
        "voice_openai_api_key": os.getenv("VOICE_OPENAI_API_KEY", "").strip(),
        "voice_openai_base_url": os.getenv("VOICE_OPENAI_BASE_URL", "").strip(),
        "voice_local_model": os.getenv("VOICE_LOCAL_MODEL", "small").strip(),
        "voice_asr_to_simplified": os.getenv("VOICE_ASR_TO_SIMPLIFIED", "true").strip().lower() == "true",
    }

    deepseek_model = os.getenv("DEEPSEEK_MODEL")
    if deepseek_model is not None and deepseek_model.strip():
        kwargs["deepseek_model"] = deepseek_model.strip()

    proactive_rate_window_sec = os.getenv("PROACTIVE_RATE_WINDOW_SEC")
    if proactive_rate_window_sec is not None and proactive_rate_window_sec.strip():
        kwargs["proactive_rate_window_sec"] = int(proactive_rate_window_sec)

    if trigger_keywords is not None:
        kwargs["trigger_keywords"] = trigger_keywords
    if feishu_export_trigger_keywords is not None:
        kwargs["feishu_export_trigger_keywords"] = feishu_export_trigger_keywords
    if todo_add_keywords is not None:
        kwargs["todo_add_keywords"] = todo_add_keywords
    if todo_complete_keywords is not None:
        kwargs["todo_complete_keywords"] = todo_complete_keywords
    if todo_delete_keywords is not None:
        kwargs["todo_delete_keywords"] = todo_delete_keywords

    _validate_config(kwargs)

    return BotConfig(**kwargs)


def is_onboarding_done() -> bool:
    """Check if onboarding has been completed without loading full config.

    Uses find_env_file() for consistent .env resolution.
    """
    env_path = find_env_file()
    if env_path and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ONBOARDING_DONE="):
                return line.split("=", 1)[1].strip().strip("\"'").lower() == "true"
        return False  # .env exists but no ONBOARDING_DONE key
    return False  # No .env found
