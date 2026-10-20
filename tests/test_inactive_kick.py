"""Tests for the inactive-member kick tracker."""

import tempfile
import unittest
from pathlib import Path

from src.wechat.inactive_kick import InactiveKickTracker


class _Clock:
    def __init__(self, now=1_800_000_000):
        self.now = now

    def __call__(self):
        return self.now


class InactiveKickTrackerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "pending_kicks.json"
        self.clock = _Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def _tracker(self, timeout=600):
        return InactiveKickTracker(
            timeout_sec=timeout, path=self.path, clock=self.clock,
        )

    def test_join_is_due_after_timeout(self):
        tracker = self._tracker()
        tracker.note_join("g@chatroom", "群", "wxid_a", "张三")
        self.assertEqual(tracker.due(), [])
        self.clock.now += 599
        self.assertEqual(tracker.due(), [])
        self.clock.now += 1
        self.assertEqual(len(tracker.due()), 1)
        self.assertEqual(tracker.due()[0]["display_name"], "张三")

    def test_activity_cancels_deadline(self):
        tracker = self._tracker()
        tracker.note_join("g@chatroom", "群", "wxid_a", "张三")
        tracker.note_activity("g@chatroom", "wxid_a")
        self.clock.now += 3600
        self.assertEqual(tracker.due(), [])

    def test_remove_drops_entry(self):
        tracker = self._tracker()
        tracker.note_join("g@chatroom", "群", "wxid_a", "张三")
        self.clock.now += 601
        tracker.remove("g@chatroom", "wxid_a")
        self.assertEqual(tracker.due(), [])
        self.assertEqual(tracker.pending(), [])

    def test_persists_and_reloads(self):
        tracker = self._tracker()
        tracker.note_join("g@chatroom", "群", "wxid_a", "张三")
        self.assertTrue(self.path.exists())
        reloaded = InactiveKickTracker(
            timeout_sec=600, path=self.path, clock=self.clock,
        )
        pending = reloaded.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["wxid"], "wxid_a")

    def test_expired_entries_are_dropped_on_load(self):
        tracker = self._tracker()
        tracker.note_join("g@chatroom", "群", "wxid_a", "张三")
        self.clock.now += 601
        reloaded = InactiveKickTracker(
            timeout_sec=600, path=self.path, clock=self.clock,
        )
        self.assertEqual(reloaded.pending(), [])

    def test_join_without_wxid_is_ignored(self):
        tracker = self._tracker()
        self.assertFalse(
            tracker.note_join("g@chatroom", "群", "", "张三")
        )
        self.assertEqual(tracker.pending(), [])


if __name__ == "__main__":
    unittest.main()
