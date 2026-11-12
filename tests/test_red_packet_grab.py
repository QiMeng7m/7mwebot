"""Red packet auto-grab: item picking, claim button and backend wiring.

The live WeChat window is never touched here.  UIA calls are replaced by
fakes so the matching/decision logic can be tested deterministically.
"""
import threading
import time
import unittest
from unittest import mock

from src.config import BotConfig
from src.wechat.wcdb_backend import WcdbBackend, _is_recent_within
from src.wechat.window_controller import WeChatWindowController


class _FakeItem:
    ControlTypeName = "ListItemControl"

    def __init__(self, name: str, class_name: str = "mmui::ChatTextItemView"):
        self.Name = name
        self.ClassName = class_name


class _FakeList:
    ControlTypeName = "ListControl"
    ClassName = "mmui::RecyclerListView"
    Name = "消息"

    def __init__(self, items):
        self._items = items

    def GetChildren(self):
        return list(self._items)


class _FakeButton:
    ControlTypeName = "ButtonControl"

    def __init__(self, name: str):
        self.Name = name


def _controller_with(items) -> WeChatWindowController:
    ctrl = WeChatWindowController()
    ctrl._find_message_list = lambda _hwnd: _FakeList(items)
    return ctrl


class RedPacketItemPickerTests(unittest.TestCase):
    def test_picks_newest_item_with_red_packet_in_name(self):
        items = [
            _FakeItem("15:00", "mmui::ChatItemView"),
            _FakeItem("微信红包", "mmui::ChatBubbleItemView"),
            _FakeItem("普通聊天"),
        ]
        # The red packet bubble sits above the newest text message; it
        # must still be picked, not the last item blindly.
        picked = _controller_with(items)._find_red_packet_item(1, "")
        self.assertIs(picked, items[1])

    def test_picks_by_greeting_title(self):
        items = [
            _FakeItem("普通聊天"),
            _FakeItem("恭喜发财，大吉大利"),
        ]
        picked = _controller_with(items)._find_red_packet_item(
            1, "恭喜发财，大吉大利",
        )
        self.assertIs(picked, items[1])

    def test_generic_title_is_not_used_for_matching(self):
        # "微信红包" is the generic appmsg title; matching it against
        # unrelated text would be meaningless.
        items = [_FakeItem("微信红包")]
        picked = _controller_with(items)._find_red_packet_item(1, "微信红包")
        # Name still contains 红包, so it matches via the name rule.
        self.assertIs(picked, items[0])

    def test_picks_by_class_fragment(self):
        items = [
            _FakeItem("恭喜发财", "mmui::ChatRedEnvelopeItemView"),
        ]
        picked = _controller_with(items)._find_red_packet_item(1, "")
        self.assertIs(picked, items[0])

    def test_returns_none_when_no_red_packet(self):
        items = [_FakeItem("普通聊天"), _FakeItem("图片", "mmui::ChatImageItemView")]
        self.assertIsNone(_controller_with(items)._find_red_packet_item(1, ""))

    def test_falls_back_to_newest_generic_bubble(self):
        items = [
            _FakeItem("普通聊天"),
            _FakeItem("恭喜发财，大吉大利", "mmui::ChatBubbleItemView"),
        ]
        ctrl = _controller_with(items)
        ctrl._looks_like_red_packet = lambda _item: True
        picked = ctrl._find_red_packet_item(1, "")
        self.assertIs(picked, items[1])

    def test_generic_bubble_rejected_without_orange(self):
        items = [_FakeItem("一篇文章", "mmui::ChatBubbleItemView")]
        ctrl = _controller_with(items)
        ctrl._looks_like_red_packet = lambda _item: False
        self.assertIsNone(ctrl._find_red_packet_item(1, ""))

    def test_does_not_fall_back_to_sticker_bubble(self):
        items = [
            _FakeItem("动画表情", "mmui::ChatBubbleReferItemView"),
        ]
        self.assertIsNone(_controller_with(items)._find_red_packet_item(1, ""))

    def test_returns_none_without_message_list(self):
        ctrl = WeChatWindowController()
        ctrl._find_message_list = lambda _hwnd: None
        self.assertIsNone(ctrl._find_red_packet_item(1, "恭喜发财"))


class _Rect:
    left, top, right, bottom = 100, 100, 600, 180


class _ClickableItem:
    def __init__(self):
        self.BoundingRectangle = _Rect()
        self.clicks: list[tuple[int, int]] = []

    def Click(self, x, y):
        self.clicks.append((x, y))


class ColorCheckTests(unittest.TestCase):
    def _check(self, color):
        from PIL import Image
        item = _ClickableItem()
        image = Image.new("RGB", (100, 40), color)
        with mock.patch(
            "src.wechat.window_controller.ImageGrab.grab",
            return_value=image,
        ):
            return WeChatWindowController()._looks_like_red_packet(item)

    def test_wechat_orange_detected(self):
        self.assertTrue(self._check((250, 157, 59)))

    def test_custom_red_cover_detected(self):
        self.assertTrue(self._check((220, 70, 60)))

    def test_gray_rejected(self):
        self.assertFalse(self._check((120, 120, 120)))

    def test_screenshot_error_rejected(self):
        item = _ClickableItem()
        with mock.patch(
            "src.wechat.window_controller.ImageGrab.grab",
            side_effect=OSError("no screen"),
        ):
            self.assertFalse(
                WeChatWindowController()._looks_like_red_packet(item)
            )


