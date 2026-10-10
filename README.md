# 念念 · 微信消息与智能待办

把真实微信消息整理成可追溯的行动清单。基于 Windows 本机微信 4.x，只读采集消息与已下载附件；使用关闭思考模式的云端模型提炼，保留原文、过滤依据和每次变更。

没有演示消息播种，也没有手动消息录入入口：待办只能来自你本机真实收到的微信消息。

## 1. 克隆仓库

微信只读适配器是一个独立仓库，以 Git 子模块固定在 `wechatauto-replica/`，所以第一次克隆必须带 `--recursive`：

```powershell
git clone --recursive https://github.com/Xpercent/Niannian.git
cd Niannian
```

`--recursive` 会从 `https://github.com/Xpercent/wechatauto-replica` 拉取适配器，并检出主工程固定的提交（`v1.2.5.1`，`fbfb026`）。也可以先普通克隆，再补一句 `git submodule update --init --recursive`。

已经克隆过、但子模块地址还是旧上游时，同步一次地址即可，本地已检出的代码和提交指针都不受影响：

```powershell
git submodule sync --recursive
git submodule update --init --recursive
git submodule status   # 应显示 fbfb0267... wechatauto-replica (v1.2.5.1)
```

`wechatauto-replica` 按固定提交使用，不在其中改代码；上游适配差异一律在 `niannian/wechat.py` 的封装层处理。

## 2. 安装环境

需要 Windows、已登录的微信 4.x、64 位 Python 3.10+（本机验证为 3.13）。构建前端还需要 Node.js 22.12+ 和 npm——`niannian/web/dist/` 不进 Git，所以清洁检出必须自己构建一次前端，否则打开首页会提示「前端尚未构建」。

一条命令装完（建虚拟环境、装依赖、初始化子模块、安装适配器、构建前端）：

```powershell
.\scripts\setup.ps1
```

想手动执行，等价步骤是：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -e .\wechatauto-replica

