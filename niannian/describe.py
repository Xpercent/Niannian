"""非纯文本消息：把正文 XML 解析成结构化描述 + 进气泡的一行中文摘要。

只处理本机实测出现过的形状（659 条里那 156 条 XML 全过了一遍），认不出的一律
返回 ``None``，由调用方按上游文本原样入库——不猜结构，也不把 XML 伪装成摘要。

上游给的类型标签很粗：``local_type`` 49 一律叫「文件/链接/卡片」，真正区分靠
``<appmsg>`` 里的 ``<type>``；系统消息则整条是 ``<sysmsg>``，``type`` 属性才是子类。

描述统一放进 metadata 的 ``attachment`` 键：图片附件管线（``wechat.py`` /
``api.py``）和前端已经认这个名字，撤回、拍一拍这类非附件也沿用，免得存量行和
新行分成两种形状。
"""

from __future__ import annotations

from xml.etree import ElementTree

# <appmsg><type> → 我们的 kind；没列出的 type 落到 card 兜底（只认标题，不编内容）
APPMSG_KIND = {4: "link", 5: "link", 6: "file", 19: "merged", 33: "miniapp",
               62: "pat", 2000: "transfer"}
# 引用/回复消息里嵌着被引原文（实测最长 2311 字），本轮按用户要求不动，正文留 XML。
QUOTE_TYPE = 57
KIND_LABEL = {"image": "图片", "emoji": "动画表情", "video": "视频", "file": "文件",
              "link": "链接", "miniapp": "小程序", "merged": "聊天记录",
              "transfer": "转账", "card": "卡片", "pat": "拍一拍",
              "recall": "撤回", "notice": "群动态"}
# 这几类微信自己已经把话说成整句了，正文直接用那句话，不再套方括号。
SENTENCE_KINDS = ("pat", "recall", "notice")


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
    return None


def _image(node: ElementTree.Element) -> dict:
    # 不从 XML 取 md5：磁盘文件名的哈希只在 packed_info_data 里，见 media.py。
    # 也不带尺寸进摘要：``cdnthumb*`` 是**预览图**尺寸，写成"[图片 262x576]"
    # 会让人以为看到的就是这个分辨率。
    return {"kind": "image"}


def _emoji(node: ElementTree.Element) -> dict:
    return {"kind": "emoji", "md5": _attr(node, "md5"),
            "bytes": _int(_attr(node, "len")),
            "width": _int(_attr(node, "width")),
            "height": _int(_attr(node, "height")),
            "sticker_type": _int(_attr(node, "type"))}


def _video(node: ElementTree.Element) -> dict:
    return {"kind": "video", "seconds": _int(_attr(node, "playlength")),
            "bytes": _int(_attr(node, "length")),
            "md5": _attr(node, "md5"), "new_md5": _attr(node, "newmd5")}


def _appmsg(node: ElementTree.Element) -> dict | None:
    msg_type = _int(_child(node, "type"))
    if msg_type == QUOTE_TYPE:
        return None
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
    if kind in SENTENCE_KINDS:
        return desc.get("text") or f"[{label}]"
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
