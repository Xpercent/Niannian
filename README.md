# 念念 Niannian（重构版）

本机微信只读采集 + 个人工作空间看板。
已完成：**待办事项页**（默认页：指标卡、筛选、右侧抽屉看依据原文并跳回会话、改状态与期限）、
微信会话页（关注、看消息与图片、会话内搜索、引用跳转、重新导入）、设置页、
**模型接入**（OpenAI 兼容服务商 + 设置页"测试连接"，请求体参数就是界面上一框原样 JSON）、
**两轮提炼**（关注会话的消息 → 模型 → `tasks` 表：第一轮从消息里挑出待办，第二轮在轮末自动
把同一件事重复建出来的几条合并成一条；手动点「立即提炼」起一轮、后台线程跑）、
**提炼诊断页**（一轮一批一条：回看每一次请求发出去的原文与模型的原样回复）。
阶段编号不再写在界面与
文档标题里（历史文档按当时的叫法，见 `docs/REFACTOR-LOG.md`）。

## 快速开始

要求：Windows + 已登录的微信 4.x（本机进程供密钥提取）、Python 3.13、Node ≥ 22.12。

```powershell
git clone --recursive <本仓库>
cd Niannian2
python -m venv .venv
.venv\Scripts\pip install -e wechatauto-replica fastapi uvicorn pytest openai
.venv\Scripts\pip install -e .          # 可选：安装后端包（也可免安装直接运行）
cd web
npm install
npm run build                            # 产物输出到 web/dist，由 FastAPI 托管
cd ..
.venv\Scripts\python -m niannian         # 打开 http://127.0.0.1:8765
```

开发前端时可另开 `npm run dev`（Vite 会把 /api 代理到 8765）。

## 配置

优先用页面左侧的**设置页**改（采集三项 + 模型接入六项 + 提炼五项，写入工程根目录
`config.json`，除端口外都即时生效）。
也可直接编辑 `config.json`（可选，参考 `config.json.example`，该文件不入库）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `host` / `port` | `127.0.0.1` / `8765` | 仅监听本机 |
| `wechat_db_dir` | 自动探测 | 微信 4.x 数据目录 |
| `wechat_account` | 自动选择 | 多账号时指定 wxid |
| `history_limit` | 100 | **新关注会话**首次自动导入的最近条数（1–1000）；已关注的会话可用页面上的"重新导入历史"单独指定条数，不影响这里 |
| `poll_seconds` | 3.0 | 采集轮询间隔 |
| `llm_base_url` | `https://api.siliconflow.cn/v1` | 模型服务商地址（任何 OpenAI 兼容的都能用，末尾带 `/v1`） |
| `llm_model` | `Qwen/Qwen3-8B` | 服务商那边的完整模型 ID |
| `llm_api_key` | 空 | 密钥。**只有这一份是敏感内容**：接口只回掩码（`sk-…xgme`），设置页那格读回来永远是空的，留空保存 = 不改 |
| `llm_include_images` | false | 提炼时喂不喂图片。**第一轮提炼没读它**（图片只喂 `[图片]` 那几个字），等要喂真图的那一轮再接 |
| `llm_extra_body` | 空 = 不带额外字段 | **一框原样 JSON**（温度、思考、输出上限……）。里面的键原样并进请求体顶层，各家字段名按那家的文档写；空着就只发模型名与正文，默认值全交回服务商。`model`/`messages`/`stream` 与密钥不许写在这里，坏 JSON 保存当场就拦 |
| `extract_round_limit` | 200 | 一轮最多处理多少条（1–1000），攒多了分几轮跑 |
| `extract_batch_limit` | 20 | 一次请求带几条（1–50），模型看到的依据编号就是 0..条数-1；越长越容易整批读不懂 JSON |
| `extract_history_limit` | 15 | 每批附带几条旧消息当上文（0–100），**不带编号、不能据它建任务**；0 就是不给上文 |
| `extract_prompt` | 空 = 内置那段 | 提炼提示词**整段可改**。设置页显示的是正在生效的那份原文，「恢复默认」把内置那段放回框里；与内置一字不差时库里落空串（不留一份会挡住日后改内置的副本） |
| `extract_profile` | 空 | **用户画像**：一段自由文本，说清"我是谁"（名字与群里被叫的称呼、班级、小组、宿舍、职务、邮箱）。它随每批正文以 `<profile>` 段发出（**不写在提示词里**，改提示词不会把它弄没），模型拿它判断"@YX""第 5 组"点的是不是你；空着整段不发，此时模型只认得出 `sender="我"` 那几条 |

