"""Tests for leave-group ("退群") detection and warning."""

import threading
import time
import unittest

from src.config import BotConfig
from src.wechat.helpers import (
    LEAVE_WARNING_TEMPLATE,
    REMOVE_WARNING_TEMPLATE,
    extract_departure_member,
    extract_leave_member,
    extract_removed_member,
)
from src.wechat.wcdb_backend import (
    WcdbBackend,
    _extract_leave_member_info,
    _is_recent_event,
)

# WeChat 4.x leave template: the name lives in the "names" link block.
SAMPLE_LEAVE_XML = """46074645339@chatroom:
<sysmsg type="sysmsgtemplate">
\t<sysmsgtemplate>
\t\t<content_template type="tmpl_type_profile">
\t\t\t<template><![CDATA["$names$"退出了群聊]]></template>
\t\t\t<link_list>
\t\t\t\t<link name="names" type="link_profile">
\t\t\t\t\t<memberlist>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_leave123]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[张三]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t</memberlist>
\t\t\t\t</link>
\t\t\t</link_list>
\t\t</content_template>
\t</sysmsgtemplate>
</sysmsg>"""


SAMPLE_REMOVE_XML = """46074645339@chatroom:
<sysmsg type="sysmsgtemplate">
\t<sysmsgtemplate>
\t\t<content_template type="tmpl_type_profile">
\t\t\t<template><![CDATA["$names$"被移出了群聊]]></template>
\t\t\t<link_list>
\t\t\t\t<link name="names" type="link_profile">
\t\t\t\t\t<memberlist>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_remove456]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[王五]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t</memberlist>
\t\t\t\t</link>
\t\t\t</link_list>
\t\t</content_template>
\t</sysmsgtemplate>
</sysmsg>"""


class ExtractLeaveMemberTests(unittest.TestCase):
    def test_plain_text_leave(self):
        self.assertEqual(extract_leave_member('"张三"退出了群聊'), "张三")
        self.assertEqual(extract_leave_member('"张三"退出群聊'), "张三")

    def test_plain_text_removal(self):
        self.assertEqual(extract_removed_member('"张三"被移出了群聊'), "张三")
        self.assertEqual(extract_removed_member('你将"张三"移出了群聊'), "张三")
        self.assertEqual(
            extract_removed_member('"李四"将"张三"移出了群聊'), "张三",
        )
        self.assertEqual(
            extract_removed_member('"张三"被群主移出了群聊'), "张三",
        )
        # WeChat may use Chinese curly quotes.
        self.assertEqual(extract_removed_member('“张三”被移出了群聊'), "张三")
        self.assertEqual(extract_removed_member('你将“张三”移出了群聊'), "张三")
        self.assertEqual(extract_leave_member('“张三”退出了群聊'), "张三")

    def test_departure_kind(self):
        self.assertEqual(
            extract_departure_member('"张三"退出了群聊'), ("张三", "leave"),
        )
        self.assertEqual(
            extract_departure_member('"张三"被移出了群聊'), ("张三", "remove"),
        )
        self.assertEqual(extract_departure_member('"张三"加入了群聊'), ("", ""))

    def test_placeholder_is_rejected(self):
        self.assertEqual(extract_leave_member('"$names$"退出了群聊'), "")
        self.assertEqual(extract_removed_member('"$names$"被移出了群聊'), "")

    def test_non_leave_message(self):
        self.assertEqual(extract_leave_member('"张三"加入了群聊'), "")
        self.assertEqual(extract_leave_member("今天天气不错"), "")

    def test_xml_names_link(self):
        name, wxid = _extract_leave_member_info(SAMPLE_LEAVE_XML)
        self.assertEqual(name, "张三")
        self.assertEqual(wxid, "wxid_leave123")

    def test_xml_name_can_be_resolved(self):
        name, wxid = _extract_leave_member_info(
            SAMPLE_LEAVE_XML,
            resolve_name=lambda wxid: "群昵称-张三" if wxid else "",
        )
        self.assertEqual(name, "群昵称-张三")
        self.assertEqual(wxid, "wxid_leave123")

    def test_warning_text_includes_member(self):
        self.assertEqual(
            LEAVE_WARNING_TEMPLATE.format(member="张三"),
            "⚠️ 退群警告：张三 已退出群聊",
        )
        self.assertEqual(
            REMOVE_WARNING_TEMPLATE.format(member="王五"),
            "⚠️ 退群警告：王五 已被移出群聊",
        )


class _FakeClient:
    def resolve_nickname(self, wxid):
        return {
            "wxid_leave123": "张三",
            "wxid_remove456": "王五",
        }.get(wxid, wxid)


def _leave_msg(content: str, local_type: int = 10000) -> dict:
    return {
        "sender_username": "",
        "message_content": content,
        "local_type": local_type,
        "create_time": int(time.time()),
        "server_id": "1",
    }


def _backend(welcome_enabled: bool = False) -> WcdbBackend:
    backend = WcdbBackend.__new__(WcdbBackend)
    backend._client = _FakeClient()
    backend._store = None
    backend._voice_config = BotConfig(welcome_enabled=welcome_enabled)
    backend._bot_name = "柒梦"
    backend._bot_aliases = []
    return backend


