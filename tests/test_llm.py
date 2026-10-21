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
       "llm_include_images": False,
       "llm_extra_body": ""}


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


def test_an_empty_box_sends_nothing_but_the_model_and_the_text(provider):
    """那一框空着 = **一个额外字段都不带**：默认值全交回服务商。

    这一条同时钉住两件旧事：我们不再自己发 `enable_thinking`/`reasoning_effort`
    （2026-10-21 起形状由用户写），也从来不发 `max_tokens`（2026-10-20 定）。
    """
    llm.test_connection(CFG)
    assert provider.calls[0] == {
        "model": "Qwen/Qwen3-8B",
        "messages": [{"role": "user", "content": llm.TEST_PROMPT}],
        "extra_body": {}}


def test_whatever_the_box_says_reaches_the_top_level_of_the_body(provider):
    """框里写什么就发什么：键并进请求体顶层，我们不看它像哪一家的字段。

    这正是这一版换掉 `thinking_body()` 的理由——各家把同一件事挂在不同名字上，
    而猜形状的代码每猜一次就多一个盲区（实测 Google 有些模型连
    ``reasoning_effort: "none"`` 都不吃，整条 400）。
    """
    box = '{"temperature": 0, "reasoning_effort": "minimal", "max_tokens": 512}'
    llm.test_connection({**CFG, "llm_extra_body": box})
    assert provider.calls[0]["extra_body"] == {"temperature": 0,
                                               "reasoning_effort": "minimal",
                                               "max_tokens": 512}


def test_the_body_the_page_shows_is_the_body_that_goes_out(provider):
    """`request_body()` 与 `chat()` 组的是同一份：回显不是按别的方式重画的对照表。"""
    box = '{"response_format": {"type": "json_object"}}'
    settings = {**CFG, "llm_extra_body": box}
    built = llm.request_body(settings, [{"role": "user", "content": "hi"}])
    llm.chat(settings, [{"role": "user", "content": "hi"}])
    sent = provider.calls[0]
    assert built["model"] == sent["model"]
    assert built["messages"] == sent["messages"]
    assert {key: value for key, value in built.items()
            if key not in ("model", "messages")} == sent["extra_body"]


@pytest.mark.parametrize("box,phrase", [
    ("{not json}", "读不懂 JSON"),
    ('"temperature": 0.2', "读不懂 JSON"),
    ("[1, 2]", "要写成 JSON 对象"),
    ('{"model": "other"}', "model 不能写在请求体参数里"),
    ('{"messages": []}', "messages 不能写在请求体参数里"),
    ('{"stream": true}', "stream 不能写在请求体参数里"),
    ('{"api_key": "sk-x"}', "api_key 不能写在请求体参数里"),
])
def test_a_broken_box_is_called_out_before_the_network(monkeypatch, box, phrase):
    """坏 JSON 与越界字段在**出网之前**就说，不拿去撞服务商那句读不懂的 400。"""
    fake = install(monkeypatch)
    got = llm.test_connection({**CFG, "llm_extra_body": box})
    assert got["ok"] is False and phrase in got["error"]
    assert fake.constructed == []           # 一次都没出网
    assert "request" not in got             # 还没组出请求体，没有"发出去的那一份"可看


def test_the_reply_still_decodes_when_the_box_is_right(provider):
    """服务商回了正文就照收：那一框里的字段我们不解释、也不检查回包形状。"""
    provider.response = completion(text="收到")
    got = llm.test_connection({**CFG, "llm_extra_body": '{"temperature": 0.2}'})
    assert got["ok"] is True and got["reply"] == "收到"


def test_a_thinking_provider_says_so_instead_of_us_guessing(provider):
    """这一层不再判断"开关与服务商表现不一致"（开关没了），只报看到的：回了思考内容就说。"""
    provider.response = completion(reasoning="先想想用户要什么")
    assert "回了思考内容" in llm.test_connection(CFG)["note"]

    provider.response = completion(reasoning=None)
    assert llm.test_connection(CFG)["note"] == ""


