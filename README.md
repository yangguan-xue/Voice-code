# 语码 · Voice Code

Python-powered interactive coding agent with voice mode. A LangChain-based rewrite of the Claude Code CLI concept.

## Quick Start

```bash
pip install reasoning-agent
# or: uv sync

cp .env.example .env  # set LLM_API_KEY
reasoning              # interactive CLI
reasoning-voice        # voice mode
```

## Configuration

### API Key

Set your LLM API key in `.env`:

```bash
LLM_API_KEY=sk-xxx
```

### Model Profiles (`models.toml`)

Multiple model backends via profiles:

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

## Commands

### CLI Mode

```bash
reasoning                          # interactive REPL
reasoning --profile deepseek       # with model profile
reasoning --permission-mode bypass # skip confirmations
```

### Voice Mode (experimental)

```bash
reasoning-voice                    # wake word: "你好小奕"
reasoning-voice --no-wake          # skip wake word
reasoning-voice --debug            # verbose logging
```

Requires microphone access and STT/TTS services (self-hosted or Step Fun API).

### TUI Mode

```bash
reasoning-tui                      # Textual terminal UI
```

## Tools

The agent has 9 tools for filesystem and shell access:

| Tool | Description |
|------|-------------|
| Bash | Execute shell commands |
| FileRead | Read files with offset/limit |
| FileWrite | Write/create files |
| FileEdit | Exact string replacement |
| Glob | File pattern search |
| Grep | Content search (ripgrep) |
| WebFetch | HTTP GET |
| TodoWrite | Task list management |
| AskUser | Question the user |

## Voice Mode

Speech-driven development flow:

```
🎤 listening → ⚙️ working → 🔊 speaking → 🎤 listening
     you speak     agent acts     TTS replies
```

- VAD-based segmentation with RMS energy detection
- Wake word activation ("你好小奕")
- Optional Step Fun API for cloud STT/TTS
- Self-hosted VoxCPM2 / Fish-Speech backends

## Architecture

```
src/reasoning_agent/
├── cli.py / voice_cli.py / tui.py   # Entry points
├── agent/loop.py                     # Query loop
├── tools/                            # 9 tool implementations
├── compact/                          # Context compression
├── session/                          # Transcript persistence
├── voice/                            # Voice mode (STT/TTS/VAD)
├── llm/models.py                     # Model factory
├── permissions.py                    # Safety gates
├── prompts.py                        # System prompt
└── context.py                        # Project context
```

## Quality

```bash
ruff check src/
mypy src/
pytest tests/ -v       # 78+ tests
```

## License

MIT
