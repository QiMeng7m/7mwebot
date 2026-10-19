"""Tests for onboarding settings persisted to .env."""

import tempfile
import unittest
from pathlib import Path

import src.web.server as server


class OnboardingEnvTests(unittest.TestCase):
    def setUp(self):
        self._backup = dict(server._onboarding_data)

    def tearDown(self):
        with server._onboarding_lock:
            server._onboarding_data.clear()
            server._onboarding_data.update(self._backup)

    def test_feature_toggles_are_written(self):
        with server._onboarding_lock:
            server._onboarding_data.update({
                "fun_enabled": False,
                "proactive_enabled": True,
                "sticky_mention_enabled": False,
                "key": "a" * 64,
            })

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            server._write_onboarding_to_env(env_path)
            text = env_path.read_text(encoding="utf-8")

        self.assertIn("FUN_ENABLED=false", text)
        self.assertIn("PROACTIVE_ENABLED=true", text)
        self.assertIn("STICKY_MENTION_ENABLED=false", text)
        self.assertIn("ONBOARDING_DONE=true", text)


if __name__ == "__main__":
    unittest.main()
