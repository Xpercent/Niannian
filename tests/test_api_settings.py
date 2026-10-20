"""设置接口这一轮多了模型接入五项：密钥不出网关、留空即不改、测试连接用草稿值。"""

import json

import pytest
from fastapi import HTTPException

from niannian import extract
from niannian.api import create_app
from niannian.config import EDITABLE_KEYS

from test_api import NoMediaCollector

BASE = {"history_limit": 100, "poll_seconds": 3.0, "port": 8765,
        "llm_base_url": "https://api.siliconflow.cn/v1",
        "llm_model": "Qwen/Qwen3-8B", "llm_api_key": "sk-saved-key-0001",
        "llm_thinking": False, "llm_include_images": False}


def endpoint(app, path, method):
    """按路径与取数方式找端点函数，直接调用（不走 HTTP：lifespan 会起采集线程）。"""
    for route in app.routes:
        if getattr(route, "path", "") == path and method in (route.methods or set()):
            return route.endpoint
    raise AssertionError(f"没有注册 {method} {path}")


def make_app(tmp_path, monkeypatch, saved=None):
    (tmp_path / "config.json").write_text(
        json.dumps(saved or BASE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("niannian.api.Collector", NoMediaCollector)
    return create_app(tmp_path)


def read_config(tmp_path) -> dict:
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


def test_get_settings_never_returns_the_api_key(tmp_path, monkeypatch):
    """真密钥只留在 config.json：网页读到的 values 里那个键必须是空串。"""
    app = make_app(tmp_path, monkeypatch)
    got = json.dumps(endpoint(app, "/api/settings", "GET")(), ensure_ascii=False)
    assert "sk-saved-key-0001" not in got
    assert '"llm_api_key": ""' in got


def test_get_settings_tells_the_page_a_key_is_already_stored(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    got = endpoint(app, "/api/settings", "GET")()
    assert got["api_key_set"] is True
    assert got["api_key_masked"] == "sk-…0001"
    assert got["values"]["llm_model"] == "Qwen/Qwen3-8B"
    assert got["values"]["llm_thinking"] is False
    # 中文名由后端给一份，界面不再抄第二张表（保存后的提示就用它）
    assert got["labels"]["llm_thinking"] == "思考模式"
    assert set(got["labels"]) == set(EDITABLE_KEYS)

    blank = make_app(tmp_path, monkeypatch, {**BASE, "llm_api_key": ""})
    none = endpoint(blank, "/api/settings", "GET")()
    assert none["api_key_set"] is False and none["api_key_masked"] == ""


def test_put_settings_blank_key_keeps_the_stored_one(tmp_path, monkeypatch):
    """留空 = 不改（界面上那是掩码，用户没重填就是没动），填了才换。"""
    app = make_app(tmp_path, monkeypatch)
    put = endpoint(app, "/api/settings", "PUT")
    got = put({**BASE, "llm_api_key": "", "llm_model": "deepseek-ai/DeepSeek-V3"})
    assert got["values"]["llm_api_key"] == ""          # 回显也不带真值
    assert read_config(tmp_path)["llm_api_key"] == "sk-saved-key-0001"
    assert read_config(tmp_path)["llm_model"] == "deepseek-ai/DeepSeek-V3"
    assert got["applied"] == ["llm_model"]


def test_put_settings_can_replace_and_clear_the_key(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    put = endpoint(app, "/api/settings", "PUT")
    put({**BASE, "llm_api_key": "sk-brand-new"})
    assert read_config(tmp_path)["llm_api_key"] == "sk-brand-new"
    put({**BASE, "llm_api_key": "sk-brand-new", "clear_llm_api_key": True})
    assert read_config(tmp_path)["llm_api_key"] == ""


def test_put_settings_rejects_bad_llm_values_in_plain_words(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    put = endpoint(app, "/api/settings", "PUT")
    with pytest.raises(HTTPException) as caught:
        put({**BASE, "llm_base_url": "api.siliconflow.cn/v1"})
    assert "http:// 或 https://" in caught.value.detail
    with pytest.raises(HTTPException) as caught:
        put({**BASE, "llm_thinking": "sometimes"})
    assert "思考模式只能是开或关" in caught.value.detail
    assert read_config(tmp_path)["llm_base_url"] == BASE["llm_base_url"]  # 没落盘


def test_put_settings_stores_a_temperature_and_blank_stops_sending_it(tmp_path, monkeypatch):
    """填了才发、清空就不发：库里那格跟着变成数字或 null，界面读回来才对得上。"""
    app = make_app(tmp_path, monkeypatch)
    put = endpoint(app, "/api/settings", "PUT")

    got = put({**BASE, "llm_temperature": 0.4})
    assert read_config(tmp_path)["llm_temperature"] == 0.4
    assert got["values"]["llm_temperature"] == 0.4

    back = put({**BASE, "llm_temperature": None})
    assert read_config(tmp_path)["llm_temperature"] is None
    assert back["values"]["llm_temperature"] is None

    with pytest.raises(HTTPException) as caught:
        put({**BASE, "llm_temperature": 2.5})
    assert "温度要在 0 到 2 之间" in caught.value.detail


def test_get_settings_reports_no_temperature_as_null(tmp_path, monkeypatch):
    """库里没填过 → values 里那格是 null，界面才知道该空着而不是显示 0。"""
    got = endpoint(make_app(tmp_path, monkeypatch), "/api/settings", "GET")()
    assert got["values"]["llm_temperature"] is None
    assert got["labels"]["llm_temperature"] == "温度"


def test_get_settings_shows_the_prompt_that_actually_runs(tmp_path, monkeypatch):
    """配置里存空串的意思是"用内置"，界面得看到**真正生效的那段**才谈得上改它。"""
    app = make_app(tmp_path, monkeypatch)
    got = endpoint(app, "/api/settings", "GET")()

    assert got["values"]["extract_prompt"] == extract.DEFAULT_SYSTEM_PROMPT
    assert got["extract_prompt_default"] == extract.DEFAULT_SYSTEM_PROMPT.strip()


def test_put_settings_stores_a_custom_prompt_and_the_numbers(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    put = endpoint(app, "/api/settings", "PUT")

    got = put({**BASE, "extract_prompt": "只要教学相关的",
               "extract_batch_limit": 8, "extract_history_limit": 0})

    assert got["values"]["extract_prompt"] == "只要教学相关的"
    saved = read_config(tmp_path)
    assert (saved["extract_batch_limit"], saved["extract_history_limit"]) == (8, 0)
    assert set(got["applied"]) == {"extract_prompt", "extract_batch_limit",
                                   "extract_history_limit"}


def test_put_settings_stores_the_profile_and_shows_it_back(tmp_path, monkeypatch):
    """画像这格回的就是库里那份原话：它没有"内置默认"，空着就是真的没填。"""
    app = make_app(tmp_path, monkeypatch)

    got = endpoint(app, "/api/settings", "PUT")(
        {**BASE, "extract_profile": "我叫 YX，心理课第 5 组，宿舍 4 号楼 301"})

    assert got["values"]["extract_profile"] == "我叫 YX，心理课第 5 组，宿舍 4 号楼 301"
    assert got["applied"] == ["extract_profile"]
    assert read_config(tmp_path)["extract_profile"] == "我叫 YX，心理课第 5 组，宿舍 4 号楼 301"


def test_saving_the_default_prompt_untouched_writes_no_copy(tmp_path, monkeypatch):
    """原样把内置那段提交回来 → 落**空串**：库里不存副本，日后内置改了才挡不住。"""
    app = make_app(tmp_path, monkeypatch)

    got = endpoint(app, "/api/settings", "PUT")(
        {**BASE, "extract_prompt": extract.DEFAULT_SYSTEM_PROMPT})

    assert read_config(tmp_path)["extract_prompt"] == ""
    assert got["unchanged"] is True


def test_put_settings_rejects_an_out_of_range_batch_with_plain_words(tmp_path, monkeypatch):
    """一批 51 条是"模型看不过来"，不是笔误能放过的：越界就不落盘。"""
    app = make_app(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as caught:
        endpoint(app, "/api/settings", "PUT")({**BASE, "extract_batch_limit": 51})

    assert "一批请求条数要在 1 到 50 之间" in caught.value.detail
    assert "extract_batch_limit" not in read_config(tmp_path)


def test_test_connection_uses_the_draft_and_the_stored_key(tmp_path, monkeypatch):
    """测连不要求先保存：草稿值覆盖在库里那份之上，密钥留空沿用已存的。"""
    seen = {}

    def fake_test_connection(settings):
        seen.update(settings)
        return {"ok": True, "elapsed_ms": 12, "model": settings["llm_model"],
                "reply": "好的", "thinking": settings["llm_thinking"], "note": ""}

    monkeypatch.setattr("niannian.api.test_connection", fake_test_connection)
    app = make_app(tmp_path, monkeypatch)
    got = endpoint(app, "/api/settings/test-connection", "POST")(
        {"llm_model": "deepseek-ai/DeepSeek-V3", "llm_thinking": True,
         "llm_api_key": ""})
    assert got["ok"] is True and got["model"] == "deepseek-ai/DeepSeek-V3"
    assert seen["llm_model"] == "deepseek-ai/DeepSeek-V3"
    assert seen["llm_thinking"] is True
    assert seen["llm_api_key"] == "sk-saved-key-0001"     # 没重填 → 用库里那把
    # 测连**不该**把草稿写进配置：不保存就再点一次也得照样能测
    assert read_config(tmp_path)["llm_model"] == BASE["llm_model"]


def test_test_connection_a_cleared_temperature_is_not_taken_from_the_library(
        tmp_path, monkeypatch):
    """界面上那格被清空 → 草稿里是 null → 这一试也不发，不能悄悄沿用库里那个旧的。"""
    seen = {}
    monkeypatch.setattr("niannian.api.test_connection",
                        lambda settings: seen.update(settings) or {"ok": True})
    app = make_app(tmp_path, monkeypatch, {**BASE, "llm_temperature": 0.8})
    endpoint(app, "/api/settings/test-connection", "POST")({"llm_temperature": None})
    assert seen["llm_temperature"] is None


def test_test_connection_does_not_go_out_for_an_unparsable_draft(tmp_path, monkeypatch):
    """地址明显写错时不出网，直接把校验那句话回给界面（也不该 500）。"""
    called = []
    monkeypatch.setattr("niannian.api.test_connection",
                        lambda settings: called.append(settings) or {"ok": True})
    app = make_app(tmp_path, monkeypatch)
    got = endpoint(app, "/api/settings/test-connection", "POST")({"llm_base_url": "not a url"})
    assert got["ok"] is False and called == []
    assert "http:// 或 https://" in got["error"]


def test_test_connection_without_any_key_says_so(tmp_path, monkeypatch):
    """这条走真 test_connection：缺密钥要在出网前就说，而不是等服务商回 401。"""
    app = make_app(tmp_path, monkeypatch, {**BASE, "llm_api_key": ""})
    got = endpoint(app, "/api/settings/test-connection", "POST")({})
    assert got["ok"] is False
    assert got["error"] == "还没有填 API Key"