class StandardizeLeaveTests(unittest.TestCase):
    def test_leave_xml_is_detected(self):
        result = _backend()._standardize(
            _leave_msg(SAMPLE_LEAVE_XML), "想亲想爱一家人",
            "46074645339@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["is_system_leave"])
        self.assertFalse(result["is_system_join"])
        self.assertEqual(result["left_member_id"], "张三")
        self.assertEqual(result["left_member_wxid"], "wxid_leave123")
        self.assertEqual(result["leave_kind"], "leave")
        self.assertIn("退出了群聊", result["content"])

    def test_removal_xml_is_detected(self):
        result = _backend()._standardize(
            _leave_msg(SAMPLE_REMOVE_XML), "想亲想爱一家人",
            "46074645339@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["is_system_leave"])
        self.assertFalse(result["is_system_join"])
        self.assertEqual(result["left_member_id"], "王五")
        self.assertEqual(result["left_member_wxid"], "wxid_remove456")
        self.assertEqual(result["leave_kind"], "remove")

    def test_plain_removal_is_detected(self):
        result = _backend()._standardize(
            _leave_msg('你将"王五"移出了群聊'), "想亲想爱一家人",
            "46074645339@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["is_system_leave"])
        self.assertEqual(result["left_member_id"], "王五")
        self.assertEqual(result["leave_kind"], "remove")

    def test_leave_is_detected_even_when_welcome_disabled(self):
        result = _backend(welcome_enabled=False)._standardize(
            _leave_msg('"张三"退出了群聊'), "想亲想爱一家人",
            "46074645339@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["is_system_leave"])
        self.assertEqual(result["left_member_id"], "张三")

    def test_leave_message_id_is_stable(self):
        msg = _leave_msg(SAMPLE_LEAVE_XML)
        backend = _backend()
        first = backend._standardize(msg, "群", "1@chatroom")
        second = backend._standardize(msg, "群", "1@chatroom")
        self.assertEqual(first["message_id"], second["message_id"])

    def test_normal_chat_mentioning_leave_is_not_an_event(self):
        result = _backend()._standardize(
            _leave_msg('他说"张三"退出了群聊', local_type=1),
            "群", "1@chatroom",
        )
        # System-message keyword filtering drops it, but it must never be
        # treated as a leave event and trigger a warning.
        self.assertTrue(result is None or not result["is_system_leave"])

    def test_stale_leave_event_is_not_recent(self):
        now = 1_800_000_000
        self.assertTrue(_is_recent_event({"timestamp": now - 10}, now=now))
        self.assertFalse(_is_recent_event({"timestamp": now - 600}, now=now))


class _FakeWindow:
    def __init__(self):
        self.calls = []

    def send_to_chat(self, group_name, content, mention_name=None):
        self.calls.append((group_name, content, mention_name))
        return True


class SendLeaveWarningTests(unittest.TestCase):
    def _backend_with_window(self):
        backend = _backend()
        backend._window = _FakeWindow()
        backend._window_lock = threading.Lock()
        return backend

    def test_sends_hardcoded_leave_warning(self):
        backend = self._backend_with_window()
        backend._send_leave_warning("想亲想爱一家人", "1@chatroom", "张三")
        self.assertEqual(
            backend._window.calls,
            [("想亲想爱一家人", "⚠️ 退群警告：张三 已退出群聊", None)],
        )

    def test_sends_hardcoded_remove_warning(self):
        backend = self._backend_with_window()
        backend._send_leave_warning(
            "想亲想爱一家人", "1@chatroom", "王五", "remove",
        )
        self.assertEqual(
            backend._window.calls,
            [("想亲想爱一家人", "⚠️ 退群警告：王五 已被移出群聊", None)],
        )


class PollLeaveDispatchTests(unittest.TestCase):
    def _run_poll(self, content):
        backend = _backend()
        backend._running = True
        backend._client_lock = threading.Lock()
        backend._known_ids = set()
        backend._pool = None
        backend._kick_tracker = None
        backend._client = type(
            "Client", (), {
                "get_messages": lambda self, **kw: [_leave_msg(content)],
                "resolve_nickname": lambda self, wxid: {
                    "wxid_leave123": "张三",
                    "wxid_remove456": "王五",
                }.get(wxid, wxid),
            },
        )()
        warnings = []
        backend._send_leave_warning = (
            lambda group, talker, name, kind="leave":
            warnings.append((group, talker, name, kind))
        )
        seen = []
        backend._poll_group(
            "想亲想爱一家人", "1@chatroom", lambda msg: seen.append(msg),
        )
        return warnings, seen

    def test_poll_group_warns_and_skips_callback(self):
        warnings, seen = self._run_poll('"张三"退出了群聊')
        self.assertEqual(
            warnings,
            [("想亲想爱一家人", "1@chatroom", "张三", "leave")],
        )
        self.assertEqual(seen, [])

    def test_poll_group_warns_on_removal(self):
        warnings, seen = self._run_poll('"王五"被移出了群聊')
        self.assertEqual(
            warnings,
            [("想亲想爱一家人", "1@chatroom", "王五", "remove")],
        )
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
