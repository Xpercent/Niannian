# AGENTS.md — Niannian 重构版

重构自 `../Niannian`（原项目，技术债过多）。本仓库是**唯一开发目录**，
原项目只读参考，**禁止复制粘贴其代码**；其内置文档（含 `../Niannian/AGENTS.md`）
仅作架构理解，未经用户确认不作为约束依据。

## 工作方式（用户明确要求）
- 每次任务以**交付最小可运行代码**为基准，一次只跑通一个功能闭环。
- 遇到问题**先汇报，不要自己替用户做决定**（含技术选型、语义取舍）。
- **涉及入库解析/库结构的改动，直接删库重建（清库重采）让改动对存量生效，不用再问**
  （用户 2026-10-14 明确授权；重建前把关注清单与每会话导入条数存下来，旧库留一份备份）。
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
- `niannian/`：`config`（默认值/校验/落盘）/ `storage`（SQLite 三表）/
  `wechat`（**只剩**只读采集线程 + 命令队列 + 水位增量 + 每轮的那些限额）/
  `wechat_db`（建上游读取器 `IsolatedDB` + 只在分片上跑的补查）/
  `normalize`（上游行 → 本地记录：署名、摘要、去重键；纯函数）/
  `ids`（账号指纹与会话主键，媒体目录也用它命名）/
  `contacts`（发送者显示名与群名片 protobuf）/
  `describe`（正文 XML → 结构化描述 + 一行摘要）/
  `media`（图片定位、解密、链接收口）/ `api`（FastAPI）。
- 切分的判据是"谁有权碰什么"：只有 `wechat_db` 里的东西读微信的解密缓存，
  且必须经 `Collector` 的命令队列进采集线程；`normalize`/`ids`/`describe` 是纯函数，
  阶段二的提炼要直接复用它们，不需要起服务。
- `web/`：React + Vite + Tailwind，手写 UI 组件（无 shadcn/radix）；
  `pages/` 下两页：`sessions`（微信会话）、`settings`（设置）。
- `tests/test_upstream_contract.py`：**认接口不认版本号**地守住上游私有成员
  （`MediaDownloader` 那 7 个 + `WeChatDB` 我们调到的那些）。其余用例全打在替身上，
  上游改名时它们照样全绿而生产立刻瞎，所以 bump pin 前这条必须先看。
- `scripts/capture_fixtures.py`：把关注会话的**原始正文**（未解析的 XML 也在内）导成
  `data/fixtures/capture.db`，供 `tests/test_capture_replay.py` 离线回放。跑它要**先停服务**
  （同一份解密缓存不该被两个读取方同时开）；产物是真实聊天，落在 `data/`（已 gitignore）。
  测试只断言"任何一次重新捕获都成立"的不变式，fixture 缺失就 skip，不造数据刷绿。
- `wechatauto-replica/`：**git submodule**，锁定上游 1.2.6.1（62503d7）。
  视为第三方依赖，**不要改动**（改动会偏离上游且 `submodule update` 会丢）。
  需要不同行为时在本仓库 `IsolatedDB` 子类里覆写。
  升级流程：`cd wechatauto-replica && git fetch --all --tags` → 看清
  `git log --oneline <旧pin>..origin/main` 改了哪些私有接口 → `git checkout <新 sha>`
  → 回主仓库跑 `pytest`（**`tests/test_upstream_contract.py` 会先告诉你接口动了没有**）
  与真机对撞脚本，再 `git add wechatauto-replica` 提交 pin。

## 上游库的关键陷阱（已踩，务必遵守）
- **`real_sender_id` 的 id 空间是每个消息分片库各自的 `Name2Id` 表**，
  上游 1.2.5.1 及更早拿 `message_resource.db` 的全局 `SenderName2Id` 去查，
  会解析出毫不相干的人（甚至群外成员）。正文无 `wxid_x:` 前缀的行（自己发的消息、
  emoji、图片、系统消息）全部会错。**这条现在由上游承担**：1.2.6.1（issue #34）
  起按消息所在分片解析，全局 `_sender_id_index()` 已删除，我们曾经兜过底的
  `sender_alias_map` + `IsolatedDB._fix_senders` 已随之删掉（2026-10-14，用户定）。
  **所以 pin 不能往回退** —— 退回 1.2.5.1 气泡上就是错的人，
  `tests/test_wechat.py:test_upstream_pin_resolves_senders_per_shard` 认接口守住这件事
  （接口面整体由 `tests/test_upstream_contract.py` 兜），
  升级前还要跑 `data/_upgrade_check.py` 拿真机存量复核。
