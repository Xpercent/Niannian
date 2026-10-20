"""非纯文本消息：把正文 XML 解析成结构化描述 + 进气泡的一行中文摘要。

只处理本机实测出现过的形状，认不出的一律返回 ``None``，由调用方按上游文本原样
入库——不猜结构，也不把 XML 伪装成摘要。

上游给的类型标签很粗：``local_type`` 49 一律叫「文件/链接/卡片」，真正区分靠
``<appmsg>`` 里的 ``<type>``；系统消息则整条是 ``<sysmsg>``，``type`` 属性才是子类。

描述统一放进 metadata 的 ``attachment`` 键：图片附件管线（``wechat.py`` /
``api.py``）和前端已经认这个名字，撤回、拍一拍这类非附件也沿用，免得存量行和
新行分成两种形状。
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

# <appmsg><type> → 我们的 kind；没列出的 type 落到 card 兜底（只认标题，不编内容）
APPMSG_KIND = {4: "link", 5: "link", 6: "file", 19: "merged", 33: "miniapp",
               57: "quote", 62: "pat", 2000: "transfer"}
KIND_LABEL = {"image": "图片", "emoji": "动画表情", "video": "视频", "file": "文件",
              "link": "链接", "miniapp": "小程序", "merged": "聊天记录",
              "transfer": "转账", "card": "卡片", "pat": "拍一拍", "quote": "引用",
              "contact": "名片",
              "recall": "撤回", "notice": "群动态"}
# 这几类微信自己已经把话说成整句了，正文直接用那句话，不再套方括号。
SENTENCE_KINDS = ("pat", "recall", "notice")
# 引用行上那行小字只是"提示你点下去看"，全文一律不入库（实测被引原文最长 2311 字）。
QUOTE_LABEL_LIMIT = 40
TAGS = re.compile(r"<[^>]+>")


def parse_xml(text: str):
    """正文是 XML 时返回根节点，纯文本或解析失败返回 None（不猜半截结构）。"""
    stripped = (text or "").strip()
    if not stripped.startswith("<"):
        return None
    try:
        return ElementTree.fromstring(stripped)
    except ElementTree.ParseError:
        return None


def human_size(value) -> str:
    if not isinstance(value, int) or value <= 0:
        return ""
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def clock(seconds) -> str:
    if not isinstance(seconds, int) or seconds <= 0:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


def _child(node: ElementTree.Element, name: str) -> str:
    """子元素文本；CDATA 由 ElementTree 自动解出来，这里只去空白。"""
    found = node.find(name) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def _attr(node: ElementTree.Element, name: str) -> str:
    return (node.get(name) or "").strip()


def _int(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def describe(body: str) -> dict | None:
    """消息正文 → 结构化描述；纯文本、认不出或刻意不做的返回 None。"""
    root = parse_xml(body)
    if root is None:
        return None
    if root.tag == "sysmsg":
        return _sysmsg(root)
    if root.tag != "msg":
        return None
    for child in root:
        handler = _NODES.get(child.tag)
        if handler is not None:
            found = handler(child)
            if found:
                return found
    # 名片（上游 type 42）没有子节点，全部信息挂在 <msg> 的属性上。
    return _contact(root)


def _contact(root: ElementTree.Element) -> dict | None:
    """别人分享的名片：昵称 + 一句自我介绍。

    介绍取自 ``certinfo``（认证账号/服务号的功能介绍），个人名片那格是空的、
    自我介绍写在 ``sign``（个性签名）。没有 ``nickname`` 就判不认：认不出是谁的名片，
    不编内容，交回调用方按上游文本入库。
    """
    nickname = _attr(root, "nickname")
    if not nickname:
        return None
    note = _attr(root, "certinfo") or _attr(root, "sign")
    return {"kind": "contact", "name": nickname, "note": note,
            "username": _attr(root, "username")}


def _image(node: ElementTree.Element) -> dict:
    # 不从 XML 取 md5：磁盘文件名的哈希只在 packed_info_data 里，见 media.py。
    # 也不带尺寸进摘要：``cdnthumb*`` 是**预览图**尺寸，写成"[图片 262x576]"
    # 会让人以为看到的就是这个分辨率。
    return {"kind": "image"}


def _emoji(node: ElementTree.Element) -> dict:
    # 动画表情只出占位：本机那几份是密文（没破），正文 `cdnurl` 又是外部消息给的直链，
    # 取图要发 HTTP 且受时效约束，所以不解析 md5/宽高，也不留可供下游取图的字段。
    return {"kind": "emoji"}


def _video(node: ElementTree.Element) -> dict:
    return {"kind": "video", "seconds": _int(_attr(node, "playlength")),
            "bytes": _int(_attr(node, "length")),
            "md5": _attr(node, "md5"), "new_md5": _attr(node, "newmd5")}


def _appmsg(node: ElementTree.Element) -> dict | None:
    msg_type = _int(_child(node, "type"))
    if msg_type == 57:
        return _quote(node)
    kind = APPMSG_KIND.get(msg_type, "card")
    # 没列出的 type 里，只有真带了小程序标识的才认成小程序；外链卡片也常挂着
    # 一个字段全空的 <weappinfo>，看结构不看标签才不会被空壳骗到。
    app = node.find("weappinfo")
    if kind == "card" and app is not None and (
            _child(app, "appid") or _child(app, "username")):
        kind = "miniapp"
    title = _child(node, "title")
    if kind == "file":
        return {"kind": kind, "name": title,
                "ext": _child(node, "appattach/fileext"),
                "bytes": _int(_child(node, "appattach/totallen")),
                "md5": _child(node, "md5")}
    if kind == "merged":
        items = _record_items(node)
        return {"kind": kind, "name": title or "聊天记录",
                "count": len(items), "items": items,
                "preview": _child(node, "des")}
    if kind == "pat":
        info = node.find("patinfo")
        return {"kind": kind, "text": title,
                "from": _child(info, "fromusername"),
                "patted": _child(info, "pattedusername"),
                "say": _child(info, "patsuffix")}
    if kind == "transfer":
        pay = node.find("wcpayinfo")
        return {"kind": kind, "name": title, "amount": _child(pay, "feedesc"),
                "sub_type": _child(pay, "paysubtype"), "memo": _child(pay, "pay_memo"),
                "transfer_id": _child(pay, "transferid"),
                "transaction_id": _child(pay, "transcationid"),
                "payer_username": _child(pay, "payer_username"),
                "receiver_username": _child(pay, "receiver_username"),
                "description": _child(node, "des")}
    desc = {"kind": kind, "name": title, "url": _child(node, "url"),
            "source": _child(node, "sourcedisplayname")}
    if kind == "miniapp" and app is not None:
        desc["appid"] = _child(app, "appid")
        desc["page_path"] = _child(app, "pagepath")
        desc["source"] = desc["source"] or _child(app, "username")
    return desc


def _quote(node: ElementTree.Element) -> dict | None:
    """引用/回复消息：正文是回复那句话，被引的那条只留定位和一行短标签，全文不入库。"""
    ref = node.find("refermsg")
    if ref is None:
        return None
    return {"kind": "quote", "text": _child(node, "title"),
            "quoted": {"type": _int(_child(ref, "type")),
                       "svrid": _child(ref, "svrid"),
                       # 作者只认 chatusr：fromusr 在群里是会话 ID，但有些客户端填的是
                       # 被引作者本人（实测 27 条里有 5 条），拿它当作者会认错人。
                       "author": _child(ref, "chatusr") or _child(ref, "fromusr"),
                       "name": _child(ref, "displayname"),
                       "at": _int(_child(ref, "createtime")),
                       "label": _quote_label(_child(ref, "content")
                                             or _child(ref, "partialtext"))}}


def _quote_label(raw: str) -> str:
    """被引消息的一行小字。

    引用卡片/图片时 ``refermsg/content`` 是**整段转义后的 XML**（实测最长 2311 字），
    必须递归换成摘要，否则引用行会把 XML 贴进气泡；递归也认不出时剥掉标签取纯文本。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("<"):
        label = summarize(describe(text)) or " ".join(TAGS.sub(" ", text).split())
    else:
        label = " ".join(text.split())
    if len(label) > QUOTE_LABEL_LIMIT:
        label = label[:QUOTE_LABEL_LIMIT] + "…"
    return label


