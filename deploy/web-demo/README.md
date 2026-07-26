# Web Demo Deployment

This folder contains a minimal Linux deployment template for the Voice Code Web Demo.

## Layout

- `voice-code-web-demo.service`: systemd unit for the Python WebSocket backend.
- `nginx-voice-code-web-demo.conf`: nginx site that serves the built frontend and proxies `/ws-demo`.
- `Dockerfile.sandbox`: Docker image used for Linux-level shell isolation in the demo.

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
  --allowed-origin http://118.25.44.28
```

The backend reads `REASONING_WEB_DEMO_ACCESS_CODE` from `.env`.