- `sender_id == 2` 在上游 1.2.5.1 被当作"自己"，但分片 `Name2Id` 的 rowid 2
  是别人；以分片表为准。1.2.6.1 已去掉该特判（本机实测自己在 `message_0` 是 rowid 5）。
- 解密缓存固定在 `data/wechat/cache/<账号指纹>/`，与其它工程隔离；
  改 WAL 合并/解密逻辑要同时 bump `wechatauto.STAMP_VERSION`。
- 消息表跨分片，`local_id` **非全局唯一**，去重/定位一律用 `(sort_seq, local_id)`。
  上游 `download_image(user, local_id)` 就是因此会挑错行；1.2.6 起消息行多带一个
  `shard`（分片库文件名），`download_*/image_status/get_message_row` 都接受
  `shard=` 来锁定唯一一行。我们不调 `download_image`（凭 `media_md5` 自己定位文件），
  `shard` 暂未进 metadata。

## 图片附件的关键事实（`niannian/media.py`）
- **磁盘文件名那个 32 位哈希只在消息行的 `packed_info_data` 里**；正文 XML 的
  `md5` 属性是另一套值，拿它去 `msg/attach/<md5(会话ID)>/` 找文件一条也找不到。
  上游 `get_messages` 不返回 `packed_info`，所以按 `(sort_seq, local_id)` 批量补查
  （`wechat_db.py:media_hash_map`），存进 metadata 的 `media_md5`。
- **`kind`/`media_md5`/`local_file`/`tier` 在 `messages` 上有投影列**，采集侧"这轮该补
  哪几行"的判定**只看列**（`storage._image_worklist`）。列只是 metadata 的投影，不是
  第二份事实：只有 `ingest` 与 `update_metadata` 两个入口会经 `media_projection` 写它，
  新增写路径必须走这两个之一，否则列与 metadata 会漂。
  **不要退回 `instr(metadata, '"kind": "image"')` 那种文本匹配**：它走不了索引
  （每轮全表扫 + 临时 B 树排序，两万行实测 6.5ms/8.3ms 每轮各一次），还绑死
  `json.dumps` 的分隔符形状。两条工作清单各自有一个部分索引对上（排序就是索引序），
  改库结构前先看 `SCHEMA` 里那段注释，别新建第三个能全表扫的每轮查询。
- 本机三档：`_h.dat` 原图（点过"查看原图"才有）、`.dat` 微信下发那份、`_t.dat`
  预览图。**没在微信里点开过的图片只有 `_t.dat`**，而它能单字节 XOR 解出来——
  所以预览图必须当可用素材，不能判成"本机没有"（实测 23 张里 8 张只有预览图）。
- **采集那一刻文件可能还没落盘**（微信翻到才下预览图）。只提取一次就永久缺失，
  所以凭 `media_md5` 的补链接是**每轮都做**的后台活（`wechat.py:_catchup_images`，
  只认关注会话）；API 侧只读库里已有的 `local_file`，**读取时不再解密图片**。
- **"有链接"也不等于拿到最好的那份**：链接一旦写进库就永久不再回头看，所以补链接之外
  还有第二种补——重扫升级（`wechat.py:_upgrade_images` + `storage.stale_tier_images`），
  每轮真扫 10 行「有链接但 `tier` 不是 `original`」的行，档位变高就改写链接并
  `ImageStore.discard()` 删掉替下来的旧图。**终点是 `original`，不是 `mid`**：
  `.dat` 可能是完整图也可能本身就是预览版，本机判不出来（上游实测结论），所以停在
  `mid` 的行还要留着升到原图的机会。**候选窗口（200）与每轮真扫（10）是两个数，别合成
  一个**：清单新→旧，而用户从没点开过的行永远升不上去，不这样最前面那批会每轮霸占名额、
  后面的永远排不到；"扫过没升上"的行记进**内存**冷却表（10 分钟），不要写进库。
  大那份文件只能由**用户在微信里点开**才会落盘——上游 `download_image_original()` 是驱动
  界面去下载的路，与"采集只读"的约定冲突，**不要用**。
- 库里**只存相对链接** `<账号指纹>/<会话>_<sort_seq>[_档位].<ext>`，不存二进制；
  读取要经 `safe_media_path` 收口（最多两段、拒绝对称路径/盘符/UNC/`..`）。
