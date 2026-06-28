# Voice Code

An interactive coding agent powered by LLM, with voice mode support.

## Features

- **CLI Mode** — Interactive REPL with streaming responses
- **Voice Mode** — Speech-driven development: speak commands, hear replies
- **Tool System** — 9 tools for filesystem and shell access
- **Multi-Model** — Supports DeepSeek, OpenAI, and any OpenAI-compatible API
- **Context Compression** — Automatic compaction for long conversations
- **Session Persistence** — Transcripts saved for resume and review
- **TUI Mode** — Textual-based terminal UI

## Quick Start

```bash
pip install code-pal
# or: uv sync

cp .env.example .env        # set your LLM_API_KEY
reasoning                   # start interactive CLI
reasoning-voice             # start voice mode (experimental)
```

## Configuration

### API Key

```bash
# .env
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1   # optional
LLM_MODEL_NAME=deepseek-v4-pro             # optional
```

### Model Profiles

Multi-backend via `models.toml`:

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

## CLI Usage

```bash
reasoning                         # interactive REPL
reasoning --profile deepseek      # with model profile
reasoning --permission-mode bypass # skip confirmations
```

### REPL Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/exit` | Exit |
| `/sessions` | List recent sessions |
| `/resume <id>` | Resume a previous session |

## Voice Mode (experimental)

```bash
reasoning-voice                   # wake word: "你好小奕"
reasoning-voice --no-wake         # skip wake word, start listening
reasoning-voice --debug           # verbose logging
```

### Flow

```
🎤 listening → ⚙️ working → 🔊 speaking → 🎤 listening
     you speak     agent acts     TTS replies
```

### Backends

- **Self-hosted** — STT (SenseVoice) + TTS (VoxCPM2 / Fish-Speech) servers
- **Cloud** — Step Fun API for ASR and TTS

## Tools

| Tool | Description |
|------|-------------|
| Bash | Execute shell commands |
| FileRead | Read files with offset/limit |
| FileWrite | Write/create files |
| FileEdit | Exact string replacement |
| Glob | File pattern search |
| Grep | Content search |
| WebFetch | HTTP GET |
| TodoWrite | Task list |
| AskUser | Question the user |

## Architecture

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

## Quality

```bash
ruff check src/
mypy src/
pytest tests/ -v
```

## License

AGPL-3.0 — see [LICENSE](LICENSE)
