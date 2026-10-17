import json

import pytest

from niannian.config import (BOOL_KEYS, DEFAULTS, EDITABLE_KEYS, KEY_LABELS,
                             RESTART_KEYS, load_config, mask_api_key,
                             merge_api_key, save_config, to_bool, validate)

GOOD = {"history_limit": "250", "poll_seconds": "5", "port": "9000",
        "llm_base_url": "https://api.siliconflow.cn/v1",
        "llm_model": "Qwen/Qwen3-8B", "llm_api_key": "sk-good",
        "llm_thinking": "false", "llm_include_images": True}

# validate 严格：GOOD 得把设置页开放的键**全**带上，缺一个就是没提交。
LLM_GOOD = {"llm_base_url": "https://api.siliconflow.cn/v1",
            "llm_model": "Qwen/Qwen3-8B", "llm_api_key": "sk-good",
            "llm_thinking": False, "llm_include_images": True}


def test_validate_normalizes_types():
    assert validate(GOOD) == {"history_limit": 250, "poll_seconds": 5.0,
                              "port": 9000, **LLM_GOOD}


def test_validate_only_returns_editable_keys():
    result = validate({**GOOD, "host": "0.0.0.0", "wechat_db_dir": "D:\\evil"})
    assert set(result) == set(EDITABLE_KEYS)
    assert "host" not in result and "wechat_db_dir" not in result


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


@pytest.mark.parametrize("key,bad,phrase", [
    ("llm_base_url", "api.siliconflow.cn/v1", "http:// 或 https://"),
    ("llm_base_url", "", "http:// 或 https://"),
    ("llm_base_url", "ftp://a/b", "http:// 或 https://"),
    ("llm_base_url", "https://a b/v1", "空格"),
    ("llm_base_url", "https://   ", "http:// 或 https://"),
    ("llm_model", "   ", "模型名不能空"),
    ("llm_model", "x" * 121, "太长"),
    ("llm_api_key", "sk good", "空格"),
    ("llm_api_key", "k" * 301, "太长"),
    ("llm_thinking", "maybe", "思考模式只能是开或关"),
    ("llm_include_images", 7, "图片喂给模型只能是开或关"),
])
def test_validate_rejects_bad_llm_values_with_plain_words(key, bad, phrase):
    with pytest.raises(ValueError) as caught:
        validate({**GOOD, key: bad})
    assert phrase in str(caught.value)


def test_llm_api_key_may_be_empty_at_save_time():
    """没填密钥**能存**（先存地址与模型名是正常的），只是测连时会说还没填。"""
    assert validate({**GOOD, "llm_api_key": ""})["llm_api_key"] == ""


@pytest.mark.parametrize("value,want", [
    (True, True), (False, False), (1, True), (0, False),
    ("true", True), ("ON", True), (" yes ", True),
    ("false", False), ("off", False), ("0", False),
])
def test_to_bool_accepts_the_shapes_a_form_can_send(value, want):
    assert to_bool(value) is want


@pytest.mark.parametrize("bad", ["maybe", 7, None, [], {}])
def test_to_bool_strict_raises_but_default_swallows(bad):
    with pytest.raises(ValueError):
        to_bool(bad)
    assert to_bool(bad, default=True) is True


def test_mask_api_key_hides_the_middle():
    assert mask_api_key("sk-abcdefghijnops") == "sk-…nops"
    assert mask_api_key("") == ""
    assert mask_api_key(None) == ""
    assert mask_api_key("b" * 8) == "•" * 8        # 短到遮不住就整个遮掉
    assert mask_api_key("short") == "•••••"


def test_merge_api_key_blank_means_keep_not_delete():
    """界面读到的永远是掩码，所以"没重填"必须是"沿用库里那把"。"""
    current = {"llm_api_key": "sk-stored"}
    assert merge_api_key({}, current) == "sk-stored"
    assert merge_api_key({"llm_api_key": "   "}, current) == "sk-stored"
    assert merge_api_key({"llm_api_key": "sk-new"}, current) == "sk-new"
    assert merge_api_key({"clear_llm_api_key": True, "llm_api_key": "sk-new"},
                         current) == ""


def test_save_creates_file_and_load_reads_it_back(tmp_path):
    saved = save_config(tmp_path, validate(GOOD))
    assert saved["port"] == 9000
    assert (tmp_path / "config.json").is_file()
    settings = load_config(tmp_path)
    assert (settings["history_limit"], settings["poll_seconds"], settings["port"]) \
        == (250, 5.0, 9000)
    assert settings["llm_model"] == "Qwen/Qwen3-8B"
    assert settings["llm_api_key"] == "sk-good"
    assert settings["llm_thinking"] is False


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
        json.dumps({"history_limit": "abc", "poll_seconds": -5,
                    "llm_base_url": 123, "llm_thinking": "maybe",
                    "llm_include_images": "on", "llm_model": "  DeepSeek/DeepSeek-V3  "}),
        encoding="utf-8")
    settings = load_config(tmp_path)
    assert settings["history_limit"] == DEFAULTS["history_limit"]
    assert settings["poll_seconds"] == 1.0        # 下限夹住，不退回默认
    assert settings["llm_base_url"] == DEFAULTS["llm_base_url"]
    assert settings["llm_thinking"] is False      # 认不出来退回默认（关）
    assert settings["llm_include_images"] is True  # "on" 是认得出的开
    assert settings["llm_model"] == "  DeepSeek/DeepSeek-V3  "   # 读侧不加工，严格在写侧


def test_llm_defaults_ship_pointing_at_the_documented_provider():
    """默认值是"填个 Key 就能试"的形状：地址与模型名给全，密钥与开关留关。"""
    assert DEFAULTS["llm_base_url"] == "https://api.siliconflow.cn/v1"
    assert DEFAULTS["llm_model"]
    assert DEFAULTS["llm_api_key"] == ""
    assert DEFAULTS["llm_thinking"] is False
    assert DEFAULTS["llm_include_images"] is False


def test_only_port_needs_restart():
    assert RESTART_KEYS == ("port",)
    assert set(EDITABLE_KEYS) - set(RESTART_KEYS) == {
        "history_limit", "poll_seconds",
        "llm_base_url", "llm_model", "llm_api_key",
        "llm_thinking", "llm_include_images"}


def test_every_editable_key_has_a_chinese_label():
    """设置页的中文名由后端给一份（界面不再抄第二张表），漏一个键就露出字段名。"""
    assert set(KEY_LABELS) == set(EDITABLE_KEYS)
    assert all(KEY_LABELS[key] for key in EDITABLE_KEYS)
    assert set(BOOL_KEYS) <= set(EDITABLE_KEYS)
