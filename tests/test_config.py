"""配置与画像的失败持久化验证。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niannian.config import ConfigStore, ProfileStore


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_and_validation(self):
        store = ConfigStore(self.root)
        store.update({"batch_interval_seconds": 60, "ai_api_key": "configuration-test-token", "ai_base_url": "api.siliconflow.cn/v1/"})
        fresh = ConfigStore(self.root)
        self.assertEqual(fresh.read()["batch_interval_seconds"], 60)
        self.assertTrue(fresh.public()["ai_key_configured"])
        self.assertNotIn("ai_api_key", fresh.public())
        self.assertFalse(fresh.public()["thinking_enabled"])
        for fields in ({"batch_interval_seconds": 0}, {"wechat_enabled": "yes"}, {"ai_base_url": "file:///tmp"}, {"unknown": True}):
            with self.assertRaises(ValueError):
                store.update(fields)
        self.assertEqual(store.read(), fresh.read())

    def test_tunable_budgets_validate_and_report_defaults(self):
        store = ConfigStore(self.root)
        defaults = store.public()["defaults"]
        for key, value in (("batch_limit", 4), ("batch_char_budget", 4200), ("new_message_chars", 2600),
                           ("history_chars", 1200), ("history_rows", 6), ("task_chars", 1600),
                           ("profile_chars", 1200), ("round_limit", 240), ("per_session_limit", 80),
                           ("ai_max_tokens_single", 700), ("ai_max_tokens_batch", 1200),
                           ("ignored_keep_per_session", 100)):
            self.assertEqual(defaults[key], value, key)
        self.assertEqual(defaults["ai_temperature"], 0)
        self.assertNotIn("ai_api_key", defaults)
        self.assertNotIn("db_path", defaults)
        store.update({"batch_limit": 8, "batch_char_budget": 9000, "ai_temperature": 0.4,
                      "history_rows": 0, "ignored_keep_per_session": 20})
        fresh = ConfigStore(self.root)
        self.assertEqual(fresh.read()["batch_limit"], 8)
        self.assertEqual(fresh.read()["ai_temperature"], 0.4)
        self.assertEqual(fresh.read()["history_rows"], 0)
        for fields in ({"batch_limit": 0}, {"batch_limit": 21}, {"batch_char_budget": 399},
                       {"ai_temperature": 2.5}, {"ai_temperature": "0.4"}, {"new_message_chars": 199},
                       {"round_limit": 2001}, {"per_session_limit": 0}, {"history_rows": 21},
                       {"ignored_keep_per_session": 9}, {"ai_max_tokens_single": 63}):
            with self.assertRaises(ValueError):
                store.update(fields)

    def test_broken_file_preserved_until_explicit_save(self):
        path = self.root / "config.json"
        path.write_text("{invalid", encoding="utf-8")
        store = ConfigStore(self.root)
        self.assertIsNotNone(store.warning)
        self.assertEqual(path.read_text(), "{invalid")

    def test_atomic_failure_keeps_prior_config_and_profile(self):
        store = ConfigStore(self.root)
        original = store.read()
        profile = ProfileStore(self.root / "PROFILE.md")
        profile.save("# 用户画像\n")
        with patch("niannian.config.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                store.update({"batch_interval_seconds": 90})
            with self.assertRaises(OSError):
                profile.save("# 更新画像\n")
        self.assertEqual(store.read(), original)
        self.assertEqual(ConfigStore(self.root).read(), original)
        self.assertEqual(profile.read(), "# 用户画像\n")
        self.assertFalse(list(self.root.glob("*.tmp")))