- 上游 `MediaDownloader` 的 `_image_files` / `decrypt_image` / `_write_decrypted`
  是这里复用的三个私有方法（定位、解密、按文件头判格式落盘 + wxgf→jpg）；
  弄钥匙再加四个：`_derive_cfg_key` / `_load_persisted_key` /
  `_scan_aes_key(monitor=False)` / `_persist_key`，注入槽位是 `_image_key`。


## 钥匙与线程：谁干什么（"一直转圈"那轮的定论）
- **图片 AES 钥匙和数据库钥匙不是一回事**：后者有 `keys.json` 落盘副本，微信没开也能读文字；
  前者要靠微信运行时的 `cfg_dword` 现推（`MD5(str(cfg)+wxid)[:16]`），微信没开时上游
  `extract_master_key()` 只会便宜地返回 None。**服务先启动、微信后启动 → 文字可用、图片没钥匙。**
- 上游"没钥匙"的分支不是报错，是**去整块扫 Weixin.exe 内存**（`_resolve_aes_key` →
  `_scan_aes_key(monitor=True)`：扫不到每 2 秒重扫、最长 120 秒）。Python 一个进程只有
  一条线路能真正干活（GIL），它在谁的线路上扫内存，谁的接口就跟着饿死——当年
  `api.py:_link_media` 在请求线程里解密，于是消息列表**和设置页**一起转圈。
- **所以：请求线程一律纯读**（只读自己的库，毫秒级返回）；`ImageStore` 有钥匙闸门，
  `key_ready` 为假时 `attach()` 直接返回 None，连 `decrypt_image` 都不进，任何调用方
  都不可能顺手触发扫描。补钥匙只允许采集线程调 `prepare_key()`，且**必须 `monitor=False`**
  （只扫一次，扫到就落盘 `image_keys.json`，之后免费派生）。cfg 现派生出来的那份**也要落盘**
  （上游只在扫描路径落盘），否则下次服务在微信没开时启动又要等一轮。
- 采集线程每轮多做三件：`_catchup_images()`（关注会话里缺链接的图片行，限量 30、新→旧）、
  `_upgrade_images()`（有链接但还没到原图的行，真扫 10 行 + 内存冷却，见上面那条图片事实）、
  `_ensure_media_key()`（缺钥匙时补，**两把时间要分开**：`wechat_running()` 只是查进程表，
  5 秒问一次；取 cfg / 扫内存是几十秒级重活，120 秒冷却。**别把两者共用一个冷却**——
  冷却被"微信还没开"那次白问占掉，用户打开微信后还要再等满两分钟才见到图，实测踩过）。
- **一个进程里只能有一个读取器**：缺 `cfg_dword` 时只调 `self._wx.extract_master_key()`
  把编号填回**同一个对象**，**不要**再 `open_isolated_db()` 重开一个。上游的建库锁登记表
  是**按实例**存的（`self._build_locks`），两个实例会同时重建同一份解密缓存，Windows 上
  `os.replace(tmp → dst)` 撞上没关的只读句柄就是 `[WinError 5] 拒绝访问`（实测踩过）。
  同理，跑 `scripts/capture_fixtures.py` 前必须停服务。
- 轮次失败要**连续两轮**才在界面上报"异常"：微信 checkpoint 时上游重建缓存会短暂撞车，
  下一轮自愈，一次失败就亮红条是噪声。没自愈的话同步时间会停着不动，那本身就是信号。
- 连接失败**不退出线程**：状态 `waiting_wechat`，按 10 秒冷却自己重试，用户后开微信不需要
  重启服务。未连接时命令必须**立刻**抛中文错误（`_request` 判 `self._wx is None`）——
  只判"线程活着"不够，线程活着但没连上时命令进队列没人执行，网页会干等 90 秒超时。
  `catalog()` 的回落判据同样是"连着微信"而不是"线程活着"。
- 补图查询 `Database.pending_images()` / `stale_tier_images()` 只看**投影列**，既不
  `instr` 匹配文本也不用 `json_extract`：前者走不了索引（每轮全表扫），后者会被一行
  坏 JSON 抛错打死整轮。metadata 仍要取回来（补完链接要整块写回），坏行交回来是
  `None`、由采集侧跳过——**不能**因为一条写坏的行让这一轮什么都不干。


