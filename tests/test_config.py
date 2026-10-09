"""配置与画像的失败持久化验证。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shiguang.config import ConfigStore, ProfileStore


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
        with patch("shiguang.config.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                store.update({"batch_interval_seconds": 90})
            with self.assertRaises(OSError):
                profile.save("# 更新画像\n")
        self.assertEqual(store.read(), original)
        self.assertEqual(ConfigStore(self.root).read(), original)
        self.assertEqual(profile.read(), "# 用户画像\n")
        self.assertFalse(list(self.root.glob("*.tmp")))
