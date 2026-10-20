"""Tests for MessageStore query helpers used by the inactive-kick timer."""

import unittest

from src.db.schema import initialize_db
from src.db.store import MessageStore


class HasSenderMessageSinceTests(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insert(self, message_id, sender_id, timestamp):
        self.store.insert_message({
            "message_id": message_id,
            "chat_id": "g@chatroom",
            "sender_id": sender_id,
            "sender_name": sender_id,
            "content": "hi",
            "msg_type": 1,
            "timestamp": timestamp,
        })

    def test_true_when_message_after_since(self):
        self._insert("m1", "wxid_a", 1_000)
        self.assertTrue(
            self.store.has_sender_message_since("g@chatroom", "wxid_a", 900)
        )

    def test_false_when_only_older_messages(self):
        self._insert("m1", "wxid_a", 500)
        self.assertFalse(
            self.store.has_sender_message_since("g@chatroom", "wxid_a", 900)
        )

    def test_false_for_other_sender(self):
        self._insert("m1", "wxid_b", 1_000)
        self.assertFalse(
            self.store.has_sender_message_since("g@chatroom", "wxid_a", 900)
        )

    def test_false_when_empty(self):
        self.assertFalse(
            self.store.has_sender_message_since("g@chatroom", "wxid_a", 0)
        )


if __name__ == "__main__":
    unittest.main()
