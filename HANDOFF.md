# HANDOFF.md — 交接

## 当前状态
**图片解密与"弄钥匙"全部收进采集线程，网页请求退回纯读**（2026-10-15 修转圈 bug）：
服务先启动、微信后启动时，读取消息会连带触发上游"整块扫 Weixin.exe 内存找图片 AES 钥匙"
（最长 120 秒的 monitor 循环），GIL 让它把设置页也一起饿死。现在 `ImageStore` 有
**钥匙闸门**（没钥匙一律不解密），补钥匙/补链接都由采集线程按冷却做；连接失败不再退出
线程，新增 `waiting_wechat` 状态并每 10 秒自己重试，**微信后启动不需要重启服务**。
动画表情已彻底退回 `[动画表情]` 占位（用户 2026-10-15 定）：取图那一轮（`15b0080`）
不在 master 上，`describe._emoji` 也只回 `{"kind": "emoji"}`，正文的 `md5`/宽高/`cdnurl`
一概不解析，采集重新变成**纯本地读**（表情行不发 HTTP）。那一轮连同之后的 wxgf/贴纸/
撤回工作完整留在 `archive/media-pipeline`（`d2f2a56`），要恢复动图从那儿拿。
存量**不需要清库重采**：库里 19 条表情行的 `attachment` 本来就全是 `{"kind":"emoji"}`、
正文全是 `[动画表情]`，与现码逐字一致；媒体目录 16 个 `emoji_*` 按用户定**保留不动**。
子模块 `wechatauto-replica` 升到 **1.2.6.1（62503d7）**，并**删掉了本地的发送者兜底补丁**
（`sender_alias_map` + `IsolatedDB._fix_senders`）：上游把我们记在 AGENTS.md 的两个坑
（发送者按全局 `SenderName2Id` 解析、跨分片 `local_id` 不可寻址）在源头修掉了，解析
现在完全由上游承担，**pin 因此成了正确性的一部分**，由
`test_upstream_pin_resolves_senders_per_shard` 与 `data/_upgrade_check.py` 守住。
未清库重建（删的是与上游输出逐条相同的路径，存量 152/152 复核一致）。此前阶段：库里
正文 XML 归零（476 条消息、11 类非文本出中文摘要），引用行只存 `quoted.sort_seq`、
点击滚到原文并描边；往上翻历史改成滚到顶部自动加载；图片弹窗去掉 ×（遮罩与 Esc 仍可
关）；图片解密、发送者显示名、每会话独立重新导入、设置页三项、真机 fixture 离线回放
测试均已在库上。`pytest` 92 passed /
6 skipped（skip 的是回放测试：本机 `data/fixtures/capture.db` 不在，按"缺 fixture 就 skip"）；
服务 `python -m niannian` → http://127.0.0.1:8765。

## 最近一轮：图片解密下放轮询 + 微信后启动自动接上
症状是"微信没开时开服务，之后开微信，消息列表和设置页一起一直转圈"。根因不在加载速度，
在**谁去弄钥匙**：数据库钥匙有 `keys.json` 落盘副本所以文字照读，而图片 AES 钥匙要靠微信
运行时的 `cfg_dword` 现推；服务先启动时推不出、`api.py:_link_media` 又在请求线路上调
`decrypt_image`，上游于是走 `_scan_aes_key(monitor=True)` 去整块扫微信进程内存（扫不到每
2 秒重扫、最长 120 秒），一条线路把 GIL 占满，连纯读自己库的 `/api/settings` 都排队。

三刀：① 消息列表改纯读，缺链接的图片行原样返回（气泡仍是"本机暂无…"），补链接进
`Collector._catchup_images()`（每轮限量 30 行、只认关注会话、新→旧）；② 连接失败不退出
线程，置 `waiting_wechat` 并按 10 秒冷却重试，未连接时命令**立刻**抛中文错误（原来只判
"线程活着"，线程活着但没连上时命令进队列没人执行，网页干等 90 秒），`catalog()` 回落判据
改成"连着微信"，前端加"等待微信启动"横条；③ `ImageStore` 加 `key_ready` 闸门 +
`prepare_key()`（cfg 派生 → `image_keys.json` → 只扫一次且 `monitor=False`，扫到就落盘），
`Collector._ensure_media_key()` 按 120 秒冷却调用它，`cfg_dword` 缺失时改成重连一次而不是
白扫内存。

