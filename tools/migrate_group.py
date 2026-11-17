"""把一个微信群的全部 bot 数据迁移到另一个微信群。

适用场景：群重建 / 换群。源群在 ``messages.db`` 里的聊天记录、积分、
等级、装备、钓鱼、副本、待办、群记忆等，以及 ``data/*.json`` 里按
chat_id 缓存的数据，都会改归属到目标群。

用法::

    # 1. 先预演（不写任何数据），确认数字对不对
    python tools/migrate_group.py \
        --source "想亲想爱一家人" \
        --target "重生之我在微信当大王"

    # 2. 停止 webot / 桌面程序后正式迁移
    python tools/migrate_group.py \
        --source "想亲想爱一家人" \
        --target "重生之我在微信当大王" --yes

群名会自动从微信会话里解析成 chat_id（需要微信正在运行）；也可以
直接传 ``xxxxx@chatroom``。迁移前会自动备份数据库和 JSON 到
``data/migration_backup_<时间戳>/``。

注意：迁移只改数据归属，不会把旧消息发到微信新群里。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.group_migration import (  # noqa: E402
    JSON_FILES,
    MigrationError,
    migrate_db,
    migrate_json,
    plan_db_migration,
    plan_json_migration,
)

DEFAULT_DB = PROJECT_ROOT / "data" / "messages.db"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def _normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = re.sub(r"[\s​‌‍⁠﻿]+", "", text)
    return text.casefold()


def _looks_like_chat_id(value: str) -> bool:
    return value.strip().endswith("@chatroom")


def _open_sessions():
    from src.wechat.wcdb_client import WcdbNativeClient

    client = WcdbNativeClient()
    client.init()
    client.open()
    rooms: dict[str, str] = {}
    for session in client.get_sessions():
        username = str(session.get("username") or "")
        if not username.endswith("@chatroom"):
            continue
        display = str(
            session.get("displayName") or session.get("displayname")
            or session.get("nickname") or session.get("display_name")
            or ""
        ).strip()
        if not display or display == username:
            display = client.resolve_nickname(username)
        if not display or display == username:
            display = str(
                session.get("last_sender_display_name") or ""
            ).strip()
        if not display or display == username:
            display = username
        rooms[username] = display
    return client, rooms


def _resolve_groups(values: list[str]) -> tuple[dict[str, tuple[str, str]], object]:
    """把群名/chat_id 解析成 ``{原参数: (chat_id, 显示名)}``。

    只有出现群名时才打开 WCDB；全部传 chat_id 时不需要微信在运行。
    """
    names = [v for v in values if not _looks_like_chat_id(v)]
    if not names:
        return {v: (v, v) for v in values}, None

    client, rooms = _open_sessions()
    resolved: dict[str, tuple[str, str]] = {}
    try:
        for value in values:
            if _looks_like_chat_id(value):
                resolved[value] = (value, value)
                continue
            target = _normalize_name(value)
            match = None
            for chat_id, display in rooms.items():
                if _normalize_name(display) == target:
                    match = chat_id
                    break
            if match is None:
                for chat_id, display in rooms.items():
                    left, right = target, _normalize_name(display)
                    if left and right and (left in right or right in left):
                        match = chat_id
                        break
            if match is None:
                available = ", ".join(
                    f"'{display}' ({chat_id})"
                    for chat_id, display in rooms.items()
                )
                raise MigrationError(
                    f"在微信会话里找不到群 '{value}'。可用的群：{available}"
                )
            resolved[value] = (match, rooms[match])
    except Exception:
        client.close()
        raise
    return resolved, client


def _backup(db_path: Path, data_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = data_dir / f"migration_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_dir / "messages.db"))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    for name in JSON_FILES:
        path = data_dir / name
        if path.exists():
            shutil.copy2(path, backup_dir / name)
    return backup_dir


def _print_db_plan(plan: dict) -> None:
    print("\n数据库表：")
    for table, count in plan["source_counts"].items():
        if count:
            print(f"  {table:<24} {count:>8} 行")
    print(f"  {'合计':<24} {plan['source_total']:>8} 行")


def _print_json_plan(plan: dict[str, int]) -> None:
    print("\nJSON 缓存：")
    for name, count in plan.items():
        print(f"  {name:<24} {count:>8} 条")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把一个群的 bot 数据整体迁移到另一个群。",
    )
    parser.add_argument("--source", required=True,
                        help="源群名或 chat_id（如 46074645339@chatroom）")
    parser.add_argument("--target", required=True,
                        help="目标群名或 chat_id")
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help=f"数据库路径（默认: {DEFAULT_DB}）")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help=f"data 目录（默认: {DEFAULT_DATA_DIR}）")
    parser.add_argument("--yes", action="store_true",
                        help="确认执行迁移；不加此参数只做预演")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        resolved, client = _resolve_groups([args.source, args.target])
    except Exception as exc:
        print(f"解析群失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if "client" in locals() and client is not None:
            client.close()

    source_id, source_name = resolved[args.source]
    target_id, target_name = resolved[args.target]
    if source_id == target_id:
        print("源群和目标群是同一个群，无需迁移。", file=sys.stderr)
        return 1

    db_path = Path(args.db)
    data_dir = Path(args.data_dir)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1

    print(f"源群  : {source_name} ({source_id})")
    print(f"目标群: {target_name} ({target_id})")
    print(f"数据库: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        db_plan = plan_db_migration(conn, source_id, target_id)
        json_plan = plan_json_migration(data_dir, source_id, target_id)

        _print_db_plan(db_plan)
        _print_json_plan(json_plan)

        if db_plan["conflicts"]:
            detail = ", ".join(
                f"{t}={db_plan['target_counts'][t]}"
                for t in db_plan["conflicts"]
            )
            print(
                f"\n目标群已有数据，拒绝迁移：{detail}\n"
                "请确认目标群是否选错，或先清理这些数据。",
                file=sys.stderr,
            )
            return 1

        if not args.yes:
            print("\n以上为预演，未写入任何数据。")
            print("确认无误后，先停止 webot，再加 --yes 重新运行。")
            return 0

        if db_plan["source_total"] == 0 and not any(json_plan.values()):
            print("\n源群没有可迁移的数据。")
            return 0

        backup_dir = _backup(db_path, data_dir)
        print(f"\n已备份到: {backup_dir}")

        moved = migrate_db(conn, source_id, target_id)
        moved_json = migrate_json(
            data_dir, source_id, target_id, target_name=target_name,
        )
    except MigrationError as exc:
        print(f"\n迁移失败：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    total = sum(moved.values())
    print(f"数据库已迁移 {total} 行。")
    for name, count in moved_json.items():
        if count:
            print(f"{name}: 迁移 {count} 条。")
    print("迁移完成。源群数据已清空，目标群已接管全部数据。")
    print("提示：如果希望 bot 在新群里运行，记得把新群加入 .env 的 "
          "WECHAT_GROUPS。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
