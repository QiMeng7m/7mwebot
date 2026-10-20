"""Backfill activity points from the stored ``messages`` table.

Examples::

    # 补最近 30 天（只写入缺失的流水，幂等）
    python tools/points_backfill.py --chat 123@chatroom --days 30

    # 规则调整后全量重算（保留管理员调整、消费与切磋转移）
    python tools/points_backfill.py --chat 123@chatroom --rebuild

    # 所有群
    python tools/points_backfill.py --all --rebuild

历史消息默认不会在机器人启动时补分，避免上线第一天就把所有人的历史
发言算成积分；需要时用本工具显式触发。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.schema import initialize_db  # noqa: E402
from src.points.engine import PointEngine  # noqa: E402
from src.points.store import PointStore  # noqa: E402


def _load_config():
    try:
        from src.config import load_config
        return load_config()
    except Exception:
        from src.config import BotConfig
        return BotConfig()


def _chats(conn: sqlite3.Connection, explicit: list[str]) -> list[str]:
    if explicit:
        return explicit
    rows = conn.execute(
        "SELECT DISTINCT chat_id FROM messages "
        "WHERE chat_id LIKE '%@chatroom%' ORDER BY chat_id"
    ).fetchall()
    return [row[0] for row in rows]


def backfill(conn: sqlite3.Connection, config, chat_id: str,
             days: int, rebuild: bool) -> dict:
    store = PointStore(conn, lock=None)
    config.points_groups = ["*"]  # chat was selected explicitly
    engine = PointEngine(store, config)

    if rebuild:
        result = engine.rebuild(chat_id, days=days)
        return {
            "chat_id": chat_id,
            "messages": result["messages"],
            "removed": result["removed"],
            "restored": result["restored"],
        }

    since = int(time.time()) - max(1, int(days)) * 86400
    messages = store.messages_since(chat_id, since)
    awarded = 0
    for msg in messages:
        awarded += engine.award_for_message(msg, allow_stale=True)
    return {
        "chat_id": chat_id,
        "messages": len(messages),
        "removed": 0,
        "restored": awarded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill activity points from stored messages.",
    )
    parser.add_argument("--chat", action="append", default=[],
                        help="chat_id to process (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="process every group chat found in messages")
    parser.add_argument("--days", type=int, default=90,
                        help="history window in days (default: 90)")
    parser.add_argument("--rebuild", action="store_true",
                        help="delete existing earn events first, then replay")
    parser.add_argument("--db", default="",
                        help="override database path")
    args = parser.parse_args()

    if not args.chat and not args.all:
        parser.error("specify --chat <id> or --all")

    config = _load_config()
    db_path = args.db or getattr(config, "db_path", "data/messages.db")
    print(f"数据库: {db_path}")

    conn = initialize_db(db_path)
    try:
        chats = _chats(conn, args.chat)
        if not chats:
            print("没有找到群聊记录。")
            return 0
        for chat_id in chats:
            result = backfill(conn, config, chat_id, args.days, args.rebuild)
            print(
                f"[{chat_id}] 扫描 {result['messages']} 条消息，"
                f"清理 {result['removed']} 条旧流水，"
                f"计入 {result['restored']} 分"
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
