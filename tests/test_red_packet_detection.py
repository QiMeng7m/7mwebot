"""Red packet / app-message detection in the WCDB backend.

WeChat 4.x encodes app messages as ``(subtype << 32) | 49`` and stores the
payload as zstd-compressed hex.  These tests pin down the unpacking and the
human-readable placeholders that replace the raw XML.
"""
import time
import unittest

from src.config import BotConfig
from src.wechat.wcdb_backend import (
    WcdbBackend,
    _app_message_label,
    _is_red_packet,
    _parse_app_message,
    _split_local_type,
)

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None


RED_PACKET_XML = """wxid_tnwakvqc5nzg12:
<msg>
  <appmsg appid="" sdkver="">
    <des><![CDATA[我发给你一个红包，快拆开看看!]]></des>
    <url><![CDATA[https://wxapp.tenpay.com/mmpayhb/wxhb_personalreceive?showwxpaytitle=1&msgtype=1]]></url>
    <type><![CDATA[2001]]></type>
    <title><![CDATA[恭喜发财]]></title>
    <wcpayinfo>
      <templateid><![CDATA[7a2a165d31da7fce6dd77e05c300028a]]></templateid>
    </wcpayinfo>
  </appmsg>
</msg>
"""

GENERIC_RED_PACKET_XML = """wxid_a:
<msg>
  <appmsg>
    <type>2001</type>
    <title>微信红包</title>
    <wcpayinfo><templateid>x</templateid></wcpayinfo>
  </appmsg>
</msg>
"""

TRANSFER_XML = """wxid_a:
<msg>
  <appmsg>
    <type>2000</type>
    <title>转账</title>
    <wcpayinfo><templateid>t</templateid></wcpayinfo>
  </appmsg>
</msg>
"""

QUOTE_XML = """wxid_a:
<msg>
  <appmsg>
    <type>57</type>
    <title>某某：你好</title>
    <refermsg><type>1</type><content>你好</content></refermsg>
  </appmsg>
</msg>
"""

LINK_XML = """wxid_a:
<msg>
  <appmsg>
    <type>5</type>
    <title>一篇文章</title>
    <url>https://mp.weixin.qq.com/s/abc</url>
  </appmsg>
</msg>
"""


class _FakeClient:
    def resolve_nickname(self, wxid):
        return {"wxid_a": "张三"}.get(wxid, wxid)


def _backend() -> WcdbBackend:
    backend = WcdbBackend.__new__(WcdbBackend)
    backend._client = _FakeClient()
    backend._store = None
    backend._voice_config = BotConfig(welcome_enabled=False)
    backend._bot_name = "柒梦"
    backend._bot_aliases = []
    return backend


def _standardize(content: str, local_type: int) -> dict:
    return _backend()._standardize({
        "sender_username": "wxid_a",
        "message_content": content,
        "local_type": local_type,
        "create_time": int(time.time()),
        "server_id": "m1",
    }, "测试群", "1@chatroom")


def _zstd_hex(text: str) -> str:
    return zstd.ZstdCompressor().compress(text.encode("utf-8")).hex()


class SplitLocalTypeTests(unittest.TestCase):
    def test_red_packet_packed_type(self):
        self.assertEqual(_split_local_type(8594229559345), (49, 2001))

    def test_plain_type_has_no_subtype(self):
        self.assertEqual(_split_local_type(1), (1, 0))
        self.assertEqual(_split_local_type(10000), (10000, 0))


class AppMessageParseTests(unittest.TestCase):
    def test_parses_red_packet_xml(self):
        info = _parse_app_message(RED_PACKET_XML)
        self.assertEqual(info["app_type"], "2001")
        self.assertEqual(info["title"], "恭喜发财")
        self.assertTrue(info["is_red_packet"])

    def test_red_packet_without_type_uses_wcpayinfo(self):
        xml = "<msg><appmsg><wcpayinfo><x/></wcpayinfo></appmsg></msg>"
        info = _parse_app_message(xml)
        self.assertTrue(info["is_red_packet"])

    def test_red_packet_without_type_uses_url_marker(self):
        xml = (
            "<msg><appmsg><type></type><url>"
            "https://wxapp.tenpay.com/mmpayhb/wxhb_personalreceive?x=1"
            "</url></appmsg></msg>"
        )
        self.assertTrue(_parse_app_message(xml)["is_red_packet"])

    def test_non_appmsg_text_returns_empty(self):
        self.assertEqual(_parse_app_message("普通聊天 < 表情"), {})

    def test_quote_keeps_refermsg_type_out_of_app_type(self):
        info = _parse_app_message(QUOTE_XML)
        self.assertEqual(info["app_type"], "57")
        self.assertFalse(info["is_red_packet"])