## 行为约定

- **关注即采集**：在“添加关注”弹窗关注一个会话后，采集器自动导入最近
  `history_limit` 条消息，之后按水位增量读取新消息；**取消关注停止采集**，
  已入库消息保留，重新关注从水位续传、不会重复导入。
- **重新导入更早历史**：会话标题栏（"取消关注"同一栏）有"最近 [N] 条 重新导入"，
  填一个条数（1–1000）即可。条数**记在该会话自己身上**，不改全局配置、
  不影响其它会话。实现是"记下条数 → 清该会话水位 → 重走首次导入那条路"，
  重复消息由 `source_id` 唯一键自动跳过，**不删除任何已入库消息**。
  注意：单次最多 1000 条，更深的历史目前不做无限往回翻页。
- 页面消息顺序按**消息时间**排，不按入库先后——否则重新导入会把顺序打乱。
- 采集为**只读**：仅读取本机微信数据库解密副本，不驱动 GUI、不发送消息、
  不触发微信下载附件。解密缓存固定在 `data/wechat/cache/<账号指纹>/`。
- **发送者解析**：`real_sender_id` 按消息分片库自身的 `Name2Id` 还原（上游用
  全局表会串号），正文里的群发送者前缀只作兜底；`is_self` 依据还原出的
  wxid 与当前账号比对。
- **图片**：正文换成 `[图片]` 摘要，本机文件解密到
  `data/wechat/media/<账号指纹>/`，**库里只存相对链接**。原图 > 本机下发 >
  预览图三档取最高；没在微信里点开过的图片会显示预览图档并在放大时标注
  “预览图”。采集那一刻文件还没落盘的，由**采集线程每轮**凭哈希补链接（网页
  请求一律纯读，不解密）；本机出现更高一档时同样由采集线程定期重扫换掉链接。
- **出网的只有两条线路**：设置页的"测试连接"（一句固定测试语，**不含任何聊天内容**），
  以及**你手动点起来的那一轮提炼**——后者把关注会话里还没提炼的消息正文（`[图片]`、
  `[文件] 名字`这类气泡上那行字）连同上文一起发给服务商。不驱动微信、不改微信任何东西，
  也不受"采集只读"约束的那条线影响（提炼只读自己的库）。一轮默认 200 条、一次请求默认 20 条
  （都在设置页「提炼」里改），没点就没有任何请求。`llm_include_images` 这一轮**仍未生效**：
  图片只喂 `[图片]` 那几个字。
- **提炼的进度按每条消息记（`extracted_at`）**：一轮吃过的消息不会重问，判出"这批没有我
  要做的事"也算吃过；某批请求失败或回复读不懂时**那批不标记**，下一轮照旧取到，
  所以服务商限流（429）不会静默吞掉消息。
- **第二轮合并在轮末自动跑，不是另起一轮**：每个会话的批次都落库之后紧接着发一次请求，
  回执只有需要合并的组（未合并的任务一个字都不动），程序删旧的、建新的、依据消息取并集，
  期限取几条里最晚的、优先级取最高的——**这两个字段第二轮不判**。它记在同一轮的
  `model_calls` 里（`stage='merge'`），诊断页标着「合并」。合并失败不影响第一轮已落库的任务。
- **每一批都留档**：发出去的正文（系统提示词 + 本批 XML）与模型的原样回复逐字存进本机
  `extract_runs` / `model_calls` 两张表，**只留最近 30 轮**，失败的请求也在里面（连服务商
  那句话）。服务商地址与 API Key 不入库、也不从诊断接口下发。