存量不用清库重采：入库字段形状没动，只是换谁来补。守门测试三条：假采集器把 `media`
做成"一读就炸"跑消息列表接口、闸门关闭时 `decrypt`/`scan` 零调用、扫内存必须
`monitor=False` 且只扫一次；另加"同一条线程先失败后连上"证明不用重启服务。真机在微信
未开状态下复核：三个接口 2–14 ms、消息列表 4 ms、待补那 1 行保持待补且没触发任何扫描。
**还没真机验证的一条**：打开微信后钥匙自动补齐（要用户实际操作微信）。

## 上一轮：表情退回占位 + 历史按用户口径重做
`git rebase --onto da016d9 15b0080 master` —— 回退到 `da016d9`、跳过取图那一轮（`15b0080`）、
把升级 pin 那一轮重放成 `135cd82`。冲突只有本文件与 `docs/REFACTOR-LOG.md`（表情段在重放
侧变成"我们这删了、对方又在其后追加"），按"删表情段、留升级段"手工合，并把两处 `pytest`
计数改成与去表情后的树一致。再补一刀：`describe._emoji` 从 6 字段削成只回 kind，
`test_emoji_and_video_fields` 拆成"表情只占位"与"视频字段"两条。`summarize` 本来就不给
emoji 走 `tail` 分支，摘要不受字段减少影响。
验证：`pytest` 78 项（77 passed / 1 skipped）、`npm run build` 通过、守门测试仍 1 passed、
回放里"表情正文必须是 `[动画表情]`"那条不变式跑真 fixture 通过、全仓 grep 无
`attach_emoji` / `emoji_allowed` / `cdnurl` / `EMOJI_READ` 残留。

## 上一轮：子模块升级 1.2.5.1 → 1.2.6.1，并删掉本地发送者补丁
上游 6 个提交做了两件事：**1.2.6（issue #20）** 消息行新增 `shard`（分片库文件名），
`get_message_row` / `download_*` / `image_status` 都能按 `shard=` 锁定唯一一行，
分片读失败不再静默当成"这片没消息"；**1.2.6.1（issue #34）** `real_sender_id` 改按
消息所在分片的 `Name2Id` 解析，全局 `_sender_id_index()` 删掉，`sender_id == 2` 即
"自己"的特判去掉。

升级动作：`git fetch` → 逐条看 `fbfb026..origin/main` 改了哪些私有接口 → checkout
`62503d7` → 回主仓库验证。**先核对接口再 bump**：我们在用的
`_msg_conns` / `_contact_conn` / `nickname_map` / `MediaDownloader._image_files` 等
全部还在，被删的三个私有方法一处都没调；`STAMP_VERSION` 没动所以缓存不作废。

真机对撞（停服独占解密缓存）：上游原生解析 vs 我们的 `_fix_senders`，3 会话
758 行 758/758 一致、0 缺行 → 用户拍板"删掉把解析交回上游"。删的是
`sender_alias_map()`、`_fix_senders()` 和 `get_messages` / `get_new_messages` 两个覆写
（`_each_shard` 留下，`media_hashes` / `quote_seqs` 还在用；`normalize_message` 不动，
它读的还是 `sender_username`，只是填这个字段的人换了）。**没有清库重采**：删的是与
上游输出完全相同的兜底路径，落库内容不变。

本地没了第二层，所以补一条守门测试 `test_upstream_pin_resolves_senders_per_shard`
（**认接口不认版本号**：`_sender_id_index` 必须不存在、`sender_username` 与
`_name2id_map` 必须在），并把 `data/_upgrade_check.py` 改成"当前代码路径重读 vs 库里
已存"的存量复核脚本。

验证：`pytest` 77（删 2 加 1）；真机存量 152/152 逐行一致（`sender_key` /
`sender_name` / `is_self` / `content` / `message_type` 五个字段都比）；服务重启
connected、152 条一条没掉、`imported: 0`；私聊 52 行仍全署名"我"（1.2.5.1 正是
在这类行上出错），群聊 100 行署名分布在真实成员内；浏览器无报错。

