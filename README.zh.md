# Voice Code · 语码

<p align="center">
  <img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/Python-≥3.12-green.svg" alt="Python">
  <img src="https://img.shields.io/badge/Tests-604%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Code%20Style-Ruff-000000.svg" alt="Code Style">
  <img src="https://img.shields.io/badge/Interaction-CLI%20%7C%20TUI%20%7C%20Voice%20%7C%20Desktop-orange.svg" alt="Interaction">
</p>

<p align="center">
  <b>语音驱动的自主交互式编程助手 (Voice-First Autonomous Coding Agent)</b><br>
  <i>原生支持 TUI 现代视窗、CLI REPL、全双工语音、Tauri 桌面端与在线 Web 演示，内置子 Agent 并行监督、5 层上下文自适应压缩、分层持久化记忆与生产级安全沙箱。</i>
</p>

<p align="center">
  <a href="README.md">🇬🇧 English</a>
  ·
  <b>🇨🇳 简体中文</b>
</p>

---

## 🌐 在线体验与公开资源

- **在线 Web Demo**：[https://voice-code.yangguanxue.top/](https://voice-code.yangguanxue.top/)（受控沙盒，邀请码进入）
- **公开仓库**：[https://github.com/yangguan-xue/Voice-code](https://github.com/yangguan-xue/Voice-code)
- **许可证**：AGPL-3.0
- **运行环境**：Python ≥ 3.12

---

## 🌟 核心特性

### 1. 多模态交互形态 (Multi-Surfaces)

- **🖥️ 现代 TUI 视窗（推荐主力入口）**
  - **优雅主题系统**：基于 Textual 框架打造，纯黑底色 (`#000000`) + 灰阶层次 + 克制暖红点缀 (`#df6a5c`)。
  - **结构化消息树**：流式 Markdown 渲染、模型思考过程 (Thinking) 折叠/展开、工具调用与结果实时卡片。
  - **会话历史侧边栏**：左侧一览历史会话，支持会话列表检索、快速恢复与一键创建新会话。
  - **交互式权限审批**：危险操作主动弹出确认对话框，支持会话级与工作区级规则记忆。
- **⚡ 极简 CLI REPL**
  - 纯文本终端交互，支持流式输出、推理思维链暗色显示与非阻塞中断，开销轻量，适合脚本管道与无图形环境。
- **🎙️ 全双工语音模式 (Voice Dashboard)**
  - **4 态有限状态机**：`sleeping` (休眠待唤醒) ➔ `listening` (聆听输入) ➔ `working` (推理与执行) ➔ `speaking` (语音播报)。
  - **唤醒与活动检测**：支持唤醒词激活（默认“你好小奕”等，可定制或 `--no-wake` 常开）、自适应静音检测 (VAD)。
  - **双语音后端支持**：
    - **云端服务**：官方支持 Step Fun (阶跃星辰) 实时语音大模型 (ASR + TTS)。
    - **本地自建**：支持 SenseVoice (STT) + VoxCPM2 / Fish-Speech (TTS) 本地推理部署。
  - **全屏交互控制台**：动态麦克风音量指示条、实时流式识别转写、Agent 执行细节与各阶段耗时统计面板。
- **💻 跨平台桌面端应用 (Desktop)**
  - 基于 Tauri 构建原生壳，支持一键打包内置独立的 Python 运行时，开箱即用免配环境。
- **🌐 托管 Web Demo**
  - 基于 React + FastAPI 构建的演示端，运行于受控沙盒内，支持代码差异预览与图片工具。

### 2. 子 Agent 并发与监督闭环 (Subagents & Supervisor)

- **指挥官调度架构 (CommandAssistant)**：
  - 形成 **派发 (Dispatch) ➔ 独立执行 (Execute) ➔ 复核审查 (Review) ➔ 继续推进/完结 (Continue/Finish)** 的完整自动化闭环。
- **隔离运行时与任务管理工具**：
  - 子 Agent 拥有独立的运行时上下文，主进程可通过内置任务工具进行全生命周期掌控：
    - `agent`：派发具体子任务给专职 Agent；
    - `task_list` / `task_get` / `task_stop`：查看运行队列、检查输出进度、随时终止异常任务。

### 3. 5 层自适应上下文压缩流水线 (Compaction Pipeline)

长程交互中自动执行渐进式上下文剪裁与压缩，兼顾关键上下文不丢失与 Token 成本控制：

| 压缩层级 | 触发机制 | 成本 | 压缩策略 |
| :--- | :--- | :--- | :--- |
| **MicroCompact** | 每轮执行后 | 0 Token | 自动清理早期工具调用的冗长无用输出 |
| **SNIP** | 历史累积 | 0 Token | 修剪过往轮次中的非关键性中间工具返回块 |
| **ContextCollapse** | 轮次累积 | 0 Token | 将只读性探索探查合并为紧凑的结构化状态快照 |
| **AutoCompact** | 接近阈值上限 | 1 次 LLM | 对早期对话历史生成高保真语义总结与关键共识提取 |
| **ReactiveCompact** | 遭遇 413 / 超限 | 1 次 LLM | 上下文溢出时的自愈熔断机制，保障任务不中断 |

### 4. 生产级安全权限引擎 V2 (Permission Engine & Sandbox)

- **动态三态裁决**：支持 `Allow`（规则白名单自动放行）、`Deny`（强制阻断）、`Ask`（弹窗/交互确认）。
- **Bash 安全沙箱**：内置危险指令正则过滤、资源限额控制与网络策略阻断。
- **敏感信息防泄漏 (Secrets Redaction)**：自动扫描 API Key、私钥与凭据，绝不回显到日志或上下文。
- **MCP 首次信任与审计链**：支持 MCP (Model Context Protocol) 首次调用显式授权，全流程写入防篡改审计日志。

### 5. 分层持久化记忆系统 (Memory & RAG V3)

- **双层架构**：本地 SQLite 作为单一事实源 (Source of Truth)，Milvus 作为可重建的高性能稠密向量 + 中文 BM25 混合检索索引。
- **渐进式偏好抽取**：支持 `shadow`（影子静默评测）➔ `review`（人工审核候选）➔ `automatic`（门禁测试放行）三级演进。
- **死信队列容灾 (DLQ)**：后台抽取或异步失败任务自动转入死信队列，提供 `reasoning-memory dlq` 诊断、原因排查与一键回放重试。

### 6. 全链路可观测性 (Telemetry & Observability)

- **9 面板性能指标**：覆盖 Agent 循环、LLM 调用、工具执行、权限裁决、会话状态、语音延迟与 RAG 命中率。
- **严格隐私门禁**：遥测数据与指标绝对屏蔽用户源代码、私有密钥与语音音频。
- **一键健康体检**：`uv run reasoning-status-sync` 提供全系统组件 `--json` 状态巡检。

---

## 🏗️ 架构总览

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        用户交互入口 (Surfaces)                          │
│   Textual TUI (默认)   │   CLI REPL (--plain)   │   Voice Dashboard    │
│   会话侧边栏 + 思考折叠  │   纯文本流式输出        │   4 态状态机 + VAD   │
│   Tauri 桌面端应用     │   Web Demo (受控沙盒)   │                      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Agent Runtime & Query Loop                         │
│  ┌───────────────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
│  │   CommandAssistant    │  │ Compaction (5层) │  │  Abort/Recovery  │  │
│  │   监督与结果复核      │  │ Micro/SNIP/Auto  │  │  跨线程中断自愈  │  │
│  └───────────────────────┘  └─────────────────┘  └──────────────────┘  │
└───────────┬───────────────────────────────┬────────────────────────────┘
            │                               │
            ▼                               ▼
┌───────────────────────┐       ┌────────────────────────────────────────┐
│   安全与权限控制中心    │       │            工具生态与子 Agent          │
│ ┌───────────────────┐ │       │ 核心工具:                              │
│ │ Permission Engine │ │       │   • bash (带沙箱)   • file_read/write  │
│ │ Allow / Deny / Ask│ │       │   • file_edit      • glob / grep      │
│ ├───────────────────┤ │       │   • web_fetch      • todo_write       │
│ │ Secrets Redaction │ │       │ 子 Agent 调度:                         │
│ │ Audit Log 审计链  │ │       │   • agent / task_list / task_stop      │
│ └───────────────────┘ │       │ Web / 多模态扩展:                      │
│                       │       │   • image_gen / image_edit             │
│                       │       └────────────────────────────────────────┘
└───────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     分层持久化记忆与容灾 (Memory)                       │
│     SQLite (单一事实源)  ◄── 同步/重建 ──►  Milvus (稠密向量 + BM25)     │
│     提取流水线 (Shadow/Review/Auto)    │  任务死信容灾队列 (DLQ)        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ 内置核心工具集

| 工具名称 | 权限级别 | 功能描述 |
| :--- | :--- | :--- |
| **`Bash`** | 默认确认 (可配置) | 执行系统 Shell 命令（支持沙箱隔离、超时控制与敏感凭据脱敏） |
| **`FileRead`** | 自动放行 (只读) | 安全读取指定路径文件，支持行号范围与偏移量限制 |
| **`FileWrite`** | 默认确认 | 覆盖或新建本地文件 |
| **`FileEdit`** | 默认确认 | 针对指定文件进行精确字符串匹配替换 |
| **`Glob`** | 自动放行 (只读) | 基于通配符快速检索匹配的文件名路径 |
| **`Grep`** | 自动放行 (只读) | 基于 ripgrep 进行高性能多线程代码/内容全文检索 |
| **`WebFetch`** | 默认确认 | 发起 HTTP GET 请求获取网页内容与 API 数据 |
| **`TodoWrite`** | 自动放行 | 维护结构化多步骤任务清单状态 |
| **`AskUser`** | 内部交互 | 向用户发起针对性反向澄清询问 |
| **`Agent`** | 默认确认 | 派发并启动独立的后台子 Agent 任务 |
| **`TaskList/Get/Stop`** | 自动放行 | 查询当前子任务列表、获取详细日志或终止执行 |
| **`ImageGen/Edit`** | 默认确认 | 原生图像生成与编辑扩展（适配 Web Demo 与多模态交互） |

---

## 🚀 快速上手

### 1. 环境准备

- **Python**：≥ 3.12
- **包管理器**：推荐使用 [uv](https://github.com/astral-sh/uv)
- **外部依赖**：代码搜索工具 `ripgrep`（可选）、音频驱动（语音模式需要麦克风权限）

```bash
# 同步并安装依赖
uv sync
```

### 2. 基础配置

复制配置文件模板并配置您的 LLM 提供商密钥：

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
# DeepSeek 官方配置示例
LLM_API_KEY=sk-your-deepseek-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-chat
```

您也可以通过 `models.toml` 配置多个模型画像与降级策略：

```toml
[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model_name = "deepseek-chat"
api_key_env = "LLM_API_KEY"
fallback_profile = "deepseek-reasoner"

[profiles.openai]
base_url = "https://api.openai.com/v1"
model_name = "gpt-4o"
api_key_env = "OPENAI_API_KEY"
```

### 3. 运行终端交互

#### 🖥️ 交互式 TUI 视窗（推荐）

```bash
uv run reasoning
# 或使用指定的模型 profile
uv run reasoning --profile deepseek
```

#### ⚡ 极简终端模式 (CLI REPL)

```bash
uv run reasoning --plain
```

#### 🎙️ 全功能语音模式 (Voice Dashboard)

```bash
# 默认唤醒词激活模式（“你好小奕”）
uv run reasoning-voice

# 跳过唤醒词，启动后直接持续监听
uv run reasoning-voice --no-wake

# 使用 Step Fun 云端语音后端
uv run reasoning-voice --stepfun-key "your-stepfun-key"

# 极简 ANSI 语音终端模式
uv run reasoning-voice --plain-display
```

---

## 💻 桌面端与 Web Demo

### 桌面端 (Tauri Desktop App)

```bash
cd desktop
pnpm install
pnpm test
pnpm build
```

构建自带独立 Python Runtime 的 Windows 安装包：

```powershell
pnpm tauri:build:windows:runtime
```

> **提示**：Runtime 安装包会在打包阶段内置独立的 Python 运行时，终端用户无需预先安装 Python 或 `uv` 即可直接运行桌面端。

### 托管 Web Demo

前端构建与运行：

```bash
cd web
pnpm install
pnpm test -- --run
pnpm build
```

后端服务启动：

```bash
uv run reasoning-web-demo --host 127.0.0.1 --port 8787
```

生产部署模板（Nginx / systemd / Docker）位于 `deploy/web-demo/` 目录。

---

## ⌨️ 常用命令与交互快捷键

### 会话内斜杠命令

| 命令 | 适用界面 | 功能说明 |
| :--- | :--- | :--- |
| `/help` | 全部 | 查看支持的全部内置命令与说明 |
| `/sessions` | 全部 | 查看近期持久化保存的历史会话列表 |
| `/resume <session_id>` | 全部 | 恢复并继续指定的历史会话 |
| `/perm-rule [session\|workspace] <idx>` | 全部 | 查看当前生效的权限规则详情 |
| `/perm-delete [session\|workspace] <idx>`| 全部 | 删除指定的权限规则 |
| `/remember user <content>` | 全部 | 记录一条跨项目的用户长期全局偏好 |
| `/remember project <content>` | 全部 | 记录一条仅当前项目生效的记忆偏好 |
| `/memory candidates` | 全部 | 查看待审核的自动记忆抽取候选条目 |
| `/memory approve <id>` / `/memory reject <id>` | 全部 | 审核通过或拒绝自动录入的记忆条目 |
| `/exit` 或 `/quit` | 全部 | 安全保存状态并退出 |

### 交互快捷键

- **TUI 界面**：
  - `Ctrl + C`：打断当前正在进行的推理生成或工具执行。
  - `Ctrl + D`：退出当前交互程序。
- **Voice 语音控制台**：
  - `Space`：快速暂停 / 恢复语音监听状态。
  - `Esc`：紧急打断当前 Agent 正在执行的工作或 TTS 播报。
  - `Q`：安全退出语音工作台。

---

## 🧪 质量门禁与测试

本项目采用严格的工程质量保障，包含 600+ 单元测试、集成测试与真实注入恢复测试：

```bash
# 静态代码检查
uv run ruff check src/

# 全量测试套件执行
uv run pytest tests/ -v
```

---

## ⚠️ 安全与公开仓库策略

1. **执行安全**：Voice Code 具备在本地执行 Shell 脚本和编辑文件的能力。日常使用强烈建议保持权限确认（Ask 模式）开启，在执行高危命令前仔细审查工具参数。
2. **Web Demo 沙箱**：公开 Web Demo 运行于受控沙盒容器中，仅供在线功能预览与技术验证，非通用云端无限制开发机。
3. **公开仓库策略**：本仓库为对外公开发布面，不包含任何内部参考代码、本地调试环境、构建二进制缓存或敏感访问凭据。

---

## 📄 许可证 (License)

本项目采用 **AGPL-3.0** 开源许可证。详情请参阅 [LICENSE](LICENSE)。