- 其余非纯文本消息由 `describe.py` 解析成中文摘要：文件/视频/链接/小程序/卡片/
  聊天记录/转账/拍一拍/引用/名片/撤回/群动态；语音与位置本机暂无样本，保持上游文本原样，
  认不出的 XML 一律不猜结构。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/status` | 采集器状态（连接状态、账号、上次同步） |
| GET | `/api/sessions` | 已关注会话列表（含消息数） |
| GET | `/api/sessions/{id}/messages?limit=&before_seq=&before_id=` | 消息分页（按消息时间新→旧游标） |
| GET | `/api/sessions/{id}/search?q=&limit=` | 在该会话**已入库**的消息正文里找关键词 |
| GET | `/media/<账号指纹>/<文件名>` | 本机图片附件（仅工程媒体目录内） |
| GET | `/api/wechat/catalog` | 微信实时会话目录 |
| POST | `/api/sessions/{id}/follow` / `unfollow` | 关注 / 取消关注 |
| POST | `/api/sessions/{id}/reimport?limit=N` | 按 N 条重新导入该会话历史（只影响该会话） |
| GET | `/api/tasks?session_id=&status=&priority=&q=&due=&limit=` | 待办清单（`due` 认 today/overdue/upcoming；`sources` 是依据消息的 sort_seq） |
| GET | `/api/tasks/stats` | 待办页那几张卡与导航角标的数字（进行中/今天截止/已逾期/累计完成/真实消息/等待提炼/关注会话） |
| GET | `/api/tasks/{id}` | 一条待办 + **依据消息原文**（按 sort_seq 升序，形状与消息列表一致） |
| PATCH | `/api/tasks/{id}` | 改标题/说明/优先级/期限/状态；认不出来的中文报错，期限与模型那份过同一个解析器 |
| POST | `/api/extract` | 起一轮提炼（body 可带 `session_id`，空=所有关注会话）。**立刻回状态**，不等模型；已经在跑就 `accepted=false`，不排队第二轮；配置没填齐直接 400，不出网 |
| GET | `/api/extract/status` | 这一轮跑到哪儿了（`total/done/batches/created/failed_batches/error`，另带本轮那一行的 `run_id`） |
| GET | `/api/diagnostics/runs?limit=` | 最近的提炼轮次（新→旧，每轮带请求数与失败数） |
| GET | `/api/diagnostics/runs/{run_id}` | 一轮的请求序列（概要 + 字符数，**不带正文**） |
| GET | `/api/diagnostics/calls/{call_id}` | 一次请求的原始内容：发出去的 system/user 正文、请求体里除 messages 之外的那些字段（`body`）、模型原样回复与思考内容 |
| GET / PUT | `/api/settings` | 读 / 存设置页的十三项（返回哪些已生效、哪些需重启；密钥只回掩码，`extract_prompt` 那格回的是**正在生效的那段**提示词，顶层另给 `extract_prompt_default` 供「恢复默认」；`extract_profile` 与 `llm_extra_body` 那两格回的就是库里那份原话，它们没有内置默认） |
| POST | `/api/settings/test-connection` | 用界面上当前那份参数试一次服务商，**不需要先保存**；通不通都回 200，结果在 `ok`/`error` 里，并带 `request`——**这一次实际发出去的请求体**（格式化好、密钥已抹） |

## 目录结构

```
niannian/        后端：config / storage（含 tasks 表与提炼诊断的 extract_runs、
                 model_calls）/ ids（账号指纹与会话主键）/
                 wechat_db（建上游读取器 + 分片补查）/ wechat（采集线程）/
                 normalize（上游行→本地记录）/ contacts / describe / media /
                 llm（发一次模型请求）/ extract（提炼输入契约，纯函数）/
                 extractor（提炼轮次线程）/ api
web/             前端（React 19 + Vite 7 + Tailwind 4，自建组件集）
wechatauto-replica/  上游微信只读库（git submodule，锁定 1.2.6.1）
tests/           pytest（存储、规范化、图片与钥匙闸门、采集线程状态机、
                 上游私有接口契约、真机 fixture 离线回放、提炼的输入契约与轮次、
                 提炼诊断的留档与只读接口）
docs/            重构记录与阶段文档
data/            运行时数据（不入库：niannian.db、解密缓存）
```

## 测试

```powershell
.venv\Scripts\python -m pytest
```

阶段详情与决策记录见 `docs/REFACTOR-LOG.md`。
