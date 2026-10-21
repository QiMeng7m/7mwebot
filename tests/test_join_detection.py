"""Tests for WeChat 4.x system-message join detection."""

import tempfile
import time
import unittest
from pathlib import Path

from src.config import BotConfig
from src.wechat.wcdb_backend import (
    WcdbBackend,
    _decode_wcdb_content,
    _extract_join_member,
    _extract_join_member_info,
    _is_recent_join,
    _mention_name_for,
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


# Real WeChat 4.x invite message: the template only has a $names$
# placeholder, the actual member is in <link name="names">.  The
# <link name="revoke"> block must not be mistaken for the new member.
SAMPLE_NAMES_XML = """46074645339@chatroom:
<sysmsg type="sysmsgtemplate">
\t<sysmsgtemplate>
\t\t<content_template type="tmpl_type_profilewithrevoke">
\t\t\t<plain><![CDATA[]]></plain>
\t\t\t<template><![CDATA[你邀请"$names$"加入了群聊  $revoke$]]></template>
\t\t\t<link_list>
\t\t\t\t<link name="names" type="link_profile">
\t\t\t\t\t<memberlist>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_vvi4cxesb04k22]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[小柒]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t</memberlist>
\t\t\t\t\t<separator><![CDATA[、]]></separator>
\t\t\t\t</link>
\t\t\t\t<link name="revoke" type="link_revoke" hidden="1">
\t\t\t\t\t<title><![CDATA[撤销]]></title>
\t\t\t\t\t<usernamelist>
\t\t\t\t\t\t<username><![CDATA[wxid_vvi4cxesb04k22]]></username>
\t\t\t\t\t</usernamelist>
\t\t\t\t</link>
\t\t\t</link_list>
\t\t</content_template>
\t</sysmsgtemplate>
</sysmsg>"""


class ExtractJoinMemberTests(unittest.TestCase):
    def test_extracts_adder_from_xml_template(self):
        self.assertEqual(_extract_join_member(SAMPLE_XML), "-")

    def test_extracts_member_from_names_link(self):
        self.assertEqual(_extract_join_member(SAMPLE_NAMES_XML), "小柒")

    def test_join_info_includes_wxid(self):
        name, wxid = _extract_join_member_info(SAMPLE_NAMES_XML)
        self.assertEqual(name, "小柒")
        self.assertEqual(wxid, "wxid_vvi4cxesb04k22")

    def test_names_link_ignores_revoke_link(self):
        # Only a revoke link (no member link): don't return its username.
        xml = SAMPLE_NAMES_XML.split('<link name="names"', 1)[0] + """\
<link name="revoke" type="link_revoke" hidden="1">
\t<usernamelist>
\t\t<username><![CDATA[wxid_vvi4cxesb04k22]]></username>
\t</usernamelist>
</link></link_list></content_template></sysmsgtemplate></sysmsg>"""
        self.assertEqual(_extract_join_member(xml), "")

    def test_placeholder_without_member_link_is_rejected(self):
        self.assertEqual(_extract_join_member('你邀请"$names$"加入了群聊'), "")

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


class StandardizeMentionTests(unittest.TestCase):
    class _Client:
        def resolve_nickname(self, wxid):
            return {
                "wxid_b": "萧山-李四",
                "wxid_bot": "柒梦",
            }.get(wxid, wxid)

    def _standardize(self, content: str) -> dict:
        backend = WcdbBackend.__new__(WcdbBackend)
        backend._client = self._Client()
        backend._store = None
        backend._voice_config = BotConfig(welcome_enabled=False)
        backend._bot_name = "柒梦"
        return backend._standardize({
            "sender_username": "wxid_a",
            "message_content": content,
            "local_type": 1,
            "create_time": int(time.time()),
            "server_id": "m1",
        }, "测试群", "1@chatroom")

    def test_mention_wxid_is_kept_for_commands(self):
        result = self._standardize("wxid_a:\n@wxid_b 切磋")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "wxid_a:\n@萧山-李四 切磋")
        self.assertEqual(result["mentioned_ids"], ["wxid_b"])

    def test_bot_mention_is_not_a_command_target(self):
        result = self._standardize("wxid_a:\n@wxid_bot 签到")
        self.assertIsNotNone(result)
        self.assertEqual(result["mentioned_ids"], [])
        self.assertTrue(result["is_at_mentioned"])


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

    def test_names_link_join_uses_real_member(self):
        result = _backend(welcome_enabled=True)._standardize(
            _join_msg(SAMPLE_NAMES_XML), "想亲想爱一家人",
            "46074645339@chatroom",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["is_system_join"])
        self.assertEqual(result["new_member_id"], "小柒")
        self.assertEqual(result["new_member_wxid"], "wxid_vvi4cxesb04k22")

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


class MentionTargetTests(unittest.TestCase):
    def test_join_reply_mentions_new_member(self):
        self.assertEqual(
            _mention_name_for(
                "欢迎 @小柒 加入！",
                {"is_system_join": True, "new_member_id": "小柒"},
            ),
            "小柒",
        )

    def test_reply_mentions_sender_when_present(self):
        self.assertEqual(
            _mention_name_for(
                "@张三 测试总结",
                {"is_system_join": False, "sender_name": "张三"},
            ),
            "张三",
        )

    def test_no_mention_when_name_absent(self):
        self.assertIsNone(
            _mention_name_for(
                "总结完成",
                {"is_system_join": False, "sender_name": "张三"},
            )
        )


class RecentJoinTests(unittest.TestCase):
    def test_fresh_join_is_welcomed(self):
        now = 1_800_000_000
        self.assertTrue(_is_recent_join({"timestamp": now - 30}, now=now))

    def test_old_join_is_skipped(self):
        now = 1_800_000_000
        self.assertFalse(_is_recent_join({"timestamp": now - 600}, now=now))

    def test_missing_timestamp_is_not_welcomed(self):
        self.assertFalse(_is_recent_join({"timestamp": 0}))


class WelcomeResolveTests(unittest.TestCase):
    def test_default_template_mentions_once(self):
        from src.welcome import WelcomeManager
        with tempfile.TemporaryDirectory() as tmp:
            wm = WelcomeManager(path=Path(tmp) / "welcome.json")
            self.assertEqual(
                wm.resolve_message("123@chatroom", "小柒"),
                "欢迎 @小柒 加入群聊！🎉 10分钟内不参与聊天将被移除群聊哦~",
            )

    def test_bare_placeholder_still_mentions(self):
        from src.welcome import WelcomeManager
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "welcome.json"
            wm = WelcomeManager(path=path)
            wm.save({
                "templates": [
                    {"id": "t", "name": "t", "message": "欢迎 {new_member}"}
                ],
                "group_mapping": {},
                "default_template": "t",
            })
            self.assertEqual(
                wm.resolve_message("123@chatroom", "小柒"), "欢迎 @小柒"
            )


if __name__ == "__main__":
    unittest.main()
