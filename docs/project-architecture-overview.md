# Voice Code Architecture Overview

Voice Code (语码) is a local-first coding agent with CLI, TUI, voice, desktop,
and web demo surfaces. The public package lives under `src/voice_code`.

## Runtime Surfaces

- `reasoning` starts the default interactive terminal experience.
- `reasoning --plain` starts the plain CLI REPL.
- `reasoning-voice` starts the voice dashboard.
- `desktop/` contains the Tauri desktop shell.
- `web/` contains the browser demo frontend.
- `deploy/web-demo/` contains the Linux deployment template for the public web demo.

## Python Package Layout

```text
src/voice_code/
├── agent/             # streaming agent loop and event contracts
├── tools/             # shell, file, search, todo, and user-prompt tools
├── permissions.py     # tool permission policy
├── session/           # transcript persistence and resume
├── compact/           # context compaction
├── subagents/         # delegated background task runtime
├── voice/             # wake word, STT, TTS, and voice orchestration
├── desktop/           # local desktop bridge
├── desktop_voice/     # local desktop voice bridge
├── web_demo/          # sandboxed web demo backend
├── memory/            # optional local memory/RAG services
└── telemetry/         # bounded logs, metrics, and health checks
```

## Safety Model

Voice Code executes tool calls on the user's machine, so the public build keeps
permission checks, workspace boundaries, transcript durability, and web demo
sandboxing as first-class runtime concerns.

The web demo is a controlled sandbox rather than a general hosted coding
environment. Its backend limits command execution, storage, session lifetime,
and downloadable files.

## Release Hygiene

The public repository intentionally excludes private reference code, local
workspace artifacts, model credentials, virtual environments, desktop build
outputs, and web build outputs. Release builds regenerate generated artifacts
from source.
