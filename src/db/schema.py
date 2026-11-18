"""Database schema and initialization.

Idempotent — safe to call on every startup.
"""

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;

-- Every message received from any monitored group chat
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT    UNIQUE NOT NULL,
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sender_name TEXT    NOT NULL,
    content     TEXT    NOT NULL DEFAULT '',
    msg_type    INTEGER NOT NULL DEFAULT 1,
    timestamp   INTEGER NOT NULL,
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- Fast lookup: "all messages in chat X since time T"
CREATE INDEX IF NOT EXISTS idx_msg_chat_time
    ON messages(chat_id, timestamp DESC);

-- Fast lookup: "last message from user S in chat X"
CREATE INDEX IF NOT EXISTS idx_msg_chat_sender_time
    ON messages(chat_id, sender_id, timestamp DESC);

-- Materialized last-message-time per user per chat.
-- Updated via UPSERT on every message insert.
CREATE TABLE IF NOT EXISTS user_last_message (
    chat_id        TEXT    NOT NULL,
    sender_id      TEXT    NOT NULL,
    sender_name    TEXT    NOT NULL,
    last_timestamp INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);

-- Prevent duplicate trigger responses.
CREATE TABLE IF NOT EXISTS trigger_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id            TEXT    NOT NULL,
    requester_id       TEXT    NOT NULL,
    trigger_message_id TEXT    NOT NULL,
    processed_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- Per-group long-term memory. Consolidates conversation history into
-- a first-person diary-style text that is injected into every prompt,
-- giving the bot a persistent sense of identity and group awareness.
CREATE TABLE IF NOT EXISTS group_memory (
    chat_id           TEXT    PRIMARY KEY,
    memory_text       TEXT    NOT NULL DEFAULT '',
    message_count     INTEGER NOT NULL DEFAULT 0,
    last_message_id   TEXT,
    last_consolidated REAL,
    created_at        REAL    NOT NULL DEFAULT (unixepoch()),
    updated_at        REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_trigger_chat_time
    ON trigger_log(chat_id, processed_at DESC);

-- ── 星盘玩法（KOI） ─────────────────────────────────────────────
-- 香火 / 气运只在本玩法内流转。

-- 香火（kind='merit'）与气运（kind='fortune'）流水。
-- UNIQUE 约束保证重复消息 / 重放不会重复发奖。
CREATE TABLE IF NOT EXISTS koi_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sender_name TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL,
    amount      INTEGER NOT NULL,
    reason      TEXT    NOT NULL,
    ref_id      TEXT    NOT NULL DEFAULT '',
    day         TEXT    NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    UNIQUE(chat_id, sender_id, ref_id, reason)
);

CREATE INDEX IF NOT EXISTS idx_koi_ledger_chat_kind
    ON koi_ledger(chat_id, kind, sender_id);
CREATE INDEX IF NOT EXISTS idx_koi_ledger_sender
    ON koi_ledger(chat_id, sender_id, created_at DESC);

-- 每人每群每天一行：签、欧气、次数与连签。
CREATE TABLE IF NOT EXISTS koi_daily (
    chat_id       TEXT    NOT NULL,
    sender_id     TEXT    NOT NULL,
    day           TEXT    NOT NULL,
    sign_id       INTEGER NOT NULL DEFAULT 0,
    sign_level    TEXT    NOT NULL DEFAULT '',
    luck          INTEGER NOT NULL DEFAULT 0,
    yi            TEXT    NOT NULL DEFAULT '',
    streak_days   INTEGER NOT NULL DEFAULT 0,
    rerolls       INTEGER NOT NULL DEFAULT 0,
    release_used  INTEGER NOT NULL DEFAULT 0,
    draws_used    INTEGER NOT NULL DEFAULT 0,
    chat_merit    INTEGER NOT NULL DEFAULT 0,
    pool_in       INTEGER NOT NULL DEFAULT 0,
    observed      INTEGER NOT NULL DEFAULT 0,
    duel_star_used INTEGER NOT NULL DEFAULT 0,
    extra_luck    INTEGER NOT NULL DEFAULT 0,
    fortune_used  INTEGER NOT NULL DEFAULT 0,
    hexagram_used INTEGER NOT NULL DEFAULT 0,
    hexagram_id   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, sender_id, day)
);

CREATE INDEX IF NOT EXISTS idx_koi_daily_chat_day
    ON koi_daily(chat_id, day);

-- 签谱：每支签第一次抽到即永久收录。
CREATE TABLE IF NOT EXISTS koi_signs (
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sign_id     INTEGER NOT NULL,
    first_at    INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id, sign_id)
);

