import json

import pytest

from niannian.config import (DEFAULTS, EDITABLE_KEYS, RESTART_KEYS, load_config,
                             save_config, validate)

GOOD = {"history_limit": "250", "poll_seconds": "5", "port": "9000"}


def test_validate_normalizes_types():
    assert validate(GOOD) == {"history_limit": 250, "poll_seconds": 5.0, "port": 9000}


def test_validate_only_returns_editable_keys():
    result = validate({**GOOD, "host": "0.0.0.0", "wechat_db_dir": "D:\\evil"})
    assert set(result) == set(EDITABLE_KEYS)


@pytest.mark.parametrize("key,bad,phrase", [
    ("history_limit", 0, "1 到 1000"),
    ("history_limit", 1001, "1 到 1000"),
    ("history_limit", "abc", "整数"),
    ("poll_seconds", 0.5, "1 到 3600"),
    ("poll_seconds", "x", "秒数"),
    ("port", 80, "1024"),
    ("port", 70000, "1024 到 65535"),
    ("port", None, "整数"),
])
def test_validate_rejects_out_of_range_with_plain_words(key, bad, phrase):
    with pytest.raises(ValueError) as caught:
        validate({**GOOD, key: bad})
    assert phrase in str(caught.value)


def test_port_below_1024_rejected_but_1024_allowed():
    with pytest.raises(ValueError):
        validate({**GOOD, "port": 22})
    assert validate({**GOOD, "port": 1024})["port"] == 1024


def test_save_creates_file_and_load_reads_it_back(tmp_path):
    saved = save_config(tmp_path, validate(GOOD))
    assert saved["port"] == 9000
    assert (tmp_path / "config.json").is_file()
    settings = load_config(tmp_path)
    assert (settings["history_limit"], settings["poll_seconds"], settings["port"]) \
        == (250, 5.0, 9000)


def test_save_preserves_keys_the_page_does_not_expose(tmp_path):
    """设置页只碰三个键，手工写在文件里的其它键不能被抹掉。"""
    (tmp_path / "config.json").write_text(
        json.dumps({"host": "127.0.0.1", "wechat_db_dir": "D:\\Weixin",
                    "history_limit": 10}),
        encoding="utf-8")
    save_config(tmp_path, validate(GOOD))
    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert written["wechat_db_dir"] == "D:\\Weixin"
    assert written["host"] == "127.0.0.1"
    assert written["history_limit"] == 250


def test_save_leaves_no_temp_file(tmp_path):
    save_config(tmp_path, validate(GOOD))
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_save_overwrites_a_corrupt_file(tmp_path):
    (tmp_path / "config.json").write_text("{ not json", encoding="utf-8")
    written = save_config(tmp_path, validate(GOOD))
    assert written["port"] == 9000
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["port"] == 9000


def test_load_config_stays_tolerant_on_bad_values(tmp_path):
    """读文件要宽容（服务得起得来），与写文件严格是两回事。"""
    (tmp_path / "config.json").write_text(
        json.dumps({"history_limit": "abc", "poll_seconds": -5}), encoding="utf-8")
    settings = load_config(tmp_path)
    assert settings["history_limit"] == DEFAULTS["history_limit"]
    assert settings["poll_seconds"] == 1.0        # 下限夹住，不退回默认


def test_only_port_needs_restart():
    assert RESTART_KEYS == ("port",)
    assert set(EDITABLE_KEYS) - set(RESTART_KEYS) == {"history_limit", "poll_seconds"}