顺带发现一个**与升级无关**的既有问题：5 条图片行 `local_file` 落成 `.wxgf`
直接进 `<img>` 渲染不出来（那 5 行 21:53 入库，早于 22:07 的 pin bump），
是 wxgf 档位没转 jpg，另起一轮处理。

## 上一轮：两个前端小改进
- **"加载更早消息"按钮删了，改成滚到接近顶部自动取一屏**。三个坑必须一起填，少一个就是
  回归：补来的内容从上方长高会顶走正在读的位置（要锚定 `scrollTop`）；一屏占不满时用户
  根本没有触发滚动的机会（要"占不满就继续补"）；一次滚动手势连发几十次事件（要自己的锁）。
- **图片弹窗右上角的 × 去掉**：`Dialog` 加了 `closable` prop（默认 `true`），只在图片弹窗
  传 `false`——直接删组件里那颗按钮会把所有弹窗的关闭键都干掉。点遮罩与 Esc 仍然能关。

实测：按钮消失；滚到顶自动从 40 条补到 80 条、原本在顶部那条仍停在面板顶 16px 处；
一路补到该会话最旧 275 条后不再触发（等 3 秒与重复滚顶都稳定）；图片弹窗无 ×、
遮罩与派发的 `keydown` 都能关，其它弹窗的 × 仍在。`press_key` 打不进内置浏览器的隐藏
标签（与截图同一限制），所以真键盘 Esc 只验证到监听器层面。

## 上一轮：引用消息（type=57）→ 存定位、点击跳原文
用户要求"和微信做得差不多"。动手前先做 Assumption Analysis，**把方案改小了两处**：

- **不加 `server_id` 列**：`sort_seq` 会话内唯一且正是翻页游标，采集时把 `svrid`
  反查成 `quoted.sort_seq` 就够。反查的是微信分片库，所以原文还在导入窗口外也查得到
  ——比原方案更鲁棒（原方案那 3 条窗口外的会永久跳不到）。
- **不加 `around` 接口**：`before_seq = 目标+1, before_id = 1` 就能取到含目标那一屏
  （`before_id=0` 会被 `ge=1` 挡成 422，踩过）。

撤回两个先前的错误说法：**没有跨会话引用**（0 条），真实原因是 `refermsg/fromusr`
语义不统一（17 条是会话 ID、5 条是被引作者本人），所以作者只认 `chatusr`。
另外补验了 `svrid` 的**唯一性**（第一次探测用 `limit 1` 且命中就 break，等于没验）：
27/27 只命中 1 行，分片库 7948 行里 `server_id` 无 0 无 NULL。

语义按用户定的：正文只放回复那句话（引用是上下文不冒充本句）；被引全文不入库，
小字截 40 字；引用卡片/图片时 `refermsg/content` 是整段转义 XML，**递归换成摘要**
（4/4 认出），递归失败就剥标签取纯文本；**原文被撤回照常可跳**，不标"已撤回"；
目标没导入则显示同一行小字但不可点，尾注「原文未导入」。

测试抓到两个自己的 bug：`quote_name` 的兜底条件该判"会话是不是群"而不是"作者是不是
群 ID"（否则群里认不出的人被贴成群名）；回放测试判断引用前要先 `split_sender` 剥前缀。

验证：pytest 78 项通过（新增 8 项）；删库重建后 476 条正文 XML 归零、26 条引用行
全有定位、`label` 无一含 `<`；API 侧 17 条可点引用逐个回查（17/17 目标行命中、
17/17 作者 wxid 与目标 `sender_key` 一致）；浏览器实测点击后目标行描边、面板滚到
533、高亮那条正文正是被引原句。截图仍受内置浏览器隐藏标签限制。
## 上一轮：非文本消息格式化（新增 `niannian/describe.py`）
先把家底摸清了再动手：库里 659 条有 **156 条正文仍是 XML**，逐条分类结果写在
`docs/REFACTOR-LOG.md` 的表里。最要紧的一条是上游标签「文件/链接/卡片」把 8 种
东西压成一个名字，真区分只在 `<appmsg><type>`，所以解析只能落在 XML 层。

