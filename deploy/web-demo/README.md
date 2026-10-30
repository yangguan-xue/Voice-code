# Web Demo Deployment

This folder contains a minimal Linux deployment template for the Voice Code Web Demo.

## Layout

- `voice-code-web-demo.service`: systemd unit for the Python WebSocket backend.
- `nginx-voice-code-web-demo.conf`: nginx site that serves the built frontend and proxies `/ws-demo`.
- `Dockerfile.sandbox`: Docker image used for Linux-level shell isolation in the demo.

## Tenant model environment variables（2026-10-22 起）

访问码从"全站共享一个"升级为"每租户一个"。`.env` 需要：

```bash
# 必填：租户访问码 csv，key 同时是 tenant_id 与用户输入的访问码
REASONING_WEB_DEMO_ACCESS_CODES="alice=Alice,bob=Bob"
# 可选：租户数据根目录（web-demo.db + 各租户 transcript），默认 <sandbox-root>/_data
REASONING_WEB_DEMO_DATA_ROOT=/home/ubuntu/voice-code-demo/data
# 可选：热 runtime 空闲回收阈值（秒，默认 1800）。会话本身永不过期，
# 回收只释放内存，transcript/sandbox 永久保留，下条消息自动冷启动续接。
REASONING_WEB_DEMO_IDLE_TIMEOUT_SECONDS=1800
REASONING_WEB_DEMO_MAX_RUNTIMES=20
```

旧的 `REASONING_WEB_DEMO_ACCESS_CODE` / `REASONING_WEB_DEMO_TTL_SECONDS` /
`REASONING_WEB_DEMO_MAX_SESSIONS` 已废弃；游客模式（共享码直接开临时会话）已下线。

**迁移注意**：部署本版本后，旧游客的临时沙箱目录（`<sandbox-root>/demo_*`）
不再被引用，可手动清理。租户数据在：
- `<sandbox-root>/tenants/<tenant_id>/workspace/`（持久工作区，每租户配额 256MB）
- `<data-root>/tenants/<tenant_id>/transcripts/<conversation_id>.jsonl`（对话记录，永久保留）
- `<data-root>/web-demo.db`（租户/token/会话索引，SQLite）

## Expected paths

- App checkout: `/home/ubuntu/workspace/workspace/voice-code`
- Frontend publish directory: `/var/www/voice-code-web-demo`
- Sandbox root: `/home/ubuntu/voice-code-demo/sandboxes`
- Backend listen address: `127.0.0.1:8787`
- Default sandbox image tag: `python:3.13-slim`
- Optional richer sandbox image tag: `voice-code-web-demo-sandbox:latest`
- Nginx rate-limit include: `/etc/nginx/conf.d/voice-code-web-demo-rate-limit.conf`

## Default Linux shell sandbox

If the host already has `python:3.13-slim`, the web demo can enable Linux-level shell isolation without any extra build step.

## Optional richer sandbox image

Build this image if you want the sandbox to also include `bash`, `git`, `ripgrep`, and `pytest`:

```bash
cd /home/ubuntu/workspace/workspace/voice-code
sudo docker build -f deploy/web-demo/Dockerfile.sandbox -t voice-code-web-demo-sandbox:latest .
```

Then set:

```bash
REASONING_WEB_DEMO_SANDBOX_IMAGE=voice-code-web-demo-sandbox:latest
```

The web demo only enables the `bash` tool when:

- the backend is running on Linux
- `docker` is available
- the sandbox image exists locally

Otherwise the demo falls back to file-only tools.

## Security hardening notes

- App-layer session creation is rate-limited per client IP.
- Sandbox downloads are capped to small source files only.
- Workspace storage and file-count limits are enforced for demo-safe file editing.
- The demo `bash` tool runs inside Docker with no network, a read-only root filesystem, tmpfs scratch space, and CPU/memory/pid limits.
- Shell commands that reference obvious host paths, create symlinks, or invoke interpreter runtimes such as `python`, `node`, `perl`, `ruby`, or `php` are blocked before execution.
- The sample nginx config expects the companion rate-limit include file in `conf.d/`.

## Current shell boundary

As of July 26, 2026, the public Web Demo shell boundary is intentionally conservative:

- Allowed: workspace-local shell discovery and simple file-oriented commands such as `find`, `rg`, and bounded writes inside `/workspace`.
- Blocked: direct paths outside the workspace, symlink escape attempts, interpreter execution, and network egress tools.
- Result: normal project inspection works, but the demo shell is not a general-purpose Linux shell.

## HTTPS

Trusted HTTPS needs a dedicated hostname and certificate. Do not replace an unrelated existing TLS vhost just to put the demo behind HTTPS.

## Example backend launch

```bash
uv run reasoning-web-demo \
  --host 127.0.0.1 \
  --port 8787 \
  --sandbox-root /home/ubuntu/voice-code-demo/sandboxes \
  --template-repo /home/ubuntu/workspace/workspace/voice-code/examples/demo-repo \
  --allowed-origin https://voice-code.yangguanxue.top
```

The backend reads `REASONING_WEB_DEMO_ACCESS_CODES` from `.env`.

## Transport

`reasoning-web-demo` uses the FastAPI/uvicorn transport (`src/voice_code/web_demo/app.py`).
The WebSocket endpoint is served on `/`, `/ws`, and `/ws-demo`; nginx proxies
`/ws-demo` and the REST API under `/api/` to the same backend.

## REST API

The FastAPI transport also exposes REST endpoints; nginx proxies `/api/` to the
backend with a 16 MB body limit (image uploads):

- `GET /api/health` - liveness probe
- `POST /api/auth/login` - exchange an access code for a tenant token
- `POST /api/auth/logout` - revoke the current tenant token
- `GET /api/auth/me` - restore the current tenant, requires a Bearer token
- `GET /api/conversations` - list the tenant's conversations
- `GET /api/conversations/{id}/messages` - replay conversation history
- `POST /api/conversations/new` - start a conversation
- `POST /api/conversations/{id}/activate` - switch the active conversation
- `POST /api/conversations/current/files?path=...` - upload a file into the sandbox,
  requires a Bearer token