## 动画表情：只留 `[动画表情]` 占位（用户 2026-10-15 定）
- `describe._emoji` **只返回 `{"kind": "emoji"}`**：正文的 `md5/len/width/height/type`
  一律不解析，metadata 里不留任何可供下游取图的字段。要恢复动图实现看
  `archive/media-pipeline`（15b0080 那一版：按 `cdnurl` 取回明文 + 按 md5 落盘）。
- **别再试本机那几份**：`business/emoticon/{Persist,Thumb}` 与
  `cache/<月>/Emoticon/<前2hex>/<md5>`（文件名就是正文 md5）长度全是 16 的倍数、与
  「明文+PKCS7」严丝合缝，但 `emoticon.db::kNonStoreEmoticonTable.aes_key` 与正文
  `aeskey` 的 12 种取法 × ECB/CBC/CTR/CFB/OFB × 4 种 IV × 偏移 0/16 **全部对不上首块**，
  图片那把 AES 密钥与单字节 XOR 也否掉。这轮结论仍然成立：没破。
- 采集因此回到**纯本地读**：表情行不发任何网络请求（`约定` 里那条"唯一例外"已作废）。
- 媒体目录 `data/wechat/media/<账号指纹>/emoji_*`（16 个）是上一轮取回的，现在没有
  任何行引用它，用户定**保留不动**，不是待清理项。


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
- 备注 > 群名片 > 昵称这一**定序**只有一处实现：`contacts.display_name()`，
  `IsolatedDB.display_names(user)` 调它；改优先级只改那一个函数。
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
- **取一屏只有一个出口** `sessions.tsx:fetchPage`：翻页、跳引用、跳搜索结果都走它，
  共用同一条 abort 链与同一个请求序号。跳转自己发请求的话，"点引用→立刻切会话"
  会让上一个会话那一屏盖到刚选中的会话上（实测过，把请求拖慢 4 秒可稳定复现）。
- 往上翻历史**没有手动按钮**：滚到接近顶部自动取更早的一屏（`sessions.tsx:loadOlder`）。
  三件事必须同时成立，少一件就是回归：补来的内容从上方长高要**锚定 scrollTop**；
  一屏占不满时**没有触发滚动的机会**，所以要"占不满就继续补"；滚动一次手势会连发几十次
  事件，所以除 `loading`/`hasMore` 还要一把自己的锁。
- `Dialog` 的 `closable`（默认 `true`）：图片弹窗不要右上角那颗 ×，但**不能直接删组件里的
  按钮**，否则所有弹窗都没了。关掉后仍可点遮罩与按 Esc。