- 已格式化：图片/文件/视频/动画表情/小程序/图文链接/卡片兜底/转账/合并转发/拍一拍/
  系统消息（撤回 + 改群名这类 `sysmsgtemplate`）。**引用消息（type=57，36 条）按用户
  要求本轮不动**，正文仍是 XML。
- 顺带修掉一个归属 bug：拍一拍没有群前缀、根 `fromusername` 也是空的，动手的人只在
  `patinfo/fromusername` 里，之前一律署名「系统消息」。
- 转账按用户要求**全字段进 metadata**（含 transferid/单号/双方 wxid），摘要只出金额。
  上游把 `transcationid` 拼错了，读取照原拼写。
- 描述仍放 metadata 的 `attachment` 键（**不改名**，理由见 AGENTS.md）；
  `_attach_media` 与 `api._link_media` 的过滤条件相应改成 `kind == "image"`，
  否则转账/文件会去抢图片链接。
- 认不出的一律返回 None、正文原样入库：语音/位置这批数据里没有样本，就一列没写，
  不猜结构。

验证：pytest 61 项通过（新增 10 项，10 类形状各有 fixture）；拿真机库那 156 条 XML
全量干跑，120 条出摘要、剩 36 条正是刻意不动的引用消息，十类实际输出逐条看过。
**存量已按用户选择清库重采**（旧库归档在 `data/backup-pre-format/`，关注清单
`data/follow-list.json`）：459 条里只剩 26 条 XML（5.7%，全是引用消息），
图片 45/45 仍有本机链接，拍一拍署名实测已变成「影」。

同轮收尾两处：气泡上的类型徽标改成 `api.type_label()` **读取时按 `attachment.kind`
现算**（复用 `describe.KIND_LABEL`，不入库所以存量行不用重采就能改过来，没有 kind 的
回落上游标签）；左下角那句阶段号换成「只读采集本机微信记录，不发消息、不改动微信数据」。

追加：转账摘要带上目标对象，措辞由用户定 —— `向影 [转账] ￥10.00`（发送者付钱）、
`收到(=°Д°=) [转账] ￥10.00`（发送者收钱）。方向只能拿**发送者**去比
`payer_username`/`receiver_username`：同一笔钱微信发两条卡片，双方 wxid 完全一样，
只有 `paysubtype` 和发送者变了。认不出对方显示名就不加前缀，不贴 wxid。
存量 10 条已删掉重导（备份 `data/backup-pre-transfer/`）。细节与原始 XML 见
`docs/REFACTOR-LOG.md` 与 `data/_transfer_two.txt`。

再追加：`scripts/capture_fixtures.py` 把关注会话各最多 100 条**原始正文**导成
`data/fixtures/capture.db`（含每会话的显示名表，跑前先停服务），
`tests/test_capture_replay.py` 拿它离线回放 5 项不变式（只有引用消息允许留 XML、
摘要不重复套方括号、转账摘要里绝不出现 `wxid_` …），fixture 缺失就 skip。
pytest 现在 70 项。

**授权更新**：涉及入库解析/库结构的改动，直接删库重建让改动对存量生效，不用再问
（已写进 AGENTS.md 工作方式）。

## 上一轮：设置页（只开放三项）
用户从候选里只挑 `history_limit` / `poll_seconds` / `port`。`host` 刻意不放
（暴露局域网 + 改错锁死自己），数据目录/账号仍可手工写文件。

- **生效方式混合且如实标注**：`Collector` 拿的是 settings 的副本，所以前两项
  写盘后经 `Collector.update_settings()` 推进采集器立即生效；`port` 只能重启，
  界面标"需重启"并额外给一条"重启后用新端口访问"的横幅，不假装已应用。
- **读宽容、写严格**：`load_config()` 坏值退默认保证起得来；新增 `validate()`
  只在保存时用，越界抛中文错误、不落盘。
