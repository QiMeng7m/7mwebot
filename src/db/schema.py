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

-- Last fortune lot drawn by each member (for 「解签」).
-- day/interpreted_at enforce one draw and one reading per local day.
CREATE TABLE IF NOT EXISTS fun_draws (
    chat_id        TEXT    NOT NULL,
    sender_id      TEXT    NOT NULL,
    sender_name    TEXT    NOT NULL DEFAULT '',
    level          TEXT    NOT NULL,
    emoji          TEXT    NOT NULL DEFAULT '',
    phrase         TEXT    NOT NULL,
    drawn_at       INTEGER NOT NULL,
    day            TEXT    NOT NULL DEFAULT '',
    interpreted_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, sender_id)
);

-- ── Activity points (“活跃修炼值”) ───────────────────────────────────
-- Append-only ledger.  One row per scoring component, plus transfers
-- (duels), spending (shop / shield) and manual admin adjustments.
-- UNIQUE(message_id, reason) makes replaying a message idempotent.
CREATE TABLE IF NOT EXISTS point_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sender_name TEXT    NOT NULL DEFAULT '',
    message_id  TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'earn',
    points      INTEGER NOT NULL,
    day         TEXT    NOT NULL,
    season      TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    UNIQUE(message_id, reason)
);

CREATE INDEX IF NOT EXISTS idx_points_chat_season
    ON point_events(chat_id, season, sender_id);
CREATE INDEX IF NOT EXISTS idx_points_chat_day
    ON point_events(chat_id, day, sender_id);
CREATE INDEX IF NOT EXISTS idx_points_chat_sender
    ON point_events(chat_id, sender_id, created_at DESC);

-- Closed seasons (written lazily when a new month starts).
CREATE TABLE IF NOT EXISTS point_seasons (
    chat_id       TEXT    NOT NULL,
    season        TEXT    NOT NULL,
    winner_id     TEXT    NOT NULL DEFAULT '',
    winner_name   TEXT    NOT NULL DEFAULT '',
    winner_points INTEGER NOT NULL DEFAULT 0,
    closed_at     INTEGER NOT NULL,
    PRIMARY KEY (chat_id, season)
);

-- ── Equipment ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS equipment_owned (
    chat_id       TEXT    NOT NULL,
    sender_id     TEXT    NOT NULL,
    item_id       TEXT    NOT NULL,
    purchased_at  INTEGER NOT NULL,
    equipped      INTEGER NOT NULL DEFAULT 0,
    enhance_level INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, sender_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_equipment_member
    ON equipment_owned(chat_id, sender_id);

-- Timed “免战” shields (duel immunity).
CREATE TABLE IF NOT EXISTS duel_shields (
    chat_id    TEXT    NOT NULL,
    sender_id  TEXT    NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);

-- Duel audit log.
CREATE TABLE IF NOT EXISTS duels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        TEXT    NOT NULL,
    message_id     TEXT    NOT NULL DEFAULT '',
    attacker_id    TEXT    NOT NULL,
    attacker_name  TEXT    NOT NULL DEFAULT '',
    defender_id    TEXT    NOT NULL,
    defender_name  TEXT    NOT NULL DEFAULT '',
    attacker_power INTEGER NOT NULL,
    defender_power INTEGER NOT NULL,
    win_prob       REAL    NOT NULL,
    attacker_win   INTEGER NOT NULL,
    points_moved   INTEGER NOT NULL DEFAULT 0,
    shield_blocked INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    day            TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_duels_attacker_day
    ON duels(chat_id, day, attacker_id);
CREATE INDEX IF NOT EXISTS idx_duels_defender_day
    ON duels(chat_id, day, defender_id);
CREATE INDEX IF NOT EXISTS idx_duels_pair
    ON duels(chat_id, attacker_id, defender_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_duels_message
    ON duels(chat_id, message_id);

-- ── Fishing (“钓鱼”) ─────────────────────────────────────────────
-- Every cast is logged, including misses (species_id = '') so daily
-- attempt limits and cooldowns survive restarts.  Fishing credits the
-- append-only ledger with category='fish', which increases balance but
-- is excluded from cumulative earned points / levels / rankings.
CREATE TABLE IF NOT EXISTS fishing_casts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT    NOT NULL,
    sender_id    TEXT    NOT NULL,
    sender_name  TEXT    NOT NULL DEFAULT '',
    message_id   TEXT    NOT NULL,
    cast_index   INTEGER NOT NULL DEFAULT 0,
    rod_id       TEXT    NOT NULL DEFAULT '',
    bait_id      TEXT    NOT NULL DEFAULT '',
    species_id   TEXT    NOT NULL DEFAULT '',
    species_name TEXT    NOT NULL DEFAULT '',
    rarity       TEXT    NOT NULL DEFAULT '',
    weight_g     INTEGER NOT NULL DEFAULT 0,
    amount       INTEGER NOT NULL DEFAULT 0,
    day          TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    UNIQUE(message_id, cast_index)
);

CREATE INDEX IF NOT EXISTS idx_fish_member_day
    ON fishing_casts(chat_id, day, sender_id);
CREATE INDEX IF NOT EXISTS idx_fish_member_time
    ON fishing_casts(chat_id, sender_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fish_chat_time
    ON fishing_casts(chat_id, created_at DESC);

-- Rods (permanent, one equipped) and bait (consumable stacks) owned by
-- each member in each group.
CREATE TABLE IF NOT EXISTS fishing_inventory (
    chat_id      TEXT    NOT NULL,
    sender_id    TEXT    NOT NULL,
    kind         TEXT    NOT NULL,              -- 'rod' | 'bait'
    item_id      TEXT    NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 0,
    equipped     INTEGER NOT NULL DEFAULT 0,
    purchased_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id, kind, item_id)
);

CREATE INDEX IF NOT EXISTS idx_fish_inventory_member
    ON fishing_inventory(chat_id, sender_id);

-- ── Endgame: world boss / achievements / realms ───────────────────
-- One boss per group per ISO week.  HP scales with the group size when
-- the boss is first summoned.
CREATE TABLE IF NOT EXISTS boss_state (
    chat_id    TEXT    NOT NULL,
    season     TEXT    NOT NULL,              -- ISO week YYYY-Www
    boss_name  TEXT    NOT NULL,
    hp_total   INTEGER NOT NULL,
    hp_left    INTEGER NOT NULL,
    killed_at  INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, season)
);