def _record_items(node: ElementTree.Element) -> list[dict]:
    """合并转发的 ``recorditem`` 是 CDATA 里再套一层 XML，要二次解析。"""
    inner = parse_xml(_child(node, "recorditem"))
    if inner is None:
        return []
    items = []
    for item in inner.iter("dataitem"):
        items.append({"name": _child(item, "sourcename"),
                      "text": _child(item, "datadesc") or _child(item, "datatitle"),
                      "time": _child(item, "sourcetime"),
                      "data_type": _attr(item, "datatype")})
    return items


def _sysmsg(root: ElementTree.Element) -> dict | None:
    kind = (root.get("type") or "").strip()
    if kind == "revokemsg":
        block = root.find("revokemsg")
        text = _child(block, "content")
        return {"kind": "recall", "text": text,
                "at": _int(_child(block, "revoketime"))} if text else None
    if kind == "sysmsgtemplate":
        text = _template_text(root)
        return {"kind": "notice", "text": text} if text else None
    return None


def _template_text(root: ElementTree.Element) -> str:
    """把 ``$username$修改群名为"$remark$"`` 里的占位符换成 link_list 给的值。"""
    block = root.find("sysmsgtemplate/content_template")
    if block is None:
        return ""
    plain = _child(block, "plain")
    text = _child(block, "template")
    if not text:
        return plain
    for link in block.findall("link_list/link"):
        name = (link.get("name") or "").strip()
        if not name:
            continue
        member = link.find("memberlist/member")
        value = (_child(member, "nickname") if member is not None else "") or \
            _child(link, "plain")
        text = text.replace(f"${name}$", value)
    return text


