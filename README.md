# 念念 Niannian（重构版）

本机微信只读采集 + 个人工作空间看板。当前处于**阶段一：微信会话看板**，
仅包含微信会话页（添加关注、查看关注对话消息），不含待办提炼等功能。

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

`config.json`（可选，参考 `config.json.example`，该文件不入库）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `host` / `port` | `127.0.0.1` / `8765` | 仅监听本机 |
| `wechat_db_dir` | 自动探测 | 微信 4.x 数据目录 |
| `wechat_account` | 自动选择 | 多账号时指定 wxid |
| `history_limit` | 100 | 关注会话后自动导入的最近消息条数（1–1000） |
| `poll_seconds` | 3.0 | 采集轮询间隔 |

## 阶段一行为约定

- **关注即采集**：在“添加关注”弹窗关注一个会话后，采集器自动导入最近
  `history_limit` 条消息，之后按水位增量读取新消息；**取消关注停止采集**，
  已入库消息保留，重新关注从水位续传、不会重复导入。
- 采集为**只读**：仅读取本机微信数据库解密副本，不驱动 GUI、不发送消息。
  解密缓存固定在 `data/wechat/cache/<账号指纹>/`，与其它工程隔离。
- 非纯文本消息（图片/转账/拍一拍等）**原样展示**上游给出的文本或 XML，
  格式化留到后续阶段。
- 消息正文中的群发送者前缀会被剥离并解析为发送者；`is_self` 仅依据
  原文前缀 / XML 根节点 `fromusername` 与当前账号比对。

## HTTP 接口（阶段一）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/status` | 采集器状态与计数 |
| GET | `/api/sessions` | 已关注会话列表（含消息数） |
| GET | `/api/sessions/{id}/messages?limit=&before_id=` | 消息分页（新→旧游标） |
| GET | `/api/wechat/catalog` | 微信实时会话目录 |
| POST | `/api/sessions/{id}/follow` / `unfollow` | 关注 / 取消关注 |

## 目录结构

```
niannian/        后端（config / storage / wechat 采集器 / api / __main__）
web/             前端（React 19 + Vite 7 + Tailwind 4，自建组件集）
wechatauto-replica/  上游微信只读库（git submodule，锁定 1.2.5.1）
tests/           pytest（存储与消息规范化）
docs/            重构记录与阶段文档
data/            运行时数据（不入库：niannian.db、解密缓存）
```

## 测试

```powershell
.venv\Scripts\python -m pytest
```

阶段详情与决策记录见 `docs/REFACTOR-LOG.md`。
