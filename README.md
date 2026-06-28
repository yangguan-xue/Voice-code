<p align="center">
  <br>
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License">
  <img src="https://img.shields.io/badge/python-≥3.12-green" alt="Python">
  <img src="https://img.shields.io/badge/voice-yes-brightgreen" alt="Voice">
  <br><br>
</p>

<h1 align="center">
  ⚡ Voice Code · 语码
</h1>

<p align="center">
  <i>An interactive coding agent with voice mode</i>
  <br>
  <b>语音驱动的编程助手，会听会说</b>
</p>

<p align="center">
  <a href="#english">🇬🇧 English</a>
  ·
  <a href="README.zh.md">🇨🇳 中文</a>
</p>

<br>

---

<a id="english"></a>

## 🇬🇧 English

Voice Code (语码) is an interactive coding agent powered by LLM. It runs in your terminal, executes tools on your behalf, and supports speech-driven interaction via voice mode.

### ✨ Features

| | |
|---|---|
| **💻 CLI REPL** | Interactive shell with streaming LLM responses |
| **🎤 Voice Mode** | Speak commands, hear replies — hands-free coding |
| **🔧 9 Tools** | Bash, file read/write/edit, glob, grep, web fetch, todo, ask |
| **🧠 Multi-Model** | DeepSeek, OpenAI, or any OpenAI-compatible API |
| **📦 Context Compression** | Auto-compacts long conversations to save tokens |
| **📋 Session Persistence** | Save, list, and resume past sessions |
| **🖥️ TUI** | Textual-based terminal UI with rich rendering |

### 🚀 Quick Start

```bash
# install
pip install voice-code
# or: uv sync

# configure
cp .env.example .env   # set your LLM_API_KEY

# run
reasoning               # CLI mode
reasoning-voice         # voice mode (experimental)
```

### ⚙️ Configuration

```ini
# .env
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-v4-pro
```

Multiple model backends via `models.toml`:

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

### 🎮 CLI Commands

```bash
reasoning                         # interactive REPL
reasoning --profile deepseek      # with model profile
reasoning --permission-mode bypass # skip confirmations
```

#### REPL Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/exit` | Exit |
| `/sessions` | List recent sessions |
| `/resume <id>` | Resume a previous session |

### 🎙️ Voice Mode

```bash
reasoning-voice                   # wake word: "你好小奕"
reasoning-voice --no-wake         # skip wake word
reasoning-voice --debug           # verbose logging
```

```
🎤 listening → ⚙️ working → 🔊 speaking → 🎤 listening
     speak        agent acts      TTS replies
```

**Backends:**
- **Self-hosted** — STT (SenseVoice) + TTS (VoxCPM2 / Fish-Speech)
- **Cloud** — Step Fun API

### 🛠️ Tools

| Tool | Description |
|------|-------------|
| Bash | Execute shell commands |
| FileRead | Read files with offset/limit |
| FileWrite | Write/create files |
| FileEdit | Exact string replacement |
| Glob | File pattern search |
| Grep | Content search |
| WebFetch | HTTP GET |
| TodoWrite | Task list management |
| AskUser | Ask the user |

### 🏗️ Architecture

```
src/reasoning_agent/
├── cli.py / voice_cli.py / tui.py   # Entry points
├── agent/loop.py                     # Query loop
├── tools/                            # Tool implementations
├── compact/                          # Context compression
├── session/                          # Transcript persistence
├── voice/                            # Voice mode
├── llm/models.py                     # Model factory
├── permissions.py                    # Safety gates
└── prompts.py                        # System prompt
```

### ✅ Quality

```bash
ruff check src/
mypy src/
pytest tests/ -v
```

### 📄 License

AGPL-3.0 — see [LICENSE](LICENSE)

---

<p align="center">
  <a href="README.zh.md">🇨🇳 中文版 →</a>
</p>
