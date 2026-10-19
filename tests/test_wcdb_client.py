"""Tests for the wcdb_api.dll expiry-check patches (Windows only)."""

import sys
import unittest

if sys.platform == "win32":
    try:
        from src.wechat.wcdb_client import (
            _EXPIRY_NEW_VALUE,
            _patch_expiry_buffer,
            _patch_init_time_check_buffer,
        )
    except Exception:  # pragma: no cover - defensive on non-Windows CI
        _patch_expiry_buffer = None
        _patch_init_time_check_buffer = None
else:
    _patch_expiry_buffer = None
    _patch_init_time_check_buffer = None


def _make_buffer(
    imm: int,
    suffix: bytes = b"\x7e\x0a\xb8\x9b\xff\xff\xff",
) -> bytearray:
    return bytearray(
        b"\x90" * 8
        + b"\x48\x3d"
        + imm.to_bytes(4, "little")
        + suffix
        + b"\xcc" * 8
    )


@unittest.skipUnless(
    _patch_expiry_buffer is not None, "requires Windows wcdb_api module"
)
class InitProtectionExpiryTests(unittest.TestCase):
    def test_patches_expired_constant(self):
        buf = _make_buffer(0x6ABDA27F)
        status, offset, new_bytes = _patch_expiry_buffer(buf)
        self.assertEqual(status, "patched")
        self.assertEqual(offset, 10)  # cmp at 8 + imm32 offset 2
        self.assertEqual(bytes(buf[offset:offset + 4]), new_bytes)
        self.assertEqual(
            bytes(buf[offset:offset + 4]),
            _EXPIRY_NEW_VALUE.to_bytes(4, "little"),
        )

    def test_already_patched_is_idempotent(self):
        buf = _make_buffer(_EXPIRY_NEW_VALUE)
        status, offset, _ = _patch_expiry_buffer(buf)
        self.assertEqual(status, "already")
        self.assertEqual(offset, 10)

    def test_ignores_unrelated_cmp_rax(self):
        buf = _make_buffer(0x6ABDA27F, suffix=b"\x7e\x0a\x90\x90\x90\x90\x90")
        status, offset, _ = _patch_expiry_buffer(buf)
        self.assertEqual(status, "not_found")
        self.assertEqual(offset, -1)
        self.assertEqual(bytes(buf[10:14]), bytes.fromhex("7fa2bd6a"))

    def test_not_found_on_empty_buffer(self):
        status, offset, _ = _patch_expiry_buffer(bytearray(b"\x90" * 32))
        self.assertEqual(status, "not_found")
        self.assertEqual(offset, -1)


@unittest.skipUnless(
    _patch_init_time_check_buffer is not None,
    "requires Windows wcdb_api module",
)
class WcdbInitExpiryTests(unittest.TestCase):
    def test_patches_second_time_check(self):
        buf = bytearray(
            b"\x90" * 6 + b"\x48\x3b\xc3\x0f\x8e\x30\x01\x00\x00" + b"\xcc"
        )
        status, offset, new_bytes = _patch_init_time_check_buffer(buf)
        self.assertEqual(status, "patched")
        self.assertEqual(offset, 6)
        self.assertEqual(new_bytes, b"\x48\x39\xc0")
        self.assertEqual(bytes(buf[6:9]), b"\x48\x39\xc0")

    def test_already_patched(self):
        buf = bytearray(b"\x90" * 6 + b"\x48\x39\xc0\x0f\x8e\x30\x01\x00\x00")
        status, offset, _ = _patch_init_time_check_buffer(buf)
        self.assertEqual(status, "already")
        self.assertEqual(offset, 6)

    def test_not_found(self):
        buf = bytearray(b"\x90" * 32)
        status, offset, _ = _patch_init_time_check_buffer(buf)
        self.assertEqual(status, "not_found")
        self.assertEqual(offset, -1)


if __name__ == "__main__":
    unittest.main()
