<p align="center">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License">
  <img src="https://img.shields.io/badge/python-≥3.12-green" alt="Python">
  <img src="https://img.shields.io/badge/语音-支持-brightgreen" alt="Voice">
  <br><br>
</p>

<h1 align="center">
  ⚡ Voice Code · 语码
</h1>

<p align="center">
  <b>语音驱动的编程助手，会听会说</b>
  <br>
  <i>An interactive coding agent with voice mode</i>
</p>

<p align="center">
  <a href="README.md">🇬🇧 English</a>
  ·
  <a href="#chinese">🇨🇳 中文</a>
</p>

<br>

---

<a id="chinese"></a>

## 🇨🇳 中文

语码 (Voice Code) 是一个由 LLM 驱动的交互式编程助手，运行在终端中，可以替你执行工具操作，还支持语音对话。

### ✨ 特性

| | |
|---|---|
| **💻 CLI REPL** | 交互式命令行，流式响应 |
| **🎤 语音模式** | 动口不动手 — 说出指令，收听回复 |
| **🔧 9 个工具** | Shell、文件读写编辑、搜索、抓网页、待办、提问 |
| **🧠 多模型** | 支持 DeepSeek、OpenAI 等兼容 API |
| **📦 上下文压缩** | 长对话自动压缩，节省 Token |
| **📋 会话持久化** | 自动保存、列出、恢复历史会话 |
| **🖥️ TUI** | 基于 Textual 的终端界面 |

### 🚀 快速开始

```bash
# 安装
pip install voice-code
# 或: uv sync

# 配置
cp .env.example .env   # 设置你的 LLM_API_KEY

# 运行
reasoning               # TUI 模式（默认）
reasoning --plain       # CLI REPL 模式
reasoning-voice         # 语音模式（实验性）
```

### ⚙️ 配置

```ini
# .env
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-v4-pro
```

通过 `models.toml` 支持多后端：

```toml
[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
model_name = "deepseek-v4-pro"
api_key_env = "LLM_API_KEY"

[profiles.openai]
base_url = "https://api.openai.com/v1"
model_name = "gpt-4o"
api_key_env = "OPENAI_API_KEY"
```

### 🎮 CLI 命令

```bash
reasoning                         # TUI 模式（默认）
reasoning --plain                  # CLI REPL 模式
reasoning --profile deepseek       # 指定模型
reasoning --permission-mode bypass  # 跳过确认
```

#### REPL 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/exit` | 退出 |
| `/sessions` | 查看历史会话 |
| `/resume <id>` | 恢复会话 |

### 🎙️ 语音模式

```bash
reasoning-voice                   # 唤醒词 "你好小奕"
reasoning-voice --no-wake         # 跳过唤醒词
reasoning-voice --debug           # 调试日志
```

```
🎤 聆听 → ⚙️ 执行 → 🔊 播报 → 🎤 聆听
   说话      处理      回复    继续听
```

**语音后端：**
- **自建服务** — STT (SenseVoice) + TTS (VoxCPM2 / Fish-Speech)
- **云端** — Step Fun API

### 🛠️ 工具

| 工具 | 说明 |
|------|------|
| Bash | 执行 Shell 命令 |
| FileRead | 读文件（支持偏移和限制） |
| FileWrite | 写/创建文件 |
| FileEdit | 精确字符串替换 |
| Glob | 文件搜索 |
| Grep | 内容搜索 |
| WebFetch | HTTP GET 请求 |
| TodoWrite | 任务清单 |
| AskUser | 询问用户 |

### 🏗️ 架构

```
src/voice_code/
├── cli.py / voice_cli.py / tui.py   # 入口
├── agent/loop.py                     # 查询循环
├── tools/                            # 工具实现
├── compact/                          # 上下文压缩
├── session/                          # 会话持久化
├── voice/                            # 语音模式
├── llm/models.py                     # 模型工厂
├── permissions.py                    # 权限门禁
└── prompts.py                        # 系统提示词
```

### ✅ 质量

```bash
ruff check src/
mypy src/
pytest tests/ -v
```

### 📄 许可证

AGPL-3.0 — 详见 [LICENSE](LICENSE)

---

<p align="center">
  <a href="README.md">🇬🇧 English →</a>
</p>