def test_empty_reply_is_explained_instead_of_looking_broken(monkeypatch):
    """通了却没回话要在 note 里说清楚，不能让用户以为连接坏了。"""
    install(monkeypatch, response=completion(text="", reasoning=""))
    got = llm.test_connection(CFG)
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


def test_the_wait_is_one_wide_band_because_we_cannot_tell(provider):
    """思考开关没了，这一层读不出这一条会不会想 → 只有一档，按实测最慢的那档等。

    2026-10-21 起请求体形状归用户，`llm_thinking` 那两档（20/45 秒）没有依据了；
    留一档就得留**宽**的那一档：开思考的单次实测跑到过 29.9 秒。
    """
    assert llm.TEST_TIMEOUT_SECONDS >= 30.0
    llm.test_connection({**CFG, "llm_extra_body": '{"reasoning_effort": "high"}'})
    assert provider.constructed[0]["timeout"] == llm.TEST_TIMEOUT_SECONDS


def test_test_connection_reports_reply_and_the_body_it_sent(provider):
    got = llm.test_connection({**CFG, "llm_extra_body": '{"temperature": 0}'})
    assert got["ok"] is True
    assert got["reply"] == "好的"
    assert got["model"] == "Qwen/Qwen3-8B"
    assert got["note"] == ""
    assert isinstance(got["elapsed_ms"], int) and got["elapsed_ms"] >= 0
    # 看得见发出去的是什么，才算得清 400 是谁写坏的
    assert '"temperature": 0' in got["request"]
    assert llm.TEST_PROMPT in got["request"]


def test_a_failed_test_also_shows_what_we_sent(monkeypatch):
    """撞 400 那一次最需要看的正是"你发了什么"——失败路径也要带 request。"""
    install(monkeypatch, error=status(BadRequestError, 400, "invalid argument"))
    got = llm.test_connection({**CFG, "llm_extra_body": '{"reasoning_effort": "none"}'})
    assert got["ok"] is False
    assert "invalid argument" in got["error"]
    assert '"reasoning_effort": "none"' in got["request"]


def test_the_body_we_show_never_carries_the_key(provider):
    """密钥不出网关：用户把密钥写进了那一框，回显里也得抹掉。"""
    got = llm.test_connection({**CFG, "llm_extra_body": '{"note": "sk-secret-value"}'})
    assert got["ok"] is True
    assert "sk-secret-value" not in got["request"]
    assert "（密钥）" in got["request"]


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


# ---------------------------------------------------------------- 服务商原样回的那一份


def status_with_body(code, body):
    request = httpx.Request("POST", "https://api.siliconflow.cn/v1/chat/completions")
    return RateLimitError("Error code: 429", response=httpx.Response(code, request=request),
                          body=body)


def test_raw_reply_keeps_the_provider_json():
    """失败那批用户要看的是**服务商原样回的那份**：429 正文里有 quota 与 retry 细节，
    翻成"等一会儿再试"那一句就全丢了。"""
    got = llm.raw_reply(status_with_body(429, {"error": {
        "code": 429, "message": "Resource has been exhausted",
        "status": "RESOURCE_EXHAUSTED"}}))

    assert got.startswith("{")
    assert '"status": "RESOURCE_EXHAUSTED"' in got


def test_raw_reply_falls_back_to_the_message_when_body_is_not_json():
    assert llm.raw_reply(status(APIStatusError, 503, "upstream said nope")) == "upstream said nope"


def test_raw_reply_is_empty_when_there_was_no_response_at_all():
    """连接错与超时**根本没有响应体**，留空比编一份出来强。"""
    assert llm.raw_reply(APIConnectionError(request=request())) == ""
    assert llm.raw_reply(APITimeoutError(request=request())) == ""


def test_raw_reply_keeps_our_own_contract_errors():
    assert llm.raw_reply(ValueError("服务商回了空响应")) == "服务商回了空响应"


def test_raw_reply_never_carries_the_key():
    text = llm.raw_reply(status_with_body(
        429, {"error": {"message": "quota for key sk-secret-value exhausted"}}),
        secret="sk-secret-value")
    assert "sk-secret-value" not in text and "（密钥）" in text