- 会话内搜索**只搜该会话已入库的消息正文**（`content LIKE`，关键词里的 `%` `_` `\` 必须
  转义），没导入的历史搜不到——这句要写在结果列表上，不能让人误判成"没有"。点结果跳原文
  复用引用跳转那套（`before_seq = 目标+1, before_id=1` + 描边），**不要**为跳转再加接口。
  `hits === null` 才是"不在搜索态"（空数组是"搜了没搜到"）；换会话必须退出搜索态。

## 消息顺序与重新导入（易踩）
- **翻页排序键必须是 `sort_seq`，不能是自增 `id`**。id 只代表入库先后；会话一旦
  被"重新导入"过，更旧的消息会带着**更大**的 id 插进来，按 id 排就翻了。
  游标是 `(before_seq, before_id)` 复合，索引 `(session_id, sort_seq, id)`。
- 同 `sort_seq` 并列用 id 稳定破并列（跨分片 `local_id` 本身重复，没有更可靠的列），
  即"同秒内按入库先后"——这是已知且接受的行为，`test_storage` 里固定住了。
- 重新导入 = 记下该会话条数 → `clear_watermark` → 复用 `_import_recent`。
  **不删消息**，重复行靠 `source_id` UNIQUE 跳过；水位取 `max`，所以回填更旧的
  消息不会把增量轮询游标拉回去。
- 导入条数是**按会话存**的（`sessions.history_limit`，0=跟随全局），刻意与全局
  `config.history_limit` 解耦：改一个会话不能影响其它会话。
- `INSERT OR IGNORE` 意味着已存在的行永不更新：改了入库时的解析/显示逻辑，
  必须清库重采才对存量生效。

## 设置页与配置（`config.py` / `api.py`）
- 设置页**只开放三项**：`history_limit`、`poll_seconds`、`port`。`host` 刻意不放
  （改成非 127.0.0.1 会把本机微信数据暴露到局域网，且改错会把自己关在门外）；
  数据目录/账号仍可手工写在 `config.json`。
- **读宽容、写严格**：`load_config()` 遇到坏值退回默认或夹到边界，保证服务起得来；
  `validate()` 只在保存时用，越界直接抛中文错误、不落盘。别把两者合并。
- `save_config()` 只更新提交的键并保留文件里其它键，先写 `.tmp` 再 `os.replace`。
- `Collector` 持有 settings 的**副本**：改 `app.state.settings` 传不进采集器，
  必须走 `Collector.update_settings()`（只接受可热更的键）。哪些键要重启**只有
  `config.RESTART_KEYS` 一份**，界面读 `GET /api/settings` 返回的 `restart_required`，
  不要在 `settings.tsx` 里再写死一个布尔。`port` 属启动期参数，
  界面必须标"需重启"，不能假装已应用。
- 保存端口前用 `port_busy()` 试绑定预检；**不要**给它加 `SO_REUSEADDR`
  （Windows 下它允许重复绑定监听中的端口，探测会假成功）。

## 约定
- 注释与 docstring 用**中文**（公开 API 名用英文），只在"为什么"不明显处写注释。
- 采集**只读**：不驱动 GUI、不发消息、不触发微信下载附件。
- 非纯文本正文由 `niannian/describe.py` 解析：图片/文件/视频/动画表情/小程序/链接/
  卡片/转账/合并转发/拍一拍/引用/系统消息出中文摘要，结构化字段进 metadata 的
  `attachment` 键（**沿用这个名字不改动**，图片管线与前端都认它，改名会让存量图片行
  退化成纯文本气泡）。认不出的（本机暂无样本的语音/位置等）保持上游文本原样入库，
  **不猜结构**。摘要规则：方括号里只放类型标签，可读信息（文件名/来源/时长/金额）
  都在括号外；微信自己已写成整句的（拍一拍、撤回、群动态）直接用那句话。
- **转账是唯一的例外，方向和对象写在标签前**（用户定的措辞）：
  `向影 [转账] ￥10.00` / `收到(=°Д°=) [转账] ￥10.00`。同一笔钱微信会发**两条**卡片
  （`paysubtype` 1 发起、3 已到账），两条的 `payer_username`/`receiver_username`
  完全一样，**只有发送者换了人**，所以方向只能拿发送者去比（见
  `normalize.py:transfer_target`）。认不出对方显示名时什么都不加——宁可只显示金额，
  也不把 `wxid_` 贴进气泡。`des` 那句"收到转账X元"是**收款方视角的固定话术**，
  两条都一样，不能当摘要用。

## 引用消息与跳转（`describe.py` + `wechat_db.py` + `api.py`）
- **不存被引全文，只存定位**（用户定的、与微信一致）：`quoted={svrid, author, name,
  at, type, label, sort_seq}`。`label` 是那行小字，实测被引原文最长 2311 字，全文一律
  不入库。引用行的**正文只放回复那句话**——引用是上下文，不该冒充本句（将来喂模型同理）。
- **定位键是 `sort_seq`，不是 svrid**：`sort_seq` 在会话内唯一（实测）且正是翻页游标，
  前端 `before_seq = 目标+1, before_id = 1` 就能取到包含目标的那一屏，**不需要新接口**。
  `before_id` 不能给 0（API 校验 `ge=1`）。
- `quoted.sort_seq` 由采集线程按 `svrid` 反查**微信分片库**得到（`wechat_db.py:server_seq_map`），
  不是反查我们自己的库：原文还在导入窗口外时也查得到，等它被导入后同一个 sort_seq
  自然对上。查不到留 0 → 前端显示成不可点 + 「原文未导入」。
- **`refermsg` 的 `fromusr` 语义不统一**：实测 27 条里 17 条它是会话 ID，另外 5 条它是
  被引作者本人的 wxid（其中 3 条 `chatusr` 为空）。所以作者只认 `chatusr`，空了才回落
  `fromusr`；**不能拿 `fromusr` 判断原文在哪个会话**。实测也不存在真正的跨会话引用
  （微信的"引用"只能长按当前会话某条消息触发）。
- 被引内容是**整段转义 XML** 时（引用卡片/图片，`<msg><appmsg…`）必须递归
  `describe`+`summarize` 换成摘要；递归也认不出时剥标签取纯文本，**绝不把 XML 贴进气泡**。
- 原文被撤回后**照常可跳**（用户明确不要"已撤回"标记）：我们不删行，跳过去仍能看到内容。
