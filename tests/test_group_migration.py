"""群数据迁移测试 — 数据库归属改绑和 JSON 缓存移动。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.db.group_migration import (
    MigrationError,
    list_chat_tables,
    migrate_db,
    migrate_json,
    plan_db_migration,
    plan_json_migration,
)
from src.db.schema import initialize_db

SRC = "111@chatroom"
DST = "222@chatroom"

_TODOS_DDL = """
CREATE TABLE IF NOT EXISTS todos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    display_order   INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active',
    creator_id      TEXT    NOT NULL DEFAULT '',
    creator_name    TEXT    NOT NULL DEFAULT '',
    created_at      REAL    NOT NULL
);
"""


class DbMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "messages.db"
        self.conn = initialize_db(str(self.db_path))
        self._seed()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self):
        conn = self.conn
        conn.execute(_TODOS_DDL)
        conn.execute(
            "INSERT INTO messages(message_id, chat_id, sender_id, sender_name,"
            " content, msg_type, timestamp) VALUES(?,?,?,?,?,?,?)",
            ("m1", SRC, "u1", "Alice", "hello", 1, 100),
        )
        conn.execute(
            "INSERT INTO point_events(chat_id, sender_id, sender_name,"
            " message_id, reason, category, points, day, season, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (SRC, "u1", "Alice", "m1", "base", "earn", 3,
             "2026-11-17", "2026-11", 100),
        )
        conn.execute(
            "INSERT INTO group_memory(chat_id, memory_text, message_count)"
            " VALUES(?,?,?)",
            (SRC, "old memory", 1),
        )
        conn.execute(
            "INSERT INTO user_last_message(chat_id, sender_id, sender_name,"
            " last_timestamp) VALUES(?,?,?,?)",
            (SRC, "u1", "Alice", 100),
        )
        conn.execute(
            "INSERT INTO todos(chat_id, content, display_order, created_at)"
            " VALUES(?,?,?,?)",
            (SRC, "buy milk", 1, 100),
        )
        conn.commit()

    def test_list_chat_tables(self):
        tables = list_chat_tables(self.conn)
        self.assertIn("messages", tables)
        self.assertIn("point_events", tables)
        self.assertIn("todos", tables)
        self.assertNotIn("sqlite_sequence", tables)

    def test_plan_reports_counts(self):
        plan = plan_db_migration(self.conn, SRC, DST)
        self.assertEqual(plan["source_counts"]["messages"], 1)
        self.assertEqual(plan["source_counts"]["point_events"], 1)
        self.assertEqual(plan["target_counts"]["messages"], 0)
        self.assertEqual(plan["conflicts"], [])
        self.assertGreater(plan["source_total"], 0)

    def test_plan_rejects_same_chat(self):
        with self.assertRaises(MigrationError):
            plan_db_migration(self.conn, SRC, SRC)

    def test_migrate_moves_every_chat_table(self):
        moved = migrate_db(self.conn, SRC, DST)
        self.assertEqual(moved["messages"], 1)
        self.assertEqual(moved["point_events"], 1)
        self.assertEqual(moved["group_memory"], 1)
        self.assertEqual(moved["user_last_message"], 1)
        self.assertEqual(moved["todos"], 1)

        counts = plan_db_migration(self.conn, SRC, DST)
        self.assertEqual(counts["source_total"], 0)
        self.assertEqual(counts["target_counts"]["messages"], 1)
        self.assertEqual(counts["target_counts"]["todos"], 1)

        row = self.conn.execute(
            "SELECT chat_id, content FROM messages WHERE message_id='m1'"
        ).fetchone()
        self.assertEqual(tuple(row), (DST, "hello"))

    def test_target_with_existing_rows_is_rejected(self):
        self.conn.execute(
            "INSERT INTO messages(message_id, chat_id, sender_id, sender_name,"
            " content, msg_type, timestamp) VALUES(?,?,?,?,?,?,?)",
            ("m2", DST, "u2", "Bob", "existing", 1, 200),
        )
        self.conn.commit()
        with self.assertRaises(MigrationError):
            migrate_db(self.conn, SRC, DST)
        # 源群数据必须原样保留
        left = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id=?", (SRC,)
        ).fetchone()[0]
        self.assertEqual(left, 1)


class JsonMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self._write("group_names.json", {
            SRC: {"name": "旧群", "member_count": 36},
        })
        self._write("group_members.json", {
            SRC: {"u1": "Alice"},
        })
        self._write("group_nicknames.json", {
            SRC: {"u1": "群昵称"},
            DST: {"u2": "目标已有"},
        })
        self._write("welcome_templates.json", {
            "templates": [],
            "group_mapping": {SRC: "tpl_default"},
            "default_template": "tpl_default",
        })
        self._write("pending_kicks.json", {
            f"{SRC}|u9": 123,
            "999@chatroom|u8": 456,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        (self.data / name).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def _read(self, name):
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def test_plan_counts(self):
        plan = plan_json_migration(self.data, SRC, DST)
        self.assertEqual(plan["group_names.json"], 1)
        self.assertEqual(plan["group_members.json"], 1)
        self.assertEqual(plan["group_nicknames.json"], 1)
        self.assertEqual(plan["welcome_templates.json"], 1)
        self.assertEqual(plan["pending_kicks.json"], 1)

    def test_migrate_moves_keys(self):
        moved = migrate_json(self.data, SRC, DST, target_name="新群")
        self.assertGreater(sum(moved.values()), 0)

        names = self._read("group_names.json")
        self.assertNotIn(SRC, names)
        self.assertEqual(names[DST]["name"], "新群")
        self.assertEqual(names[DST]["member_count"], 36)

        members = self._read("group_members.json")
        self.assertNotIn(SRC, members)
        self.assertEqual(members[DST], {"u1": "Alice"})

        nicknames = self._read("group_nicknames.json")
        self.assertNotIn(SRC, nicknames)
        # 目标群已有的同名 wxid 昵称优先
        self.assertEqual(nicknames[DST], {"u1": "群昵称", "u2": "目标已有"})

        templates = self._read("welcome_templates.json")
        self.assertEqual(templates["group_mapping"], {DST: "tpl_default"})

        kicks = self._read("pending_kicks.json")
        self.assertIn(f"{DST}|u9", kicks)
        self.assertNotIn(f"{SRC}|u9", kicks)
        self.assertIn("999@chatroom|u8", kicks)

    def test_missing_files_are_ignored(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        moved = migrate_json(empty, SRC, DST)
        self.assertEqual(sum(moved.values()), 0)


if __name__ == "__main__":
    unittest.main()