-- Every boss hit.  UNIQUE(chat_id, message_id) makes command replay safe.
CREATE TABLE IF NOT EXISTS boss_attacks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    season      TEXT    NOT NULL,
    message_id  TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sender_name TEXT    NOT NULL DEFAULT '',
    damage      INTEGER NOT NULL,
    crit        INTEGER NOT NULL DEFAULT 0,
    day         TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    UNIQUE(chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_boss_attacks_member
    ON boss_attacks(chat_id, season, sender_id);
CREATE INDEX IF NOT EXISTS idx_boss_attacks_day
    ON boss_attacks(chat_id, season, day, sender_id);

-- Regenerating challenge attempts: one every BOSS_COOLDOWN_SEC seconds,
-- accumulating up to BOSS_MAX_ATTEMPTS.  Reset per boss week.
CREATE TABLE IF NOT EXISTS boss_energy (
    chat_id    TEXT    NOT NULL,
    season     TEXT    NOT NULL,
    sender_id  TEXT    NOT NULL,
    attempts   INTEGER NOT NULL,
    regen_at   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, season, sender_id)
);

-- Unlocked achievements (definitions live in src/points/achievements.py).
CREATE TABLE IF NOT EXISTS achievements (
    chat_id        TEXT    NOT NULL,
    sender_id      TEXT    NOT NULL,
    achievement_id TEXT    NOT NULL,
    unlocked_at    INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id, achievement_id)
);

-- Equipped display title per member.
CREATE TABLE IF NOT EXISTS member_profiles (
    chat_id    TEXT    NOT NULL,
    sender_id  TEXT    NOT NULL,
    title      TEXT    NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);

-- 境界 (realm) progression after Lv12.
CREATE TABLE IF NOT EXISTS member_realms (
    chat_id    TEXT    NOT NULL,
    sender_id  TEXT    NOT NULL,
    realm      INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);

-- Daily lottery: one row per paid draw.  UNIQUE(chat_id, message_id)
-- makes command replay safe; the day column enforces the daily limit.
CREATE TABLE IF NOT EXISTS lottery_draws (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sender_name TEXT    NOT NULL DEFAULT '',
    message_id  TEXT    NOT NULL,
    reward_kind TEXT    NOT NULL,
    reward_name TEXT    NOT NULL DEFAULT '',
    amount      INTEGER NOT NULL DEFAULT 0,
    day         TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    UNIQUE(chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_lottery_member_day
    ON lottery_draws(chat_id, sender_id, day);
"""


def _ensure_columns(conn: sqlite3.Connection, table: str,
                    columns: dict[str, str]) -> None:
    """Add missing columns to an existing table (idempotent migration)."""
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
    _ensure_columns(conn, "fun_draws", {
        "day": "day TEXT NOT NULL DEFAULT ''",
        "interpreted_at": "interpreted_at INTEGER NOT NULL DEFAULT 0",
    })
    _ensure_columns(conn, "equipment_owned", {
        "enhance_level": "enhance_level INTEGER NOT NULL DEFAULT 0",
    })

    conn.commit()
    return conn