class AppMessageLabelTests(unittest.TestCase):
    def test_red_packet_includes_custom_title(self):
        self.assertEqual(
            _app_message_label(_parse_app_message(RED_PACKET_XML), 2001),
            "[红包] 恭喜发财",
        )

    def test_generic_red_packet_title_is_omitted(self):
        self.assertEqual(
            _app_message_label(
                _parse_app_message(GENERIC_RED_PACKET_XML), 2001,
            ),
            "[红包]",
        )

    def test_transfer_label(self):
        info = _parse_app_message(TRANSFER_XML)
        self.assertTrue(_is_red_packet(info, 2000) is False)
        self.assertEqual(_app_message_label(info, 2000), "[转账]")

    def test_quote_label(self):
        self.assertEqual(
            _app_message_label(_parse_app_message(QUOTE_XML), 57), "[引用]",
        )

    def test_unknown_app_without_url_is_message(self):
        self.assertEqual(_app_message_label({}, 0), "[消息]")

    def test_unknown_app_with_url_is_link(self):
        self.assertEqual(
            _app_message_label({"url": "https://x"}, 0), "[链接]",
        )


class StandardizeAppMessageTests(unittest.TestCase):
    def test_plain_red_packet_xml(self):
        result = _standardize(RED_PACKET_XML, (2001 << 32) | 49)
        self.assertIsNotNone(result)
        self.assertEqual(result["msg_type"], 49)
        self.assertEqual(result["app_subtype"], 2001)
        self.assertEqual(result["app_type"], "2001")
        self.assertTrue(result["is_red_packet"])
        self.assertEqual(result["red_packet_title"], "恭喜发财")
        self.assertEqual(result["content"], "[红包] 恭喜发财")

    def test_unpacked_type_still_detected(self):
        """Older WeChat builds return a plain ``49`` rather than packed."""
        result = _standardize(GENERIC_RED_PACKET_XML, 49)
        self.assertTrue(result["is_red_packet"])
        self.assertEqual(result["content"], "[红包]")

    def test_non_red_app_message_is_not_flagged(self):
        result = _standardize(QUOTE_XML, (57 << 32) | 49)
        self.assertIsNotNone(result)
        self.assertFalse(result["is_red_packet"])
        self.assertEqual(result["content"], "[引用]")

    def test_empty_type_49_does_not_crash(self):
        result = _standardize("", 49)
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "[消息]")
        self.assertFalse(result["is_red_packet"])

    def test_text_message_unchanged(self):
        result = _standardize("wxid_a:\n大家晚上好", 1)
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "wxid_a:\n大家晚上好")
        self.assertEqual(result["msg_type"], 1)
        self.assertFalse(result["is_red_packet"])

    @unittest.skipUnless(zstd is not None, "zstandard not installed")
    def test_zstd_compressed_red_packet_hex(self):
        result = _standardize(_zstd_hex(RED_PACKET_XML), (2001 << 32) | 49)
        self.assertIsNotNone(result)
        self.assertTrue(result["is_red_packet"])
        self.assertEqual(result["content"], "[红包] 恭喜发财")
        self.assertEqual(result["red_packet_title"], "恭喜发财")

    @unittest.skipUnless(zstd is not None, "zstandard not installed")
    def test_zstd_compressed_image_becomes_placeholder(self):
        # Media rows are compressed too; the AI prompt must not see hex.
        result = _standardize(_zstd_hex("wxid_a:\n<msg/>"), 3)
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "[图片]")
        self.assertEqual(result["msg_type"], 3)


if __name__ == "__main__":
    unittest.main()
