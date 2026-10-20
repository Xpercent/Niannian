import json

import pytest

from niannian.config import (BOOL_KEYS, DEFAULTS, EDITABLE_KEYS, KEY_LABELS,
                             RESTART_KEYS, load_config, mask_api_key,
                             merge_api_key, save_config, to_bool, validate)
from niannian.extract import (DEFAULT_BATCH_LIMIT, DEFAULT_HISTORY_LIMIT,
                              DEFAULT_ROUND_LIMIT, DEFAULT_SYSTEM_PROMPT)

GOOD = {"history_limit": "250", "poll_seconds": "5", "port": "9000",
        "llm_base_url": "https://api.siliconflow.cn/v1",
        "llm_model": "Qwen/Qwen3-8B", "llm_api_key": "sk-good",
        "llm_thinking": "false", "llm_include_images": True,
        "llm_temperature": "0.3",
        "extract_round_limit": "80", "extract_batch_limit": "10",
        "extract_history_limit": "5", "extract_prompt": "只要学习相关的"}

# validate 严格：GOOD 得把设置页开放的键**全**带上，缺一个就是没提交。
LLM_GOOD = {"llm_base_url": "https://api.siliconflow.cn/v1",
            "llm_model": "Qwen/Qwen3-8B", "llm_api_key": "sk-good",
            "llm_thinking": False, "llm_include_images": True,
            "llm_temperature": 0.3}


def test_validate_normalizes_types():
    assert validate(GOOD) == {"history_limit": 250, "poll_seconds": 5.0,
                              "port": 9000, **LLM_GOOD,
                              "extract_round_limit": 80, "extract_batch_limit": 10,
                              "extract_history_limit": 5,
                              "extract_prompt": "只要学习相关的"}


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
    ("llm_temperature", "热", "温度要填数字"),
    ("llm_temperature", 2.5, "温度要在 0 到 2 之间"),
    ("llm_temperature", -0.1, "温度要在 0 到 2 之间"),
])
def test_validate_rejects_bad_llm_values_with_plain_words(key, bad, phrase):
    with pytest.raises(ValueError) as caught:
        validate({**GOOD, key: bad})
    assert phrase in str(caught.value)


def test_blank_temperature_means_dont_send_the_field_but_zero_does_not():
    """这格空着的意思是"请求里不带 temperature"，而 0 是"我要 0 度"——两回事。"""
    assert validate({**GOOD, "llm_temperature": ""})["llm_temperature"] is None
    assert validate({**GOOD, "llm_temperature": "   "})["llm_temperature"] is None
    assert validate({**GOOD, "llm_temperature": None})["llm_temperature"] is None
    assert validate({**GOOD, "llm_temperature": 0})["llm_temperature"] == 0.0
    assert validate({**GOOD, "llm_temperature": 2})["llm_temperature"] == 2.0


def test_llm_api_key_may_be_empty_at_save_time():
    """没填密钥**能存**（先存地址与模型名是正常的），只是测连时会说还没填。"""
    assert validate({**GOOD, "llm_api_key": ""})["llm_api_key"] == ""


@pytest.mark.parametrize("key,bad,phrase", [
    ("extract_round_limit", 0, "1 到 1000"),
    ("extract_round_limit", 1001, "1 到 1000"),
    ("extract_batch_limit", 51, "1 到 50"),
    ("extract_batch_limit", "两批", "整数"),
    ("extract_history_limit", 101, "0 到 100"),
    ("extract_prompt", "x" * 8001, "太长"),
])
def test_validate_rejects_bad_extract_values_with_plain_words(key, bad, phrase):
    with pytest.raises(ValueError) as caught:
        validate({**GOOD, key: bad})
    assert phrase in str(caught.value)


def test_blank_extract_numbers_fall_back_to_the_built_in_defaults():
    """数字那三格**留空**的意思是"我不覆盖"，不是"我要 0 条"。"""
    got = validate({**GOOD, "extract_round_limit": "", "extract_batch_limit": None,
                    "extract_history_limit": "   "})
    assert (got["extract_round_limit"], got["extract_batch_limit"],
            got["extract_history_limit"]) == (DEFAULT_ROUND_LIMIT, DEFAULT_BATCH_LIMIT,
                                              DEFAULT_HISTORY_LIMIT)


def test_zero_history_limit_survives_because_zero_is_a_real_choice():
    """上文 0 条是合法要求（只想看这一批），不能和"没填"混成一回事。"""
    assert validate({**GOOD, "extract_history_limit": 0})["extract_history_limit"] == 0


def test_prompt_identical_to_the_built_in_is_stored_blank():
    """一字不差时落**空串**：配置文件里不留一份副本，否则日后内置改了会被旧的挡住。"""
    assert validate({**GOOD, "extract_prompt": DEFAULT_SYSTEM_PROMPT})["extract_prompt"] == ""


def test_load_config_clamps_extract_numbers_instead_of_refusing_to_start(tmp_path):
    """读文件那一头宽容：手工写在 config.json 里的越界值夹回边界，服务照样起得来。"""
    (tmp_path / "config.json").write_text(
        json.dumps({"extract_round_limit": 99999, "extract_batch_limit": "abc",
                    "extract_history_limit": -3, "extract_prompt": 42}),
        encoding="utf-8")
    settings = load_config(tmp_path)
    assert settings["extract_round_limit"] == 1000                    # 夹到上界
    assert settings["extract_batch_limit"] == DEFAULT_BATCH_LIMIT     # 认不出来退默认
    assert settings["extract_history_limit"] == 0                     # 夹到下界
    assert settings["extract_prompt"] == ""                           # 非字符串当没填


def test_load_config_treats_an_unreadable_temperature_as_not_filled(tmp_path):
    """温度读不出来的宽容回落是"不发这个字段"，越界照样夹边界——服务不能起不来。"""
    (tmp_path / "config.json").write_text(json.dumps({"llm_temperature": "abc"}),
                                          encoding="utf-8")
    assert load_config(tmp_path)["llm_temperature"] is None

    (tmp_path / "config.json").write_text(json.dumps({"llm_temperature": 9}),
                                          encoding="utf-8")
    assert load_config(tmp_path)["llm_temperature"] == 2.0


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
    assert settings["llm_temperature"] == 0.3


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
    assert DEFAULTS["llm_temperature"] is None    # 默认不发 temperature


def test_only_port_needs_restart():
    assert RESTART_KEYS == ("port",)
    assert set(EDITABLE_KEYS) - set(RESTART_KEYS) == {
        "history_limit", "poll_seconds",
        "llm_base_url", "llm_model", "llm_api_key",
        "llm_thinking", "llm_include_images", "llm_temperature",
        "extract_round_limit", "extract_batch_limit",
        "extract_history_limit", "extract_prompt"}


def test_every_editable_key_has_a_chinese_label():
    """设置页的中文名由后端给一份（界面不再抄第二张表），漏一个键就露出字段名。"""
    assert set(KEY_LABELS) == set(EDITABLE_KEYS)
    assert all(KEY_LABELS[key] for key in EDITABLE_KEYS)
    assert set(BOOL_KEYS) <= set(EDITABLE_KEYS)
