"""模型接入层：全部打在替身服务商上，测试不出网。

真服务商那一条链路由设置页的「测试连接」按钮人工验证（见 HANDOFF）。
"""

import types

import httpx
import pytest
from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                    AuthenticationError, BadRequestError, NotFoundError,
                    RateLimitError)

from niannian import llm

CFG = {"llm_base_url": "https://api.siliconflow.cn/v1",
       "llm_model": "Qwen/Qwen3-8B",
       "llm_api_key": "sk-secret-value",
       "llm_thinking": False,
       "llm_include_images": False}


def completion(text="好的", model="Qwen/Qwen3-8B", reasoning=None):
    message = types.SimpleNamespace(content=text, reasoning_content=reasoning)
    return types.SimpleNamespace(model=model,
                                 choices=[types.SimpleNamespace(message=message)])


class FakeProvider:
    """替身：记下 ``OpenAI()`` 的构造参数与 ``create()`` 的调用参数，按剧本回话或抛错。"""

    def __init__(self, response=None, error=None):
        self.constructed = []
        self.calls = []
        self.response = response if response is not None else completion()
        self.error = error

    def __call__(self, **kwargs):
        self.constructed.append(kwargs)
        return types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(
                create=self._create)))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(llm, "OpenAI", fake)
    return fake


def install(monkeypatch, **kwargs) -> FakeProvider:
    """把替身装进 ``llm.OpenAI``，用于要指定回包内容的用例。"""
    fake = FakeProvider(**kwargs)
    monkeypatch.setattr(llm, "OpenAI", fake)
    return fake


def test_thinking_off_is_sent_explicitly(provider):
    """Qwen3 在服务商那边**默认就是开的**：省略参数等于替用户开着。

    实测（2026-10-17）不带参数回了 reasoning_content 并花 334 token，显式
    ``enable_thinking=false`` 才降到 19 token，所以"关"必须出现在请求体里。
    """
    llm.test_connection(CFG)
    assert provider.calls[0]["extra_body"] == {"enable_thinking": False}


def test_thinking_on_reaches_the_request_body(provider):
    llm.test_connection({**CFG, "llm_thinking": True})
    assert provider.calls[0]["extra_body"] == {"enable_thinking": True}


GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta"


@pytest.mark.parametrize("thinking,shape", [
    (False, {"reasoning_effort": "none"}),
    (True, {"reasoning_effort": "high"}),
])
def test_google_gets_the_openai_standard_field(provider, thinking, shape):
    """Google 的兼容层严格解析请求体：``enable_thinking`` 会让**整条**请求 400（实测）。

    它只认 OpenAI 规范里的字段，思考这一档只能用 ``reasoning_effort`` 表达。
    """
    llm.test_connection({**CFG, "llm_base_url": GOOGLE_BASE, "llm_thinking": thinking})
    assert provider.calls[0]["extra_body"] == shape


def test_host_recognition_tolerates_a_missing_scheme(provider):
    """地址可以不带协议头（配置就是这么读进来的），认服务商不能因此认错形状。"""
    llm.test_connection({**CFG, "llm_base_url": "generativelanguage.googleapis.com/v1beta"})
    assert provider.calls[0]["extra_body"] == {"reasoning_effort": "none"}


def test_a_googleapis_lookalike_host_is_not_treated_as_google(provider):
    llm.test_connection({**CFG, "llm_base_url": "https://notgoogleapis.com/v1"})
    assert provider.calls[0]["extra_body"] == {"enable_thinking": False}


def test_temperature_stays_out_of_the_request_body_when_not_filled(provider):
    """没填温度 = **不发这个字段**：各家默认值不一样，我们不替服务商决定。

    与 ``max_tokens`` 那一条同一个道理，而且少数推理模型压根不接受 temperature，
    替用户塞一个默认值就是给它们一条 400。
    """
    llm.test_connection(CFG)
    assert "temperature" not in provider.calls[0]
    llm.test_connection({**CFG, "llm_temperature": None})
    assert "temperature" not in provider.calls[1]


def test_a_filled_temperature_reaches_the_request_body(provider):
    llm.test_connection({**CFG, "llm_temperature": 0.2})
    assert provider.calls[0]["temperature"] == 0.2
    # 0 度是合法要求（只想照字面办），不能和"没填"混成一回事
    llm.test_connection({**CFG, "llm_temperature": 0})
    assert provider.calls[1]["temperature"] == 0


def test_thinking_on_widens_the_wait_and_never_sends_a_ceiling(provider):
    """开思考的单次能跑到 20 秒以上（真机踩过），所以只放宽等待。

    回复长度不设上限（用户 2026-10-20 定）：请求体里出现 ``max_tokens`` 就是回归。
    """
    llm.test_connection({**CFG, "llm_thinking": True})
    assert "max_tokens" not in provider.calls[0]
    assert provider.constructed[0]["timeout"] == llm.TEST_TIMEOUT_THINKING_SECONDS

    llm.test_connection(CFG)
    assert "max_tokens" not in provider.calls[1]
    assert provider.constructed[1]["timeout"] == llm.TEST_TIMEOUT_SECONDS


