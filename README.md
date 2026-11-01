# Voice Code · 语码

<p align="center">
  <img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/Python-≥3.12-green.svg" alt="Python">
  <img src="https://img.shields.io/badge/Tests-604%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Code%20Style-Ruff-000000.svg" alt="Code Style">
  <img src="https://img.shields.io/badge/Interaction-CLI%20%7C%20TUI%20%7C%20Voice%20%7C%20Desktop-orange.svg" alt="Interaction">
</p>

<p align="center">
  <b>Voice-First Autonomous Coding Agent</b><br>
  <i>Native support for modern Textual TUI, plain CLI REPL, full-duplex voice dashboard, Tauri desktop app, and hosted web demo. Features multi-agent supervision, 5-layer adaptive context compaction, layered persistent memory RAG, and production-grade security sandboxing.</i>
</p>

<p align="center">
  <b>🇬🇧 English</b>
  ·
  <a href="README.zh.md">🇨🇳 简体中文</a>
</p>

---

## 🌐 Live Demo & Resources

- **Public Web Demo**: [https://voice-code.yangguanxue.top/](https://voice-code.yangguanxue.top/) (gated preview in a controlled sandbox)
- **Repository**: [https://github.com/yangguan-xue/Voice-code](https://github.com/yangguan-xue/Voice-code)
- **License**: AGPL-3.0
- **Python**: ≥ 3.12

---

## 🌟 Key Features

### 1. Multi-Surface Interaction

- **🖥️ Modern Textual TUI (Default & Recommended)**
  - **Premium Aesthetic**: Pure black base (`#000000`) with refined grayscale layers and restrained warm red accents (`#df6a5c`).
  - **Structured Message Tree**: Streaming markdown, collapsible model thinking/reasoning blocks, and live tool invocation cards.
  - **Session History Sidebar**: Browse past sessions, quick resume, and create new sessions from the sidebar.
  - **Interactive Safety Dialog**: Approval prompts for high-risk operations with session and workspace persistence.
- **⚡ Plain CLI REPL**
  - Lightweight streaming output with dim reasoning traces and non-blocking interruption. Perfect for pipes, scripting, and headless environments.
- **🎙️ Full-Duplex Voice Dashboard**
  - **4-State Finite State Machine**: `sleeping` ➔ `listening` ➔ `working` ➔ `speaking`.
  - **Wake Word & VAD**: Wake word activation ("你好小奕", customizable, or `--no-wake` for continuous listening) with adaptive silence detection.
  - **Dual Voice Backends**:
    - **Cloud**: Step Fun API for real-time speech-to-speech interaction.
    - **Self-Hosted**: Local SenseVoice (STT) + VoxCPM2 / Fish-Speech (TTS).
  - **Full-Screen Dashboard**: Live audio volume meter, real-time STT transcripts, streaming agent output, and per-stage latency breakdown.
- **💻 Desktop Application (Tauri)**
  - Cross-platform desktop shell with optional bundling of an isolated Python runtime (no pre-installed Python needed).
- **🌐 Hosted Web Demo**
  - React + FastAPI web interface running in an isolated Docker sandbox with code diff inspection and image tool support.

### 2. Multi-Agent Supervision (Subagents & Supervisor)

- **CommandAssistant Supervision Loop**:
  - Closed-loop orchestration: **Dispatch ➔ Execute (Isolated Agent) ➔ Review (Independent Validation) ➔ Continue / Finish**.
- **Lifecycle & Queue Management**:
  - Dedicated agent delegation (`agent`) and task control tools (`task_list`, `task_get`, `task_stop`).

### 3. 5-Layer Adaptive Compaction Pipeline

Dynamically balances token budgets and critical context retention for long-running workflows:

| Layer | Trigger | Cost | Strategy |
| :--- | :--- | :--- | :--- |
| **MicroCompact** | Post-turn | 0 Token | Clears obsolete high-volume tool execution outputs |
| **SNIP** | Historical turns | 0 Token | Trims non-essential intermediate tool responses |
| **ContextCollapse** | Multi-turn reads | 0 Token | Collapses consecutive read-only turns into compact state snapshots |
| **AutoCompact** | Token threshold | 1 LLM Call | Generates high-fidelity semantic summaries and consensus state |
| **ReactiveCompact** | 413 / Overflow | 1 LLM Call | Emergency circuit-breaker that recovers from token overflow errors |

### 4. Permission Engine V2 & Security Sandbox

- **Dynamic 3-Tier Decisions**: `Allow` (whitelist), `Deny` (block), and `Ask` (interactive approval).
- **Bash Security Sandbox**: Dangerous command pattern blocking, resource throttling, and network policy restrictions.
- **Secrets Redaction**: Automatic scanning and masking of API keys, tokens, and private credentials.
- **MCP Trust & Audit Chain**: Explicit authorization for Model Context Protocol tools with an append-only audit trail.

### 5. Layered Persistent Memory RAG (Memory V3)

- **Dual-Storage Engine**: SQLite as the single source of truth; Milvus for dense vector + Chinese BM25 hybrid indexing.
- **Graduated Preference Extraction**: Staged rollout through `shadow` (silent evaluation) ➔ `review` (human approval) ➔ `automatic` (gated promotion).
- **Dead Letter Queue (DLQ)**: Failed background tasks and extraction workers route to a DLQ with CLI inspection and one-click replay.

### 6. Full-Stack Observability & Health

- **9-Panel Telemetry**: Real-time metrics across Agent Loop, LLM calls, Tools, Permissions, Sessions, Voice Latency, and RAG hits.
- **Privacy Gate**: Strict redaction guarantees that metrics and logs never store user code, credentials, or raw audio.
- **Unified Health Checks**: `uv run reasoning-status-sync` provides full-system `--json` status checks.

---

## 🏗️ Architecture

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        User Interaction Surfaces                       │
│   Textual TUI (Default)  │  CLI REPL (--plain)  │   Voice Dashboard    │
│   Sidebar + Thinking Tree│  Streaming text      │   4-State FSM + VAD  │
│   Tauri Desktop Shell    │  Web Demo (Sandbox)  │                      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Agent Runtime & Query Loop                         │
│  ┌───────────────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
│  │   CommandAssistant    │  │ Compaction (5L) │  │  Abort/Recovery  │  │
│  │   Supervise & Review  │  │ Micro/SNIP/Auto │  │  Cross-thread OS │  │
│  └───────────────────────┘  └─────────────────┘  └──────────────────┘  │
└───────────┬───────────────────────────────┬────────────────────────────┘
            │                               │
            ▼                               ▼
┌───────────────────────┐       ┌────────────────────────────────────────┐
│   Security & Policy   │       │          Tools & Subagents             │
│ ┌───────────────────┐ │       │ Core Tools:                            │
│ │ Permission Engine │ │       │   • bash (sandboxed) • file_read/write │
│ │ Allow / Deny / Ask│ │       │   • file_edit        • glob / grep     │
│ ├───────────────────┤ │       │   • web_fetch        • todo_write      │
│ │ Secrets Redaction │ │       │ Subagent Platform:                     │
│ │ Audit Log Chain   │ │       │   • agent / task_list / task_stop      │
│ └───────────────────┘ │       │ Web / Multimodal Tools:                │
│                       │       │   • image_gen / image_edit             │
│                       │       └────────────────────────────────────────┘
└───────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Layered Memory & Resilience                        │
│       SQLite (Source of Truth)  ◄── Sync/Rebuild ──►  Milvus (Dense)   │
│       Extraction Pipeline (Shadow/Review/Auto)     │  Resilient DLQ    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Built-in Tools

| Tool | Permission | Description |
| :--- | :--- | :--- |
| **`Bash`** | Ask (configurable) | Executes shell commands inside a sandbox with resource limits |
| **`FileRead`** | Auto-allow | Reads file content with range and offset parameters |
| **`FileWrite`** | Ask | Creates or overwrites files |
| **`FileEdit`** | Ask | Performs exact string replacement in files |
| **`Glob`** | Auto-allow | Matches filenames and directory paths via patterns |
| **`Grep`** | Auto-allow | Fast multi-threaded text search using ripgrep |
| **`WebFetch`** | Ask | Fetches content from web URLs and APIs |
| **`TodoWrite`** | Auto-allow | Tracks structured multi-step task progress |
| **`AskUser`** | Interactive | Prompts the user for clarification |
| **`Agent`** | Ask | Launches an isolated background subagent task |
| **`TaskList/Get/Stop`**| Auto-allow | Queries task queues, inspects logs, or terminates tasks |
| **`ImageGen/Edit`** | Ask | Generates or modifies images (integrated for Web Demo) |

---

## 🚀 Quick Start

### 1. Prerequisites

- **Python**: ≥ 3.12
- **Package Manager**: [uv](https://github.com/astral-sh/uv) (recommended)
- **Optional**: `ripgrep` for faster codebase search; microphone permissions for voice mode.

```bash
uv sync
```

### 2. Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Configure your model credentials in `.env`:

```ini
LLM_API_KEY=sk-your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-chat
```

Or configure multiple profiles with fallbacks in `models.toml`:

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

### 3. Launching Surfaces

#### 🖥️ Interactive TUI (Recommended)

```bash
uv run reasoning
# Or with a specific profile:
uv run reasoning --profile deepseek
```

#### ⚡ Plain CLI REPL

```bash
uv run reasoning --plain
```

#### 🎙️ Voice Dashboard

```bash
# Wake word mode ("你好小奕")
uv run reasoning-voice

# Direct continuous listening
uv run reasoning-voice --no-wake

# Using Step Fun cloud voice backend
uv run reasoning-voice --stepfun-key "your-stepfun-key"

# Minimal ANSI terminal mode
uv run reasoning-voice --plain-display
```

---

## 💻 Desktop & Web Demo

### Tauri Desktop Shell

```bash
cd desktop
pnpm install
pnpm test
pnpm build
```

Build a self-contained Windows runtime installer:

```powershell
pnpm tauri:build:windows:runtime
```

> **Note**: The runtime installer bundles an isolated Python runtime, allowing end users to run Voice Code without installing Python or `uv`.

### Hosted Web Demo

Frontend:

```bash
cd web
pnpm install
pnpm test -- --run
pnpm build
```

Backend:

```bash
uv run reasoning-web-demo --host 127.0.0.1 --port 8787
```

Production deployment configs are available in `deploy/web-demo/`.

---

## ⌨️ Commands & Shortcuts

### REPL / TUI Slash Commands

| Command | Surfaces | Description |
| :--- | :--- | :--- |
| `/help` | All | Display available slash commands |
| `/sessions` | All | List saved interactive sessions |
| `/resume <id>` | All | Resume a past session by ID |
| `/perm-rule [session\|workspace] <idx>` | All | Inspect effective permission rules |
| `/perm-delete [session\|workspace] <idx>`| All | Delete a specific permission rule |
| `/remember user <text>` | All | Save a global cross-project user preference |
| `/remember project <text>` | All | Save a project-specific constraint |
| `/memory candidates` | All | Inspect automatically extracted memory candidates |
| `/memory approve <id>` / `/memory reject <id>` | All | Approve or reject a memory candidate |
| `/exit`, `/quit` | All | Save state and exit |

### Keyboard Shortcuts

- **TUI**:
  - `Ctrl + C`: Interrupt active reasoning or tool execution.
  - `Ctrl + D`: Exit the application.
- **Voice Dashboard**:
  - `Space`: Pause or resume listening.
  - `Esc`: Interrupt active reasoning or TTS speech output.
  - `Q`: Quit voice dashboard.

---

## 🧪 Quality & Tests

Voice Code enforces strict quality standards with 600+ automated unit, integration, and fault-injection tests:

```bash
# Code style and linting
uv run ruff check src/

# Full test suite execution
uv run pytest tests/ -v
```

---

## ⚠️ Security & Public Repository Policy

1. **Local Execution**: Voice Code can execute shell commands and modify local files. Keep permission prompts (`Ask` mode) enabled and carefully review tool arguments before approving dangerous operations.
2. **Web Demo Sandbox**: The public web demo is hosted inside an isolated container sandbox designed for technology preview, not arbitrary cloud development.
3. **Public Repository Policy**: This repository is the public release surface. It excludes private reference implementations, local workspace artifacts, build caches, and sensitive credentials.

---

## 📄 License

AGPL-3.0. See [LICENSE](LICENSE).
