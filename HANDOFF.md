# HANDOFF.md — 交接

## 当前状态
阶段一「微信会话看板」可运行：只读采集本机微信 4.x 本地库，关注会话后自动
导入最近 `history_limit` 条并按水位增量续采，FastAPI + React 看板展示。
`pytest` 14 项通过；服务 `python -m niannian` → http://127.0.0.1:8765。

## 最近一轮：修复微信发送者名字解析错误
**症状**：群里出现根本不是群成员的署名（如"吴寒"），且私聊里自己发的消息
全部署上对方的名字（`is_self` 恒为 0，王五月会话 95 条"王五月"/5 条"我"）。

**根因**：微信 4.x 消息分片库的 `Msg_*.real_sender_id` 指向**该分片库自己的
`Name2Id` 表**，而上游 `wechatauto` 拿 `message_resource.db` 的全局
`SenderName2Id` 解析，两套 id 空间互不相通。正文带 `wxid_x:` 前缀的行靠前缀
侥幸正确，无前缀的行（自己发的消息 / emoji / 图片 / 系统消息）全部落到错误映射。

**改动**（仅 `niannian/wechat.py` + 测试，未碰子模块）：
- 新增 `sender_alias_map()`：按 `(sort_seq, local_id)` 从分片 `Name2Id` 取真实发送者。
- `IsolatedDB` 覆写 `get_messages` / `get_new_messages`，经 `_fix_senders` 重写
  `sender_username`（解析不到则置空，回落到正文前缀）。
- `normalize_message` 改为**分片解析优先、正文前缀兜底**。

**验证**：清库重采后，群 100 条署名全部落在真实 4 名群成员内（"吴寒"16 条
→"我"16 条）；89 条带前缀的行前缀与分片解析**零冲突**；私聊 `is_self` 变成分
布自然的 56/44；增量轮询路径 `get_new_messages` 单测同规则通过；浏览器实测
群聊与私聊署名均正确。

**残留**：1 条拍一拍 appmsg 的 `real_sender_id=9` 在 `Name2Id` 里 `user_name`
为空，回落显示"群成员"（原版同场景显示成"助班学长"，已是改善）。

## 待用户决定
- 空 `user_name` 的系统/拍一拍类消息怎么署名（"系统"？按 `message_type` 特判？）。
- 群成员显示名策略：当前沿用原版「备注优先于昵称」（故显示"26同"而非群内昵称
  "鱼尾巴不吃"），是否改为贴近微信界面优先群内昵称。
- 非纯文本消息（图片/转账/卡片）的 XML 正文格式化，属阶段二候选。

## 环境备注
- 旧的问题库保留为 `data/niannian.db.bad-1`（含修复前数据，可对照；确认可删）。
- `data/diag_*.py`、`data/verify_senders.py` 是本轮排查脚本，`data/` 不入库。
