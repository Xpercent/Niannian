"""模型接入层：只负责"按配置发一次 OpenAI 兼容请求"，提炼的语义不在这层。

阶段二的提炼直接复用 :func:`chat`——它是纯函数式的入参出参，不起服务、不碰
微信解密缓存，所以在请求线程上调它是安全的（"请求线程一律纯读"那条约定针对的是
微信那份缓存和图片钥匙扫描，不是外部 HTTPS）。
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                    AuthenticationError, BadRequestError, NotFoundError,
                    OpenAI, RateLimitError)

# 测试连接是人在界面上等的：20 秒没回就该说话，不能让人盯着转圈。
TEST_TIMEOUT_SECONDS = 20.0
# 开思考的单次耗时实测在 3.5～21 秒之间晃过（2026-10-17，Qwen/Qwen3-8B），20 秒那道线
# 被直接撞破过，所以**开思考只放宽等待**。回复长度不设上限（用户 2026-10-20 定：请求体里
# 一个 ``max_tokens`` 都不发），超时是这一层唯一的护栏。
TEST_TIMEOUT_THINKING_SECONDS = 45.0
TEST_PROMPT = "只回两个字：好的"


def thinking_body(enabled, base_url: str = "") -> dict:
    """思考模式在请求体里的写法：**形状按服务商给**（2026-10-20 定的）。

    SiliconFlow/Qwen3 认 ``enable_thinking``，而且**默认就是开的**：什么参数都不带
    时回了 ``reasoning_content`` 并花掉 334 token，显式关掉只剩 19 token。所以
    "关"必须**显式发过去**，省略参数等于替用户开着。

    Google 的 OpenAI 兼容层用严格的 proto-JSON 解析，OpenAI 规范之外的顶层字段
    一律 400（实测 ``Unknown name "enable_thinking": Cannot find field``；
    ``google.thinking_config`` 与 ``thinking_level`` 同样被拒）。它只吃标准的
    ``reasoning_effort``——``high``/``none``/``minimal`` 三档都收，但有没有真的改变
    思考行为看不出来（``reasoning_content`` 一直是空），所以这一头只是把意图表达出去。
    """
    if _is_google(base_url):
        return {"reasoning_effort": "high" if enabled else "none"}
    return {"enable_thinking": bool(enabled)}


def _is_google(base_url: str) -> bool:
    """地址是不是 Google 那家（AI Studio 的 generativelanguage 与 Vertex 的 aiplatform 同域）。"""
    base = str(base_url or "").strip()
    # 用户可能不带协议头就填进来，那样 urlparse 读不出 netloc
    host = urlparse(base if "//" in base else f"//{base}").netloc.lower()
    return host == "googleapis.com" or host.endswith(".googleapis.com")


def split_config(settings: dict) -> tuple[str, str, str]:
    """取出 ``(base_url, model, api_key)``，缺哪个就用中文说哪个没填。

    在出网**之前**检查：配置没填齐是我的问题，不该拿去问服务商。
    """
    base = str(settings.get("llm_base_url") or "").strip()
    model = str(settings.get("llm_model") or "").strip()
    key = str(settings.get("llm_api_key") or "").strip()
    if not base:
        raise ValueError("还没有填服务商地址")
    if not model:
        raise ValueError("还没有填模型名")
    if not key:
        raise ValueError("还没有填 API Key")
    return base, model, key


def chat(settings: dict, messages: list[dict], *,
         timeout: float = TEST_TIMEOUT_SECONDS) -> dict:
    """发一次对话请求，回 ``{text, reasoning, model}``。

    请求体里**不带** ``max_tokens``：长度上限由服务商自己定（用户 2026-10-20 定）。
    ``llm_temperature`` 同一条思路——**填了才发**，空的意思就是不发这个字段（用户
    2026-10-20 定）：各家默认值不一样，而我们替它决定就是给不接受温度的模型塞坏参数。
    ``max_retries=0``：SDK 默认失败重试两次，那会把界面上的等待时间乘三，
    而"测试连接"要的是立刻告诉用户通不通。
    """
    base, model, key = split_config(settings)
    client = OpenAI(api_key=key, base_url=base, timeout=timeout, max_retries=0)
    payload: dict = {"model": model, "messages": messages,
                     "extra_body": thinking_body(settings.get("llm_thinking"), base)}
    temperature = settings.get("llm_temperature")
    if temperature is not None:
        payload["temperature"] = temperature
    completion = client.chat.completions.create(**payload)
    if not getattr(completion, "choices", None):
        raise ValueError("服务商回了空响应")
    message = completion.choices[0].message
    return {"text": (getattr(message, "content", None) or "").strip(),
            "reasoning": (getattr(message, "reasoning_content", None) or "").strip(),
            "model": getattr(completion, "model", None) or model}


def error_text(exc: Exception, secret: str = "",
               timeout: float = TEST_TIMEOUT_SECONDS) -> str:
    """把服务商的错翻成一句给用户看的话，并且**绝不带上密钥**。

    判断顺序是"先具体后笼统"：``AuthenticationError`` 与 ``BadRequestError`` 都是
    :class:`APIStatusError` 的子类，而 ``APITimeoutError`` 是 ``APIConnectionError``
    的子类，反过来写就会把 401 说成"连不上"。
    """
    if isinstance(exc, AuthenticationError):
        text = "API Key 被服务商拒了（401）：检查密钥有没有填对、是不是已经作废"
    elif isinstance(exc, NotFoundError):
        text = "服务商找不到这个模型或地址（404）：检查模型名和 URL 里的 /v1"
    elif isinstance(exc, RateLimitError):
        text = "服务商说请求太频繁（429）：等一会儿再试"
    elif isinstance(exc, BadRequestError):
        # 参数与模型不匹配最常走这条，服务商那句话本身就有用，原样带出来
        text = f"服务商不接受这个请求（400）：{exc.message}"
    elif isinstance(exc, APITimeoutError):
        text = (f"等了 {int(timeout)} 秒服务商还没回话：地址对不对、网络通不通，"
                "开着思考可以再等等或关掉再测")
    elif isinstance(exc, APIConnectionError):
        text = "连不上服务商：地址写错、网络不通，或那个端口没在听"
    elif isinstance(exc, APIStatusError):
        text = f"服务商返回 {exc.status_code}：{exc.message}"
    else:
        text = f"模型请求失败：{type(exc).__name__}"
    if secret:
        text = text.replace(secret, "（密钥）")
    return text


def test_timeout(settings: dict) -> float:
    """这一次测试最多等多久。

    两档分开是实测：开思考的单次耗时在 3.5～21 秒之间晃，20 秒那道线被撞破过。
    额度不设上限，所以等待是仅剩的护栏。
    """
    return TEST_TIMEOUT_THINKING_SECONDS if bool(settings.get("llm_thinking")) \
        else TEST_TIMEOUT_SECONDS


def test_connection(settings: dict) -> dict:
    """按当前配置发一条最短请求，把结果讲成人话。

    失败**不抛异常**：测连不通是这条接口的正常结果之一，回 ``ok=False`` 与原因，
    界面才知道该显示什么，而不是收到一个 500。
    """
    thinking = bool(settings.get("llm_thinking"))
    timeout = test_timeout(settings)
    started = time.perf_counter()
    try:
        result = chat(settings, [{"role": "user", "content": TEST_PROMPT}], timeout=timeout)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "elapsed_ms": _elapsed(started)}
    except Exception as exc:
        return {"ok": False,
                "error": error_text(exc, str(settings.get("llm_api_key") or ""), timeout),
                "elapsed_ms": _elapsed(started)}
    notes: list[str] = []
    if thinking and not result["reasoning"]:
        notes.append("开了思考但服务商没回思考内容，这个模型可能不支持")
    elif not thinking and result["reasoning"]:
        notes.append("没开思考却回了思考内容，服务商可能忽略了这个参数")
    if not result["text"]:
        # 通了却没回话不算坏，但这句得讲出来，不然看着像连接坏了
        notes.append("服务商回了空正文，链路本身是通的")
    return {"ok": True, "elapsed_ms": _elapsed(started), "model": result["model"],
            "reply": result["text"], "thinking": thinking, "note": "；".join(notes)}


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