- `save_config()` 只更新提交的键、保留文件里其它键，`.tmp` + `os.replace` 原子写。
- 保存端口前 `port_busy()` 试绑定预检（故意不设 `SO_REUSEADDR`，Windows 下会假成功）。
- `Badge` 加 `tone` 档：同 `Dialog` 的道理，不能用冲突类压基类。
- 顺带修一个切页丢状态的问题：选中会话从 `SessionsPage` 提升到 `App`。

## 上一轮：修复微信发送者名字解析错误
根因：微信 4.x 的 `Msg_*.real_sender_id` 指向**每个消息分片库自己的 `Name2Id`**，
上游 `wechatauto` 却拿 `message_resource.db` 的全局 `SenderName2Id` 解析，两套 id
空间互不相通。带 `wxid_x:` 前缀的群聊文本被前缀救回，无前缀的行（自己发的消息、
emoji、图片、系统消息）全部落到错映射 → 群里冒出非群成员署名、私聊 `is_self` 恒 0。
修法：`IsolatedDB` 按分片还原 `sender_username`，`normalize_message` 改为分片解析
优先、前缀兜底。原项目有同一隐患，只是它只导入过"全员带前缀"的通知群没暴露。

## 本轮：图片显示
三条实测事实（细节见 `docs/REFACTOR-LOG.md` 与 `AGENTS.md`）：
1. 磁盘文件名哈希**只在 `packed_info_data`** 里，XML 的 `md5` 属性是另一套值。
2. 没在微信里点开过的图片只有 `_t.dat` 预览图，**能解**（单字节 XOR）：23 张里
   8 张只有预览图。上游 `tier='best'` 本来就会回落到预览图档。
3. 原版的真正缺口是**时机**：提取只挂在采集那一刻，而微信的预览图是翻到才落盘
   ——它库里 28 条图片只有 8 条有链接，今天对这 32 条重跑却是 32/32 全可解。

改动：新增 `niannian/media.py`（`parse_image` / `hash_from_blob` / `pick_tier` /
`ImageStore` / `safe_media_path`），采集线程补 `media_md5` + `local_file`，
`api.py` 加 `GET /media/{name}` 与**读取时凭哈希回填**的 `_link_media`，
前端 `mediaUrl()` + 图片气泡与放大弹窗（标注原图/本机图/预览图）。

验证：清库重采 200 条、23 张图片全部拿到链接（9 原图 / 6 本机图 / 8 预览图）；
预览图 URL 返回 200 `image/jpeg`；五种越界路径全部 404；抹掉链接后重新请求能
自动补回并持久化；浏览器 9 个图片气泡 8 个渲染、空态文案正确、弹窗 Esc/遮罩
均可关。截图受限于内置浏览器隐藏标签（Chrome 不加载 hidden 页的懒加载图）。

## 本轮：读路径按时间排序 + 每会话独立的重新导入
用户要"导入更早历史"的入口。我先给的完整方案（上游 keyset 翻页 + 新命令 +
新字段 + 到底判断 + 防连点）被明确否掉：**非刚需，别把系统搞复杂**。最终只做
两件便宜的，明确不做无限往回翻页。

关键发现是：真正必须做的不是"取更旧的消息"，而是**修排序**。原来翻页按自增 id，
只因消息永远"新的后入库"才碰巧等于时间顺序；重新导入会把更旧的消息以更大的 id
插进来，顺序立刻翻。所以：

- **A**：`messages_page` 排序键换成 `(sort_seq DESC, id DESC)`，游标换成
  `(before_seq, before_id)`，索引换成 `(session_id, sort_seq, id)`；同秒并列用
  id 稳定破并列（跨分片 local_id 本身重复，接受"同秒按入库先后"）。
- **B**：`sessions.history_limit` 新列（0=跟随全局），**按会话存**，与全局
  `config.history_limit` 解耦。`Collector.reimport` 走已有命令队列进采集线程，
  清水位后复用现成的 `_import_recent`——不删消息，重复靠 `source_id` UNIQUE 跳过。
  前端控件在会话标题栏（与"取消关注"同一栏），条数输入默认值取该会话自己存的条数。

