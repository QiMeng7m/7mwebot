"""WECHAT_GROUPS name matching against WCDB display names."""

import unittest
from unittest.mock import patch

from src.wechat.wcdb_backend import (
    WcdbBackend,
    _group_name_matches,
    _normalize_group_name,
)


class GroupNameNormalizationTests(unittest.TestCase):
    def test_fullwidth_parens_match_ascii(self):
        self.assertTrue(_group_name_matches(
            "想亲想爱一家人（审核群）",
            "想亲想爱一家人(审核群)",
        ))

    def test_whitespace_and_zero_width_are_ignored(self):
        self.assertTrue(_group_name_matches(
            "想亲想爱一家人（审核群）",
            " 想亲想爱一家人​（审核群） ",
        ))

    def test_short_alias_still_matches(self):
        self.assertTrue(_group_name_matches(
            "想亲想爱一家人", "想亲想爱一家人（审核群）"))

    def test_different_groups_do_not_match(self):
        self.assertFalse(_group_name_matches(
            "想亲想爱一家人", "幸福的一家人"))
        self.assertFalse(_group_name_matches("", "任意群"))

    def test_normalization_casefolds(self):
        self.assertEqual(
            _normalize_group_name("ＣＡＲＬＹ 群"), "carly群")


class _FakeClient:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self):
        return self._sessions

    def get_group_nicknames(self, username):
        return {}

    def get_group_members(self, username):
        return []


class ResolveGroupsTests(unittest.TestCase):
    def _backend(self, groups, sessions):
        backend = WcdbBackend.__new__(WcdbBackend)
        backend._groups = list(groups)
        backend._talker_ids = {}
        backend._client = _FakeClient(sessions)
        backend._store = None
        backend._group_nicknames = {}
        backend._last_group_nick_refresh = 0.0
        return backend

    def _resolve(self, backend):
        with patch.object(WcdbBackend, "_save_group_names"), \
                patch.object(WcdbBackend, "_save_group_members"), \
                patch.object(WcdbBackend, "_save_group_nicknames"):
            backend._resolve_groups()

    def test_fullwidth_config_matches_ascii_display_name(self):
        backend = self._backend(
            ["想亲想爱一家人（审核群）"],
            [{"username": "123@chatroom",
              "displayName": "想亲想爱一家人(审核群)"}],
        )
        self._resolve(backend)
        self.assertEqual(
            backend._talker_ids,
            {"想亲想爱一家人（审核群）": "123@chatroom"},
        )

    def test_exact_match_wins_over_substring(self):
        backend = self._backend(
            ["想亲想爱一家人"],
            [
                {"username": "999@chatroom",
                 "displayName": "想亲想爱一家人（审核群）"},
                {"username": "123@chatroom",
                 "displayName": "想亲想爱一家人"},
            ],
        )
        self._resolve(backend)
        self.assertEqual(
            backend._talker_ids, {"想亲想爱一家人": "123@chatroom"})

    def test_chatroom_id_is_accepted(self):
        backend = self._backend(
            ["123@chatroom"],
            [{"username": "123@chatroom", "displayName": "任意群名"}],
        )
        self._resolve(backend)
        self.assertEqual(
            backend._talker_ids, {"123@chatroom": "123@chatroom"})


if __name__ == "__main__":
    unittest.main()