class OverlayOpenTests(unittest.TestCase):
    def test_clicks_inside_left_bubble_not_row_center(self):
        ctrl = WeChatWindowController()
        item = _ClickableItem()
        sentinel = object()
        ctrl._find_wechat_control = lambda _pid, _cls: sentinel
        info = ctrl._open_red_packet_overlay(1, 1, item)
        self.assertIs(info, sentinel)
        self.assertEqual(len(item.clicks), 1)
        dx, dy = item.clicks[0]
        # Row is 500 px wide; the bubble starts near the left edge.
        self.assertLess(dx, 200)
        self.assertEqual(dy, 40)

    def test_retries_second_point_when_overlay_missing(self):
        ctrl = WeChatWindowController()
        item = _ClickableItem()
        ctrl.RED_PACKET_POPUP_DELAY = 0.15
        calls = {"n": 0}
        sentinel = object()

        def fake_find(_pid, _cls):
            calls["n"] += 1
            return sentinel if calls["n"] >= 2 else None

        ctrl._find_wechat_control = fake_find
        info = ctrl._open_red_packet_overlay(1, 1, item)
        self.assertIs(info, sentinel)
        self.assertEqual(len(item.clicks), 2)
        self.assertLess(item.clicks[1][0], item.clicks[0][0])


class ClaimButtonTests(unittest.TestCase):
    def _run(self, buttons, allow_short: bool):
        ctrl = WeChatWindowController()
        clicked = []
        ctrl._click_control = lambda c: clicked.append(c.Name) or True
        with mock.patch("uiautomation.WalkControl",
                        return_value=[(b, 0) for b in buttons]):
            ok = ctrl._click_open_button(object(), allow_short=allow_short)
        return ok, clicked

    def test_clicks_chaikai(self):
        ok, clicked = self._run([_FakeButton("拆开")], allow_short=True)
        self.assertTrue(ok)
        self.assertEqual(clicked, ["拆开"])

    def test_short_open_button_only_when_allowed(self):
        ok, clicked = self._run([_FakeButton("开")], allow_short=False)
        self.assertFalse(ok)
        self.assertEqual(clicked, [])

        ok, clicked = self._run([_FakeButton("开")], allow_short=True)
        self.assertTrue(ok)
        self.assertEqual(clicked, ["开"])

    def test_falls_back_to_lingqu_hongbao(self):
        ok, clicked = self._run([_FakeButton("领取红包")], allow_short=False)
        self.assertTrue(ok)
        self.assertEqual(clicked, ["领取红包"])

    def test_ignores_unrelated_buttons(self):
        ok, _ = self._run([_FakeButton("关闭"), _FakeButton("发送")],
                          allow_short=True)
        self.assertFalse(ok)


class _FakeWindow:
    def __init__(self, result: bool = True):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def grab_red_packet(self, group_name: str, title: str = "") -> bool:
        self.calls.append((group_name, title))
        return self.result


def _backend(config: BotConfig, window: _FakeWindow) -> WcdbBackend:
    backend = WcdbBackend.__new__(WcdbBackend)
    backend._voice_config = config
    backend._window = window
    backend._window_lock = threading.Lock()
    backend._running = True
    return backend


class BackendGrabWiringTests(unittest.TestCase):
    def test_enabled_by_default(self):
        backend = _backend(BotConfig(), _FakeWindow())
        self.assertTrue(backend._red_packet_grab_enabled())

    def test_disable_flag(self):
        backend = _backend(
            BotConfig(red_packet_auto_grab_enabled=False), _FakeWindow(),
        )
        self.assertFalse(backend._red_packet_grab_enabled())

    def test_grab_uses_window_controller_and_title(self):
        window = _FakeWindow(True)
        backend = _backend(BotConfig(), window)
        backend._grab_red_packet(
            "测试群",
            {"red_packet_title": "恭喜发财", "sender_name": "张三"},
        )
        self.assertEqual(window.calls, [("测试群", "恭喜发财")])

    def test_grab_failure_is_not_raised(self):
        window = _FakeWindow(False)
        backend = _backend(BotConfig(), window)
        with self.assertLogs("src.wechat.wcdb_backend", level="WARNING"):
            backend._grab_red_packet(
                "测试群", {"red_packet_title": "", "sender_name": "张三"},
            )

    def test_grab_skipped_when_stopped(self):
        window = _FakeWindow(True)
        backend = _backend(BotConfig(), window)
        backend._running = False
        backend._grab_red_packet("测试群", {})
        self.assertEqual(window.calls, [])


class RecentWindowTests(unittest.TestCase):
    def test_recent_message(self):
        now = time.time()
        self.assertTrue(
            _is_recent_within({"timestamp": int(now) - 10}, 120, now=now)
        )

    def test_stale_message(self):
        now = time.time()
        self.assertFalse(
            _is_recent_within({"timestamp": int(now) - 300}, 120, now=now)
        )

    def test_missing_timestamp_is_stale(self):
        self.assertFalse(_is_recent_within({}, 120))


if __name__ == "__main__":
    unittest.main()