验证：真机对宿舍群重新导入 400 → "读到 259 / 新增 159"（该群总共 259 条，已探到
最旧）；逐页翻到底 7 页 259 条，无重复、时间倒序违例 0、与库内计数一致；三个会话
只有被操作的那个 `history_limit=500`，另两个仍 0；水位停在最新 sort_seq，没被
回填拉回；回填消息的图片自动补链接（39 → 45 张）。pytest 34 项通过。

## 明确没做（按用户要求保持简单）
- 无限往回翻页（需上游 `WHERE sort_seq < ?` 的 keyset 查询 + 新命令 + "已探到
  最旧"标记 + 防连点）。等 1000 条真不够用再说。
- 因此**单次重新导入上限仍是 1000 条**（`clamp_history_limit` 的天花板）。

## 待用户决定
- **`.wxgf` 图片气泡是破图**（既有问题，非升级引起）：5 条图片行的 `local_file` 落成
  `.wxgf`，`/media/…wxgf` 虽 200 但浏览器不认。要不要单起一轮把 wxgf 档转成 jpg 再落盘
  （涉及存量 → 按惯例清库重采）。
- **文件与视频挂本机链接**（数据已探明，做与不做等你定）：21 条文件在 `msg/file/`
  **21/21 命中同名文件**；`msg/video/` 有 24 个 mp4 + 27 张 `_thumb.jpg`，但要用
  `packed_info_data` 里的 id 定位（那条视频用 md5/newmd5 都没命中）。
- **合并转发（聊天记录）要不要给"点开看"入口**：`recorditem` 已解析成 `items`
  （每条含作者/时间/正文）存在 metadata 里，但界面还没有展开的入口。
- **跳过去之后要不要能跳回来**：现在点引用会把当前这一屏换掉，原浏览位置丢失。
  要做"返回"得存一个跳转栈（前端一处状态，不动后端）。
- 语音（34）/位置（48）本机暂无样本，等出现真样本再写解析，不猜结构。
- 设置页要不要再扩项（每页显示条数、图片档位偏好"只要原图"等已在候选清单里，
  本轮按用户要求只做三项）。

## 已定并落地的显示语义
- 发送者显示名 = **备注 > 群名片 > 昵称**（`niannian/contacts.py`）。群名片藏在
  `chat_room.ext_buffer` 的 protobuf 里，上游不解析；优先级拿微信自己算好的
  `SessionTable.last_sender_display_name` 实测反推，不是猜的。
- 解析不出发送者的行（撤回/进群提示）署名 `系统消息`、`sender_key` 置空。
  **例外是拍一拍**：它的发送者在 `patinfo/fromusername` 里，`normalize_message`
  会补这一笔认回真人，不再一律算系统消息。
- 非文本摘要：**方括号里只放类型标签**，可读信息都在括号外
  （`[文件] 报名表.docx · 13.8 KB`、`[转账] ￥8.00`、`[视频] 0:06`）；微信自己已经
  写成整句的（拍一拍/撤回/改群名）直接用那句话，不套方括号。
- 群自己发的系统行正文前缀是群 ID（`xxx@chatroom:`），剥掉但不认作发送者。
- 预览图角标只打在气泡缩略图左上角（`tier === 'thumb'`），完整档位文案在弹窗底部。
- 放大弹窗**不放大图片**：`w-auto max-w-full max-h-[74vh]`，弹窗 `w-fit` 收到
  图片大小，上限 `min(1200px,94vw)`。低分辨率预览图因此显示得小，是有意的。
- `Dialog` 基类不再写死 `max-w-*` / `p-*`（`cn` 是纯拼接，写死会和调用方传入的
  同类冲突且胜负取决于样式表顺序），改由 `width` / `padded` 两个 prop 整档给。

## 环境备注
- 旧的问题库保留为 `data/niannian.db.bad-1`（发送者修复前的数据，可对照；确认可删）。
- `data/diag_*.py`、`data/probe_media.py`、`data/verify_senders.py` 是排查脚本，
  `data/` 不入库。`data/_upgrade_check.py` 是子模块升级的存量复核脚本（当前代码路径
  重读关注会话 vs 库里已存的发送者/正文），下次 bump pin 前先停服跑它。
- 图片落盘在 `data/wechat/media/<账号指纹>/`，清库不会自动清这里，需要时手动删。