def test_empty_reply_is_explained_instead_of_looking_broken(monkeypatch):
    """通了却没回话要在 note 里说清楚，不能让用户以为连接坏了。"""
    install(monkeypatch, response=completion(text="", reasoning="用户在要我回两个字"))
    got = llm.test_connection({**CFG, "llm_thinking": True})
    assert got["ok"] is True
    assert got["reply"] == ""
    assert "链路本身是通的" in got["note"]


def test_client_is_built_from_config_with_a_bounded_timeout(provider):
    llm.test_connection(CFG)
    built = provider.constructed[0]
    assert built["api_key"] == "sk-secret-value"
    assert built["base_url"] == "https://api.siliconflow.cn/v1"
    # 人在界面上等：超时给死，且不许 SDK 自己重试（那会把等待时间乘三）
    assert built["timeout"] == llm.TEST_TIMEOUT_SECONDS
    assert built["max_retries"] == 0


def test_test_connection_reports_reply_and_latency(provider):
    got = llm.test_connection(CFG)
    assert got["ok"] is True
    assert got["reply"] == "好的"
    assert got["model"] == "Qwen/Qwen3-8B"
    assert got["thinking"] is False
    assert got["note"] == ""
    assert isinstance(got["elapsed_ms"], int) and got["elapsed_ms"] >= 0
    # 一条最短请求而已：不带 max_tokens，也不该带别的服务商字段
    assert "max_tokens" not in provider.calls[0]
    assert provider.calls[0]["messages"] == [
        {"role": "user", "content": llm.TEST_PROMPT}]


def test_thinking_switch_and_provider_disagreement_is_said_out_loud(provider):
    """开了开关但服务商没回思考内容 → 要在结果里说出来，别让用户以为生效了。"""
    provider.response = completion(reasoning=None)
    assert "没回思考内容" in llm.test_connection({**CFG, "llm_thinking": True})["note"]

    provider.response = completion(reasoning="先想想用户要什么")
    assert "可能忽略了这个参数" in llm.test_connection(CFG)["note"]


@pytest.mark.parametrize("missing,phrase", [
    ("llm_api_key", "还没有填 API Key"),
    ("llm_base_url", "还没有填服务商地址"),
    ("llm_model", "还没有填模型名"),
])
def test_incomplete_config_never_leaves_the_process(monkeypatch, missing, phrase):
    """缺配置是我这边的问题，不该拿去问服务商：连客户端都不许构造。"""
    fake = install(monkeypatch)
    got = llm.test_connection({**CFG, missing: "   "})
    assert got["ok"] is False and got["error"] == phrase
    assert fake.constructed == []            # 一次都没出网


def test_empty_choices_from_provider_is_reported(monkeypatch):
    fake = install(monkeypatch, response=types.SimpleNamespace(model="m", choices=[]))
    got = llm.test_connection(CFG)
    assert got["ok"] is False
    assert got["error"] == "服务商回了空响应"
    assert len(fake.constructed) == 1        # 出网了，但回来的东西没法用


def request():
    return httpx.Request("POST", "https://api.siliconflow.cn/v1/chat/completions")


def status(cls, code, message="provider says no"):
    return cls(message, response=httpx.Response(code, request=request()), body=None)


@pytest.mark.parametrize("error,phrase", [
    (status(AuthenticationError, 401), "API Key 被服务商拒了（401）"),
    (status(NotFoundError, 404), "找不到这个模型或地址（404）"),
    (status(RateLimitError, 429), "请求太频繁（429）"),
    (status(BadRequestError, 400), "不接受这个请求（400）"),
    (status(APIStatusError, 503), "服务商返回 503"),
    (APIConnectionError(request=request()), "连不上服务商"),
    (APITimeoutError(request=request()), "服务商还没回话"),
])
def test_provider_errors_become_plain_chinese(monkeypatch, error, phrase):
    install(monkeypatch, error=error)
    got = llm.test_connection(CFG)
    assert got["ok"] is False
    assert phrase in got["error"]


def test_bad_request_keeps_the_provider_sentence(provider):
    """400 通常是参数与模型对不上，服务商那句话本身有用，原样带出来。"""
    provider.error = status(BadRequestError, 400, "model not found in siliconflow")
    assert "model not found" in llm.test_connection(CFG)["error"]


def test_error_text_never_carries_the_key():
    """服务商把请求内容回显在错误里，也不能把密钥带上网页。"""
    text = llm.error_text(status(BadRequestError, 400, "bad key sk-secret-value"),
                         secret="sk-secret-value")
    assert "sk-secret-value" not in text
    assert "（密钥）" in text


def test_error_text_falls_back_to_exception_name():
    assert llm.error_text(RuntimeError("boom")) == "模型请求失败：RuntimeError"
