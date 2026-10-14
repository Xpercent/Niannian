# AGENTS.md — Niannian 重构版

重构自 `../Niannian`（原项目，技术债过多）。本仓库是**唯一开发目录**，
原项目只读参考，**禁止复制粘贴其代码**；其内置文档（含 `../Niannian/AGENTS.md`）
仅作架构理解，未经用户确认不作为约束依据。

## 工作方式（用户明确要求）
- 每次任务以**交付最小可运行代码**为基准，一次只跑通一个功能闭环。
- 遇到问题**先汇报，不要自己替用户做决定**（含技术选型、语义取舍）。
- 每轮自行 `git` 提交，并同步维护 `docs/REFACTOR-LOG.md`、`HANDOFF.md`。
- Web UI 需与原项目**基本一致**（配色 token、布局、交互流）。
- 服务可由 agent 自行启停（`python -m niannian`，端口 8765）。

## 运行
```powershell
.venv\Scripts\python -m niannian        # 后端 + 托管 web/dist
.venv\Scripts\python -m pytest -q       # 唯一自动化验证
cd web && npm run dev                   # 前端热更新，/api 代理到 8765
```
真机验证需要本机已登录微信 4.x（只读解密其本地库）。

## 结构
- `niannian/`：`config` / `storage`（SQLite 三表）/ `wechat`（只读采集线程 +
  命令队列 + 水位增量）/ `api`（FastAPI）。
- `web/`：React + Vite + Tailwind，手写 UI 组件（无 shadcn/radix）。
- `wechatauto-replica/`：**git submodule**，锁定上游 1.2.5.1（fbfb026）。
  视为第三方依赖，**不要改动**（改动会偏离上游且 `submodule update` 会丢）。
  需要不同行为时在本仓库 `IsolatedDB` 子类里覆写。

## 上游库的关键陷阱（已踩，务必遵守）
- **`real_sender_id` 的 id 空间是每个消息分片库各自的 `Name2Id` 表**，
  上游 `db._sender_id_index()` 却拿 `message_resource.db` 的全局 `SenderName2Id`
  去查，会解析出毫不相干的人（甚至群外成员）。`get_messages` 返回的
  `sender_username` 因此**不可信**，正文无 `wxid_x:` 前缀的行（自己发的消息、
  emoji、图片、系统消息）全部会错。修法见 `niannian/wechat.py:sender_alias_map`
  + `IsolatedDB._fix_senders`。原项目有同一隐患，只是它只导入过"全员带前缀"的
  通知群，症状没暴露。
- `sender_id == 2` 在上游被当作"自己"，但分片 `Name2Id` 的 rowid 2 是别人；
  以分片表为准，不要沿用该特判。
- 解密缓存固定在 `data/wechat/cache/<账号指纹>/`，与其它工程隔离；
  改 WAL 合并/解密逻辑要同时 bump `wechatauto.STAMP_VERSION`。
- 消息表跨分片，`local_id` **非全局唯一**，去重/定位一律用 `(sort_seq, local_id)`。
  上游 `download_image(user, local_id)` 就是因此会挑错行。

## 图片附件的关键事实（`niannian/media.py`）
- **磁盘文件名那个 32 位哈希只在消息行的 `packed_info_data` 里**；正文 XML 的
  `md5` 属性是另一套值，拿它去 `msg/attach/<md5(会话ID)>/` 找文件一条也找不到。
  上游 `get_messages` 不返回 `packed_info`，所以按 `(sort_seq, local_id)` 批量补查
  （`wechat.py:media_hash_map`），存进 metadata 的 `media_md5`。
- 本机三档：`_h.dat` 原图（点过"查看原图"才有）、`.dat` 微信下发那份、`_t.dat`
  预览图。**没在微信里点开过的图片只有 `_t.dat`**，而它能单字节 XOR 解出来——
  所以预览图必须当可用素材，不能判成"本机没有"（实测 23 张里 8 张只有预览图）。
- **采集那一刻文件可能还没落盘**（微信翻到才下预览图）。只提取一次就永久缺失，
  所以 API 读取时凭 `media_md5` 再补一次链接（`api.py:_link_media`）——纯读文件
  系统与工程缓存，不碰上游库，因此不必走采集线程。
- 库里**只存相对链接** `<账号指纹>/<会话>_<sort_seq>[_档位].<ext>`，不存二进制；
  读取要经 `safe_media_path` 收口（最多两段、拒绝对称路径/盘符/UNC/`..`）。
- 上游 `MediaDownloader` 的 `_image_files` / `decrypt_image` / `_write_decrypted`
  是这里复用的三个私有方法（定位、解密、按文件头判格式落盘 + wxgf→jpg）。


## 发送者显示名（`niannian/contacts.py`）
- 微信群聊气泡上的名字是**三档**：`备注 > 群名片 > 昵称`。上游
  `nickname_map()` 只有「备注或昵称」两档，**中间那档是缺的**。
- **群名片没有独立表列**：`chatroom_member` 只有 `room_id + member_id`，群名片
  藏在 `chat_room.ext_buffer` 的 protobuf 里（字段 1=成员wxid、2=群名片、
  4=邀请人）。上游 `get_group_members()` 不解析它。用 `contacts.protobuf_fields`
  扫描（只支持 wire 0/2，遇到别的类型就停，不猜偏移量）。
- 优先级是**实测**出来的，不是猜的：拿微信自己算好的
  `session.db.SessionTable.last_sender_display_name` 与三方比对，
  7 例「群名片/备注/昵称互不相同」的样本里 6 例（无备注）跟随群名片、
  1 例（有备注）跟随备注。改显示规则前先用这条通道复核。
- 每会话的显示名由 `IsolatedDB.display_names(user)` 现算后传给
  `normalize_message`，**不要**再把全局 `nickname_map()` 当发送者表用。
- 解析不出发送者的行（`Name2Id.user_name` 为空：拍一拍、撤回、进群提示）
  署名 `系统消息`、`sender_key` 置空，不参与按人归组。
- 群自己发的系统行正文前缀是**群 ID**（`45948752706@chatroom:`）而不是
  `wxid_`，`split_sender` 要剥掉但**不能**当成某个人的发送者。

## 前端约定（`web/`）
- `components/ui.tsx` 的 `cn()` 是**纯字符串拼接**，没有 tailwind-merge：同一个
  样式槽位出现两个类（如 `max-w-lg` + `max-w-3xl`）时谁生效取决于样式表顺序。
  因此可覆盖的尺寸/内边距必须由 prop 整档给出，**基类里不要写死**
  （`Dialog` 的 `width` / `padded` 就是这么来的）。
- 图片一律不放大显示：`<img>` 用 `w-auto max-w-full max-h-[…vh]`，不用 `w-full`，
  否则低分辨率预览图会被插值拉糊。

## 约定
- 注释与 docstring 用**中文**（公开 API 名用英文），只在"为什么"不明显处写注释。
- 采集**只读**：不驱动 GUI、不发消息、不触发微信下载附件。
- 图片已格式化为 `[图片]` 摘要 + 本机链接；其余非纯文本消息（视频/语音/文件/
  卡片/拍一拍）仍按上游文本或 XML 原样入库。
