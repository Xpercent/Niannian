# 念念 Niannian（重构版）

本机微信只读采集 + 个人工作空间看板。
已完成：微信会话页（关注、看消息与图片、会话内搜索、引用跳转、重新导入）与设置页；
下一步是**消息提炼**。阶段编号不再写在界面与文档标题里（历史文档按当时的叫法，见
`docs/REFACTOR-LOG.md`）。

## 快速开始

要求：Windows + 已登录的微信 4.x（本机进程供密钥提取）、Python 3.13、Node ≥ 22.12。

```powershell
git clone --recursive <本仓库>
cd Niannian2
python -m venv .venv
.venv\Scripts\pip install -e wechatauto-replica fastapi uvicorn pytest
.venv\Scripts\pip install -e .          # 可选：安装后端包（也可免安装直接运行）
cd web
npm install
npm run build                            # 产物输出到 web/dist，由 FastAPI 托管
cd ..
.venv\Scripts\python -m niannian         # 打开 http://127.0.0.1:8765
```

开发前端时可另开 `npm run dev`（Vite 会把 /api 代理到 8765）。

## 配置

优先用页面左侧的**设置页**改（`history_limit` / `poll_seconds` / `port` 三项，
写入工程根目录 `config.json`，前两项即时生效、端口需重启）。
也可直接编辑 `config.json`（可选，参考 `config.json.example`，该文件不入库）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `host` / `port` | `127.0.0.1` / `8765` | 仅监听本机 |
| `wechat_db_dir` | 自动探测 | 微信 4.x 数据目录 |
| `wechat_account` | 自动选择 | 多账号时指定 wxid |
| `history_limit` | 100 | **新关注会话**首次自动导入的最近条数（1–1000）；已关注的会话可用页面上的"重新导入历史"单独指定条数，不影响这里 |
| `poll_seconds` | 3.0 | 采集轮询间隔 |

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
- 其余非纯文本消息由 `describe.py` 解析成中文摘要：文件/视频/链接/小程序/卡片/
  聊天记录/转账/拍一拍/引用/撤回/群动态；语音与位置本机暂无样本，保持上游文本原样，
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
| GET / PUT | `/api/settings` | 读 / 存设置页的三项（返回哪些已生效、哪些需重启） |

## 目录结构

```
niannian/        后端：config / storage / ids（账号指纹与会话主键）/
                 wechat_db（建上游读取器 + 分片补查）/ wechat（采集线程）/
                 normalize（上游行→本地记录）/ contacts / describe / media / api
web/             前端（React 19 + Vite 7 + Tailwind 4，自建组件集）
wechatauto-replica/  上游微信只读库（git submodule，锁定 1.2.6.1）
tests/           pytest（存储、规范化、图片与钥匙闸门、采集线程状态机、
                 上游私有接口契约、真机 fixture 离线回放）
docs/            重构记录与阶段文档
data/            运行时数据（不入库：niannian.db、解密缓存）
```

## 测试

```powershell
.venv\Scripts\python -m pytest
```

阶段详情与决策记录见 `docs/REFACTOR-LOG.md`。