-- 身份：星座 / MBTI / 简化星盘。
CREATE TABLE IF NOT EXISTS koi_profile (
    chat_id      TEXT    NOT NULL,
    sender_id    TEXT    NOT NULL,
    sign         TEXT    NOT NULL DEFAULT '',
    mbti         TEXT    NOT NULL DEFAULT '',
    moon_sign    TEXT    NOT NULL DEFAULT '',
    rising_sign  TEXT    NOT NULL DEFAULT '',
    title        TEXT    NOT NULL DEFAULT '',
    noble_star   TEXT    NOT NULL DEFAULT '',
    life_number  INTEGER NOT NULL DEFAULT 0,
    awakening    TEXT    NOT NULL DEFAULT '',
    birth_year   INTEGER NOT NULL DEFAULT 0,
    birth_month  INTEGER NOT NULL DEFAULT 0,
    birth_day    INTEGER NOT NULL DEFAULT 0,
    birth_hour   INTEGER NOT NULL DEFAULT -1,
    sign_updated_at   INTEGER NOT NULL DEFAULT 0,
    mbti_updated_at   INTEGER NOT NULL DEFAULT 0,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);

-- 通用收集（塔罗 / 星骰）；签谱仍使用 koi_signs。
CREATE TABLE IF NOT EXISTS koi_collection (
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    kind        TEXT    NOT NULL,      -- tarot | dice
    item_id     INTEGER NOT NULL,
    first_at    INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id, kind, item_id)
);

-- 符 / 增益
CREATE TABLE IF NOT EXISTS koi_buffs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    value       REAL    NOT NULL DEFAULT 0,
    uses_left   INTEGER NOT NULL DEFAULT 0,
    expires_at  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_koi_buffs_member
    ON koi_buffs(chat_id, sender_id, active);

-- 开光保底
CREATE TABLE IF NOT EXISTS koi_pity (
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    draws_since_legendary INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);

-- 星盘 28 宿 + 外圈
CREATE TABLE IF NOT EXISTS koi_stars (
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    quadrant    TEXT    NOT NULL,      -- qinglong | baihu | zhuque | xuanwu | outer
    star_index  INTEGER NOT NULL,
    lit_at      INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id, quadrant, star_index)
);

-- 四象阵营周结算
CREATE TABLE IF NOT EXISTS koi_faction_week (
    chat_id     TEXT    NOT NULL,
    week        TEXT    NOT NULL,
    winner      TEXT    NOT NULL DEFAULT '',
    settled_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, week)
);

-- 锦鲤池
CREATE TABLE IF NOT EXISTS koi_pool (
    chat_id     TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'open',
    winner_id   TEXT    NOT NULL DEFAULT '',
    winner_name TEXT    NOT NULL DEFAULT '',
    payout      INTEGER NOT NULL DEFAULT 0,
    drawn_at    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, day)
);

CREATE TABLE IF NOT EXISTS koi_pool_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sender_name TEXT    NOT NULL DEFAULT '',
    amount      INTEGER NOT NULL,
    luck        INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_koi_pool_day
    ON koi_pool_entries(chat_id, day, sender_id);

-- 周运
CREATE TABLE IF NOT EXISTS koi_weekly (
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    week        TEXT    NOT NULL,
    goals_json  TEXT    NOT NULL DEFAULT '[]',
    claimed     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, sender_id, week)
);
"""


def _ensure_columns(conn: sqlite3.Connection, table: str,
                    columns: dict[str, str]) -> None:
    """Add missing columns to an existing table (idempotent migration)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def initialize_db(db_path: str) -> sqlite3.Connection:
    """Initialize the SQLite database, creating tables if they don't exist.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A sqlite3.Connection with WAL mode enabled and row_factory set.
    """
    # Ensure the parent directory exists
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    # Migrations for tables created by older builds.
    _ensure_columns(conn, "koi_daily", {
        "duel_star_used": "duel_star_used INTEGER NOT NULL DEFAULT 0",
        "extra_luck": "extra_luck INTEGER NOT NULL DEFAULT 0",
        "fortune_used": "fortune_used INTEGER NOT NULL DEFAULT 0",
        "hexagram_used": "hexagram_used INTEGER NOT NULL DEFAULT 0",
        "hexagram_id": "hexagram_id INTEGER NOT NULL DEFAULT 0",
    })
    _ensure_columns(conn, "koi_profile", {
        "noble_star": "noble_star TEXT NOT NULL DEFAULT ''",
        "life_number": "life_number INTEGER NOT NULL DEFAULT 0",
        "awakening": "awakening TEXT NOT NULL DEFAULT ''",
        "birth_year": "birth_year INTEGER NOT NULL DEFAULT 0",
        "birth_month": "birth_month INTEGER NOT NULL DEFAULT 0",
        "birth_day": "birth_day INTEGER NOT NULL DEFAULT 0",
        "birth_hour": "birth_hour INTEGER NOT NULL DEFAULT -1",
        "sign_updated_at": "sign_updated_at INTEGER NOT NULL DEFAULT 0",
        "mbti_updated_at": "mbti_updated_at INTEGER NOT NULL DEFAULT 0",
    })

    conn.commit()
    return conn
