# Voice Code · 语码

Voice Code is a local-first coding agent with CLI, TUI, voice, desktop, and a
hosted web demo surface.

- Public web demo: https://voice-code.yangguanxue.top/
- Repository: https://github.com/yangguan-xue/Voice-code
- License: AGPL-3.0
- Python: 3.12+

## What You Can Try

| Surface | Status | Notes |
| --- | --- | --- |
| CLI / TUI | Ready for local development | Run with `uv run reasoning` |
| Voice mode | Experimental | Supports wake word, STT, agent loop, and TTS |
| Desktop app | Demo-ready packaging path | Tauri shell under `desktop/` |
| Web demo | Deployed preview | Controlled sandbox, invite-code gated |

## Quick Start

```bash
uv sync
cp .env.example .env
uv run reasoning
```

Plain CLI:

```bash
uv run reasoning --plain
```

Voice mode:

```bash
uv run reasoning-voice
```

## Configuration

Set a model profile in `.env` or `models.toml`:

```ini
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-v4-pro
```

`models.toml` supports multiple OpenAI-compatible profiles.

## Desktop

```bash
cd desktop
pnpm install
pnpm test
pnpm build
```

Windows runtime installer build:

```powershell
pnpm tauri:build:windows:runtime
```

The runtime installer bundles an app-owned Python runtime during packaging, so
end users do not need to install Python or `uv` just to launch the desktop app.
They still need valid model configuration, and Git is required for Git-backed
project workflows.

## Web Demo

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

Deployment templates live in `deploy/web-demo/`.

## Project Layout

```text
src/voice_code/      Python agent runtime
desktop/             Tauri desktop shell
web/                 React web demo frontend
deploy/web-demo/     Nginx/systemd/Docker demo deployment templates
tools/               Release packaging helpers
tests/               Python test suite
docs/                Public architecture and release notes
```

## Safety

Voice Code can execute shell commands and edit files. Keep permission prompts on
for normal use, review tool requests before approving them, and do not run the
agent in sensitive directories unless you trust the model profile and prompt.

The public web demo is a constrained sandbox. It is designed for project
preview, not for arbitrary hosted development.

## Public Repository Policy

This repository is the public release surface. It excludes private reference
code, local workspaces, generated runtime bundles, virtual environments, build
outputs, and credentials.

## License

AGPL-3.0. See [LICENSE](LICENSE).
