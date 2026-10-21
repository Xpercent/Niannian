"""模型接入层：只负责"按配置发一次 OpenAI 兼容请求"，提炼的语义不在这层。

阶段二的提炼直接复用 :func:`chat`——它是纯函数式的入参出参，不起服务、不碰
微信解密缓存，所以在请求线程上调它是安全的（"请求线程一律纯读"那条约定针对的是
微信那份缓存和图片钥匙扫描，不是外部 HTTPS）。
"""

from __future__ import annotations

import json
import time

from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                    AuthenticationError, BadRequestError, NotFoundError,
                    OpenAI, RateLimitError)

from .config import parse_body

# 测试连接是人在界面上等的：没回就该说话，不能让人盯着转圈。
# 只有**一档**（2026-10-21）：思考开关从配置里删掉了，这一层不再知道这一条会不会思考，
# 只能按实测最慢的那档等——单次跑到 29.9 秒是撞过的，20 秒那条线破过（Qwen3 开思考）。
# 回复长度也不设上限（用户 2026-10-20 定：我们一个 ``max_tokens`` 都不发，
# 要限就在请求体参数那一框里自己写），所以超时是这一层唯一的护栏。
TEST_TIMEOUT_SECONDS = 45.0
TEST_PROMPT = "只回两个字：好的"


def extra_fields(settings: dict) -> dict:
    """请求体里除 ``model``/``messages`` 之外的那些字段，**全部来自那一框 JSON**。

    发什么由用户写（2026-10-21 定）：这一层不再按域名猜服务商、也不再替它决定字段
    形状——同一件事各家挂在不同字段上，而"猜形状"这种代码每猜一次就多一个盲区
    （实测：Google 有些模型连 ``reasoning_effort: "none"`` 都不吃，直接 400）。
    空着的意思就是 ``{}``，请求体里只带模型名与正文。
    """
    return parse_body(settings.get("llm_extra_body"))


def request_body(settings: dict, messages: list[dict]) -> dict:
    """这一次**实际发出去的顶层 JSON**：``model``、``messages`` 加那一框里的字段。

    :func:`chat` 组的是同一个函数，所以设置页与诊断页回显的就是真发出去的那一份，
    不是按另一种方式重画出来的对照表。
    """
    _base, model, _key = split_config(settings)
    return {"model": model, "messages": list(messages), **extra_fields(settings)}


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

    那一框 JSON 走 SDK 的 ``extra_body``：它把这些键**并进请求体顶层**，而 ``create()``
    自己只认签名里那些参数——规范外的字段直接当关键字传会被丢掉（等于没发），
    这是"用户写了什么就发什么"能成立的原因。
    ``max_retries=0``：SDK 默认失败重试两次，那会把界面上的等待时间乘三，
    而"测试连接"要的是立刻告诉用户通不通。
    """
    base, model, key = split_config(settings)
    client = OpenAI(api_key=key, base_url=base, timeout=timeout, max_retries=0)
    completion = client.chat.completions.create(
        model=model, messages=messages, extra_body=extra_fields(settings))
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
                "请求是不是真的需要这么久（模型要在服务端想很久就会）")
    elif isinstance(exc, APIConnectionError):
        text = "连不上服务商：地址写错、网络不通，或那个端口没在听"
    elif isinstance(exc, APIStatusError):
        text = f"服务商返回 {exc.status_code}：{exc.message}"
    else:
        text = f"模型请求失败：{type(exc).__name__}"
    if secret:
        text = text.replace(secret, "（密钥）")
    return text


def raw_reply(exc: Exception, secret: str = "") -> str:
    """服务商**原样回的那一份**，出错时也要留档（用户 2026-10-21 定：失败那批我要看的
    就是对方到底说了什么，而不是我们替他翻译的那句中文）。

    状态错（429/400/500）SDK 把响应体解析好挂在 ``body`` 上，读不成 JSON 的挂在
    ``message`` 里；连接错与超时**根本没有响应**，那就留空，不编一份出来。
    """
    if isinstance(exc, APIStatusError):
        text = (json.dumps(exc.body, ensure_ascii=False, indent=2)
                if exc.body is not None else str(exc.message or ""))
    elif isinstance(exc, ValueError):
        text = str(exc)          # "服务商回了空响应"这类我们这层的判断也照原样留
    else:
        text = ""
    if secret:
        text = text.replace(secret, "（密钥）")
    return text.strip()


def test_connection(settings: dict) -> dict:
    """按当前配置发一条最短请求，把结果讲成人话。

    失败**不抛异常**：测连不通是这条接口的正常结果之一，回 ``ok=False`` 与原因，
    界面才知道该显示什么，而不是收到一个 500。

    两种结果都带 ``request``：那一次**实际发出去的请求体**（格式化好、密钥抹掉）。
    参数形状现在由用户在那一框里自己写，看得见发出去的是什么才算得清是谁写坏的。
    只有出网前的校验（配置没填齐、那一框 JSON 读不懂）给不出这一份——那时还没组出来。
    """
    started = time.perf_counter()
    messages = [{"role": "user", "content": TEST_PROMPT}]
    secret = str(settings.get("llm_api_key") or "")
    try:
        sent = _pretty(request_body(settings, messages), secret)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "elapsed_ms": _elapsed(started)}
    try:
        result = chat(settings, messages, timeout=TEST_TIMEOUT_SECONDS)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "request": sent,
                "elapsed_ms": _elapsed(started)}
    except Exception as exc:
        return {"ok": False, "error": error_text(exc, secret, TEST_TIMEOUT_SECONDS),
                "request": sent, "elapsed_ms": _elapsed(started)}
    notes: list[str] = []
    if result["reasoning"]:
        # 不再有"开关与服务商表现不一致"这种判断：这一层不控制它想不想，只报看到的
        notes.append("服务商回了思考内容（要它别想就在那一框里写那一家的字段）")
    if not result["text"]:
        # 通了却没回话不算坏，但这句得讲出来，不然看着像连接坏了
        notes.append("服务商回了空正文，链路本身是通的")
    return {"ok": True, "elapsed_ms": _elapsed(started), "model": result["model"],
            "reply": result["text"], "request": sent, "note": "；".join(notes)}


def _pretty(body: dict, secret: str) -> str:
    text = json.dumps(body, ensure_ascii=False, indent=2)
    return text.replace(secret, "（密钥）") if secret else text


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