def summarize(desc: dict | None) -> str:
    """进气泡的一行摘要；没有描述返回空串，由调用方回落到上游文本。

    方括号里只放类型标签，能读的信息（文件名、来源、时长、金额）都放在括号外，
    长标题被包进 ``[…]`` 会整行都像代码。唯一的例外是转账：方向和对象在标签前
    （``向影 [转账] ￥10.00``），那是用户定的措辞。
    """
    if not desc:
        return ""
    kind = desc.get("kind") or ""
    label = KIND_LABEL.get(kind) or "消息"
    if kind == "quote":
        # 正文只放"这个人说的话"；引用是上下文，不该冒充本句，将来喂模型也一样。
        text = (desc.get("text") or "").strip()
        if text:
            return text
        quoted = desc.get("quoted") or {}
        detail = "：".join(part for part in (quoted.get("name") or "",
                                            quoted.get("label") or "") if part)
        return f"[{label}] {detail}" if detail else f"[{label}]"
    if kind in SENTENCE_KINDS:
        return desc.get("text") or f"[{label}]"
    if kind == "contact":
        # 措辞由用户定：介绍用括号跟在名字后面，不是 " · "（那读起来像链接的来源）
        note = f" ({desc['note']})" if desc.get("note") else ""
        return f"[{label}] {desc.get('name') or ''}{note}"
    name = desc.get("name") or ""
    tail = ""
    if kind == "file":
        name = name or "未命名文件"
        tail = human_size(desc.get("bytes"))
    elif kind == "video":
        tail = clock(desc.get("seconds"))
    elif kind == "transfer":
        # 标题永远是"微信转账"四个字，标签已经说了，别再重复一遍
        name = ""
        tail = desc.get("amount") or ""
    elif kind == "merged":
        name = name or "聊天记录"
        tail = f"{desc.get('count')} 条" if desc.get("count") else ""
    elif kind not in ("image", "emoji"):
        tail = desc.get("source") or ""
    label_part = f"[{label}]"
    if kind == "transfer" and desc.get("target"):
        direction = "向" if desc.get("direction") == "out" else "收到"
        label_part = f"{direction}{desc['target']} {label_part}"
    parts = [label_part]
    if name:
        parts.append(name)
    if tail:
        # 前面已经有名字了才用 · 分隔，否则 [视频] · 0:06 这种读起来像漏了字
        parts.append(f"· {tail}" if name else tail)
    return " ".join(parts)


_NODES = {"img": _image, "emoji": _emoji, "videomsg": _video, "appmsg": _appmsg}
