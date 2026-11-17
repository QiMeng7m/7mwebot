"""群数据迁移 — 把一个群的所有数据归属改到另一个群。

使用场景：游戏群重建 / 换群。源群在 ``messages.db`` 里的所有
``chat_id`` 行（聊天记录、积分、装备、钓鱼、副本、待办、记忆……）
以及 ``data/`` 下按 chat_id 缓存的 JSON，一次性迁移到目标群。

迁移是"移动"而不是"复制"：迁移完成后源群在数据库里的行数为 0。
目标群必须尚不存在任何数据，否则直接报错，避免主键/唯一索引冲突
或把两套积分无声地混在一起。

所有 SQLite 更新在一个事务里完成，任何一步失败都会整体回滚。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

CHAT_ID = "chat_id"

# JSON 缓存文件名（均以 chat_id 作为顶层 key，pending_kicks 例外）
GROUP_NAMES = "group_names.json"
GROUP_MEMBERS = "group_members.json"
GROUP_NICKNAMES = "group_nicknames.json"
WELCOME_TEMPLATES = "welcome_templates.json"
PENDING_KICKS = "pending_kicks.json"

JSON_FILES = (
    GROUP_NAMES,
    GROUP_MEMBERS,
    GROUP_NICKNAMES,
    WELCOME_TEMPLATES,
    PENDING_KICKS,
)


class MigrationError(RuntimeError):
    """迁移前置检查失败，或迁移过程中出现不可恢复的错误。"""


def list_chat_tables(conn: sqlite3.Connection) -> list[str]:
    """返回所有带 ``chat_id`` 列的用户表（按名称排序）。"""
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    result: list[str] = []
    for table in tables:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if CHAT_ID in columns:
            result.append(table)
    return sorted(result)


def chat_row_counts(conn: sqlite3.Connection, chat_id: str) -> dict[str, int]:
    """统计 *chat_id* 在每张带 chat_id 的表里的行数。"""
    counts: dict[str, int] = {}
    for table in list_chat_tables(conn):
        counts[table] = int(
            conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE {CHAT_ID}=?',
                (chat_id,),
            ).fetchone()[0]
        )
    return counts


def plan_db_migration(
    conn: sqlite3.Connection, source: str, target: str,
) -> dict[str, Any]:
    """只读预演：返回源群行数、目标群已有行数和冲突表。"""
    _validate_ids(source, target)
    source_counts = chat_row_counts(conn, source)
    target_counts = chat_row_counts(conn, target)
    conflicts = [t for t, n in target_counts.items() if n]
    return {
        "source_counts": source_counts,
        "target_counts": target_counts,
        "source_total": sum(source_counts.values()),
        "conflicts": conflicts,
    }


def migrate_db(
    conn: sqlite3.Connection, source: str, target: str,
) -> dict[str, int]:
    """把 *source* 的所有 chat_id 数据改归属到 *target*。

    Returns:
        每张表实际迁移的行数 ``{table: rows}``。

    Raises:
        MigrationError: 目标群已有数据、数据库被占用、或迁移后校验失败。
    """
    _validate_ids(source, target)

    started_transaction = not conn.in_transaction
    if started_transaction:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise MigrationError(
                "无法锁定数据库（可能 webot 正在运行）。"
                "请先停止 webot / 桌面程序后重试。"
            ) from exc

    try:
        target_counts = chat_row_counts(conn, target)
        conflicts = [t for t, n in target_counts.items() if n]
        if conflicts:
            detail = ", ".join(f"{t}={target_counts[t]}" for t in conflicts)
            raise MigrationError(
                f"目标群已有数据，拒绝迁移：{detail}。"
                "请先确认目标群是否选错，或手动清理这些行。"
            )

        moved: dict[str, int] = {}
        for table in list_chat_tables(conn):
            cursor = conn.execute(
                f'UPDATE "{table}" SET {CHAT_ID}=? WHERE {CHAT_ID}=?',
                (target, source),
            )
            moved[table] = int(cursor.rowcount)

        if started_transaction:
            conn.commit()
    except Exception:
        if started_transaction:
            conn.rollback()
        raise

    # 事务外校验：源群必须已清空，目标群行数必须与迁移数一致。
    left = {t: n for t, n in chat_row_counts(conn, source).items() if n}
    if left:
        raise MigrationError(f"迁移后源群仍有残留数据：{left}")
    return moved


def plan_json_migration(
    data_dir: str | Path, source: str, target: str,
) -> dict[str, int]:
    """只读预演：返回每个 JSON 文件会被迁移的条目数。"""
    _validate_ids(source, target)
    data_path = Path(data_dir)
    plan: dict[str, int] = {}
    for name in JSON_FILES:
        plan[name] = _json_move_count(data_path / name, source, target)
    return plan


def migrate_json(
    data_dir: str | Path, source: str, target: str,
    target_name: str = "",
) -> dict[str, int]:
    """把 data/ 下按 chat_id 索引的 JSON 条目移动到目标群。

    Args:
        target_name: 目标群显示名；提供时更新 group_names.json 里的名字。

    Returns:
        ``{filename: 移动条目数}``。文件不存在时计 0。
    """
    _validate_ids(source, target)
    data_path = Path(data_dir)
    moved: dict[str, int] = {}

    names = _read_json(data_path / GROUP_NAMES)
    if names is not None:
        entry = names.pop(source, None)
        if entry is not None:
            if target_name:
                entry["name"] = target_name
            names[target] = entry
            _write_json(data_path / GROUP_NAMES, names)
            moved[GROUP_NAMES] = 1
        else:
            moved[GROUP_NAMES] = 0

    for filename in (GROUP_MEMBERS, GROUP_NICKNAMES):
        data = _read_json(data_path / filename)
        if data is None:
            moved[filename] = 0
            continue
        entry = data.pop(source, None)
        if entry is not None:
            merged = dict(entry)
            merged.update(data.get(target, {}))  # 目标群已有昵称优先
            data[target] = merged
            _write_json(data_path / filename, data)
            moved[filename] = len(entry)
        else:
            moved[filename] = 0

    templates = _read_json(data_path / WELCOME_TEMPLATES)
    if templates is not None:
        mapping = templates.get("group_mapping")
        if isinstance(mapping, dict) and source in mapping:
            mapping[target] = mapping.pop(source)
            _write_json(data_path / WELCOME_TEMPLATES, templates)
            moved[WELCOME_TEMPLATES] = 1
        else:
            moved[WELCOME_TEMPLATES] = 0

    kicks = _read_json(data_path / PENDING_KICKS)
    if kicks is not None:
        count = 0
        prefix = f"{source}|"
        for key in [k for k in kicks if k.startswith(prefix)]:
            kicks[f"{target}|{key[len(prefix):]}"] = kicks.pop(key)
            count += 1
        if count:
            _write_json(data_path / PENDING_KICKS, kicks)
        moved[PENDING_KICKS] = count

    return moved


def _validate_ids(source: str, target: str) -> None:
    if not source or not target:
        raise MigrationError("源群和目标群不能为空")
    if source == target:
        raise MigrationError("源群和目标群是同一个 chat_id")


def _json_move_count(path: Path, source: str, target: str) -> int:
    data = _read_json(path)
    if data is None:
        return 0
    if path.name in (GROUP_MEMBERS, GROUP_NICKNAMES):
        entry = data.get(source)
        return len(entry) if isinstance(entry, dict) else (1 if entry else 0)
    if path.name == WELCOME_TEMPLATES:
        mapping = data.get("group_mapping")
        return 1 if isinstance(mapping, dict) and source in mapping else 0
    if path.name == PENDING_KICKS:
        return sum(1 for key in data if key.startswith(f"{source}|"))
    return 1 if source in data else 0


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"读取 {path} 失败：{exc}") from exc
    if not isinstance(data, dict):
        raise MigrationError(f"{path} 的顶层结构不是对象")
    return data


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
