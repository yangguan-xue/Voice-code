# Voice Code

语音驱动的 LLM 交互式编程助手，支持命令行与语音两种交互方式。

## 特性

- **CLI 模式** — 交互式 REPL，流式响应
- **语音模式** — 用语音编程：说出指令，收听回复
- **工具系统** — 9 个工具，覆盖文件系统和 Shell 操作
- **多模型** — 支持 DeepSeek、OpenAI 等兼容 API
- **上下文压缩** — 长对话自动压缩，节省 Token
- **会话持久化** — 自动保存会话，支持恢复
- **TUI 模式** — 基于 Textual 的终端界面

## 快速开始

```bash
pip install code-pal
# 或: uv sync

cp .env.example .env   # 设置 LLM_API_KEY
reasoning              # 启动交互式 CLI
reasoning-voice        # 启动语音模式（实验性）
```

## 配置

### API 密钥

```bash
# .env
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1   # 可选
LLM_MODEL_NAME=deepseek-v4-pro             # 可选
```

### 模型配置

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

## CLI 用法

```bash
reasoning                         # 交互式 REPL
reasoning --profile deepseek      # 指定模型
reasoning --permission-mode bypass # 跳过确认
```

### REPL 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/exit` | 退出 |
| `/sessions` | 查看历史会话 |
| `/resume <id>` | 恢复会话 |

## 语音模式（实验性）

```bash
reasoning-voice                   # 唤醒词 "你好小奕"
reasoning-voice --no-wake         # 跳过唤醒词
reasoning-voice --debug           # 调试日志
```

### 流程

```
🎤 聆听 → ⚙️ 执行 → 🔊 播报 → 🎤 聆听
   说话      处理      回复    继续听
```

### 语音后端

- **自建服务** — STT (SenseVoice) + TTS (VoxCPM2 / Fish-Speech)
- **云端** — Step Fun API (ASR + TTS)

## 工具

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

## 架构

```
src/reasoning_agent/
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

## 质量

```bash
ruff check src/
mypy src/
pytest tests/ -v
```

## 许可证

AGPL-3.0 — 详见 [LICENSE](LICENSE)