cd niannian\web
npm ci
npm run build
cd ..\..
```

新克隆没有 `config.json`。把 `config.json.example` 复制一份、去掉 `.example` 后缀，填上模型地址、Key 和模型名，也可以启动后在「设置与数据」里填。


## 3. 启动

```powershell
.\.venv\Scripts\python.exe -X utf8 -m niannian --port 8765
```

`-X utf8` 让日志与报错在中文 Windows 控制台上保持 UTF-8 输出。`.\scripts\start.ps1` 是这条命令的包装，额外支持 `-Port` 与 `-NoWeChat`（`start.ps1 -NoWeChat` 仅供离线查看已保存数据）；它会先切到工程根目录，因为上游微信适配器用相对路径 `wechatauto_logs` 写日志。

打开 http://127.0.0.1:8765/ 。模型请求始终发送 `enable_thinking: false`。

## 4. 使用流程

1. 「微信会话」选择真实会话，打开关注；新会话默认不关注。关注后读取最近历史，可以再次导入最多 1000 条，自动去重。
2. 在「我的画像」写准确的身份、职责和参与意愿。画像为空时，条件活动邀请或指定他人的职责通知留在过滤记录中等待复核。
3. 点击「立即提炼」，或者等待自动轮次。运行中显示真实进度，支持取消；没有处理完的消息保留在队列。
4. 在待办清单筛选、编辑、完成或忽略事项；打开详情查看完整来源、附件及变更。忽略事项可以恢复。仅有明确本人身份的完成消息才会自动核销任务。
5. 查看过滤记录，按需要调整规则与画像。导出 JSON/Markdown 或创建一致性数据库备份。

关闭会话关注仅暂停提炼；已经选择的会话继续采集，重新关注后可以继续处理。采集不会发送微信消息或驱动微信下载附件。

## 5. 架构

采用模块化单体：一个本地 FastAPI 服务、SQLite WAL 仓储、一个串行微信读取线程、一个提炼协调器和 Vite + React + TypeScript 前端。样式使用 Tailwind CSS，本地组件采用 shadcn/ui，图表采用 shadcn/ui Charts（Recharts），图标来自 lucide-react。页面资源全部本地提供，不依赖 CDN。

| 模块 | 职责 |
| --- | --- |
| `niannian/wechat.py` | 上游只读适配、账号隔离、串行命令、可靠采集水位及身份证据 |
| `niannian/media.py` | 解析真实附件并定位已下载本机文件 |
| `niannian/storage.py` | 消息与水位原子持久化、来源幂等、UTC 索引、任务版本与审计、一致性快照 |
| `niannian/engine.py` | 聚合、会话依次处理、总结压缩协调、失败留存、指令执行与本人身份约束 |
| `niannian/llm.py` | OpenAI 兼容请求、紧凑上下文、结构验证与业务闸门 |
| `niannian/jobs.py` | 定时/手动单轮提炼、持久化进度、取消和退出恢复 |
| `niannian/config.py` | 类型化配置、文件原子保存、单轮快照 |
| `niannian/api.py` | 应用工厂、显式生命周期、薄 HTTP 接口 |
| `niannian/web/` | 亮色工作台、来源抽屉、会话/规则/画像/设置 |

微信采集先提交原始消息与水位，再交给提炼。任务编辑带版本号，冲突返回 409，避免模型覆盖用户编辑。自动轮次处理开始时符合静默条件的关注会话，同一会话按正文预算拆批，不限消息总数或请求段数；整轮结束后才等待下一次间隔。长正文先总结压缩，原文保留；画像完整提供。云端失败或无效/漏答的消息保持未处理，下轮再请求，不本地兜底。请求前过滤与业务校验保留，模型未启用时仍可本地处理。完整 API 见 `/docs`。

## 6. 验证与资料

```powershell
.\scripts\verify.ps1
.\.venv\Scripts\python.exe -X utf8 scripts\check-wechat.py
```

Python 统一通过 pytest 收集 unittest 与 fixture 测试；前端验证采用 TypeScript/Vite 构建与关键工具检查。真实消息回放依赖本地 `data/raw-wechat.json`，清洁检出中缺少捕获会明确跳过，不用编造消息替代。近期验证与历史运行证据见 [docs/VERIFICATION.md](docs/VERIFICATION.md)。

- [重构前审查](docs/AUDIT.md)
- [Agent 项目维护规范](AGENTS.md)
- [新会话交接文档](docs/HANDOFF.md)
- [架构与数据一致性](docs/ARCHITECTURE.md)
- [微信采集与身份判定](docs/WECHAT.md)
- [设计方向与交互](docs/DESIGN.md)
- [独立视觉评审](docs/VISUAL-REVIEW.md)

## 7. 已知边界

依赖本机微信登录状态、Windows 和上游微信数据库适配，客户端升级可能需要同步更新适配器。只处理文字及附件说明，不做图片 OCR、语音转写，也不会自动下载未落地媒体。模型结果需要人工判断，缺失画像和角色不清的通知会保守留待复核；历史已过期事项仍按原文保留，用户可忽略。过滤记录每会话默认保留最近 100 个判定，可在高级设置调节，不删除原始聊天。服务定位为个人工作台，未实现多用户协作或移动端服务器。

## 8. 前端维护

```powershell
cd niannian/web
npm ci
npm run dev    # 开发地址见终端；API/media 代理到 127.0.0.1:8765
npm run build  # 将源码编译到 dist，FastAPI 刷新后加载
npm test       # 附件路径与上海时间边界的轻量检查
```

源码按 `src/pages/`、`src/components/`、`src/lib/` 分工；shadcn/ui 组件源码在 `src/components/ui/`，统一主题在 `src/styles.css`。修改源码后须重新构建并刷新浏览器。`dist/` 被 Git 忽略，但 `[tool.setuptools.package-data]` 会把它作为包数据携带，因此发布或安装 wheel 前要先构建。已移除旧命令面板和应用自定义快捷键。
