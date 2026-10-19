"""Tests for WeChat 4.x system-message join detection."""

import time
import unittest

from src.config import BotConfig
from src.wechat.wcdb_backend import (
    WcdbBackend,
    _decode_wcdb_content,
    _extract_join_member,
)

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None


# Decoded from a real WeChat 4.x <sysmsgtemplate> join message.
SAMPLE_XML = """52566664210@chatroom:
<sysmsg type="sysmsgtemplate">
\t<sysmsgtemplate>
\t\t<content_template type="tmpl_type_profile">
\t\t\t<plain><![CDATA[]]></plain>
\t\t\t<template><![CDATA["$adder$"通过扫描"$from$"分享的二维码加入群聊]]></template>
\t\t\t<link_list>
\t\t\t\t<link name="adder" type="link_profile">
\t\t\t\t\t<memberlist>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_dmuofmyjq93v21]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[-]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t</memberlist>
\t\t\t\t</link>
\t\t\t\t<link name="from" type="link_profile">
\t\t\t\t\t<memberlist>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_0514035140022]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[萧山 -晚风]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t</memberlist>
\t\t\t\t</link>
\t\t\t</link_list>
\t\t</content_template>
\t</sysmsgtemplate>
</sysmsg>"""


class ExtractJoinMemberTests(unittest.TestCase):
    def test_extracts_adder_from_xml_template(self):
        self.assertEqual(_extract_join_member(SAMPLE_XML), "-")

    def test_resolves_adder_nickname(self):
        member = _extract_join_member(
            SAMPLE_XML,
            resolve_name=lambda wxid: (
                "新成员" if wxid == "wxid_dmuofmyjq93v21" else wxid
            ),
        )
        self.assertEqual(member, "新成员")

    def test_plain_text_join(self):
        self.assertEqual(_extract_join_member('"张三"加入了群聊'), "张三")
        self.assertEqual(
            _extract_join_member('"张三"通过扫描二维码加入群聊'), "张三"
        )

    def test_invite_join_targets_invitee(self):
        self.assertEqual(
            _extract_join_member('"张三"邀请"李四"加入了群聊'), "李四"
        )

    def test_non_join_message(self):
        self.assertEqual(
            _extract_join_member('"萧山 -晚风"被添加为群管理员'), ""
        )


@unittest.skipUnless(zstd is not None, "zstandard not installed")
class DecodeWcdbContentTests(unittest.TestCase):
    def test_decodes_zstd_hex(self):
        text = '"张三"加入了群聊'
        hexstr = zstd.ZstdCompressor().compress(
            text.encode("utf-8")
        ).hex()
        self.assertEqual(_decode_wcdb_content(hexstr), text)

    def test_plain_text_passthrough(self):
        self.assertEqual(
            _decode_wcdb_content('"张三"加入了群聊'),
            '"张三"加入了群聊',
        )


class _FakeClient:
    def resolve_nickname(self, wxid):
        if wxid == "wxid_dmuofmyjq93v21":
            return "新成员"
        return wxid


def _backend(welcome_enabled: bool) -> WcdbBackend:
    backend = WcdbBackend.__new__(WcdbBackend)
    backend._client = _FakeClient()
    backend._store = None
    backend._voice_config = BotConfig(welcome_enabled=welcome_enabled)
    backend._bot_name = "柒梦"
    return backend


def _join_msg(content: str) -> dict:
    return {
        "sender_username": "",
        "message_content": content,
        "local_type": 10000,
        "create_time": int(time.time()),
        "server_id": "1",
    }


class StandardizeJoinTests(unittest.TestCase):
    def test_xml_join_is_detected(self):
        result = _backend(welcome_enabled=True)._standardize(
            _join_msg(SAMPLE_XML), "摸鱼大王上线", "52566664210@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["is_system_join"])
        self.assertEqual(result["new_member_id"], "新成员")

    def test_compressed_xml_join_is_detected(self):
        if zstd is None:
            self.skipTest("zstandard not installed")
        hexstr = zstd.ZstdCompressor().compress(
            SAMPLE_XML.encode("utf-8")
        ).hex()
        result = _backend(welcome_enabled=True)._standardize(
            _join_msg(hexstr), "摸鱼大王上线", "52566664210@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["is_system_join"])

    def test_join_dropped_when_welcome_disabled(self):
        result = _backend(welcome_enabled=False)._standardize(
            _join_msg(SAMPLE_XML), "摸鱼大王上线", "52566664210@chatroom",
        )
        self.assertIsNone(result)

    def test_admin_message_is_not_a_join(self):
        result = _backend(welcome_enabled=True)._standardize(
            _join_msg('"萧山 -晚风"被添加为群管理员'),
            "摸鱼大王上线", "52566664210@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertFalse(result["is_system_join"])


if __name__ == "__main__":
    unittest.main()
