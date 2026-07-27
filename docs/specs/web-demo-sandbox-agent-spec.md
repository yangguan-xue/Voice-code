# Spec: Web Demo Sandbox Agent

## Assumptions
1. This is a controlled demo experience, not a public SaaS launch.
2. Users access the demo through a browser. The agent runtime runs on a server.
3. Demo users may write files, run tests, and inspect diffs, but only inside an isolated sandbox workspace created for that demo session.
4. The server never exposes its host filesystem, operator home directory, real project checkout, `.env`, SSH keys, model keys, or deployment secrets to demo sessions.
5. V0 uses an access code or invite token. No full login/account system is required yet.
6. V0 can use a small allowlisted template repo or demo repo. Arbitrary user GitHub repo cloning is out of scope unless explicitly added later.
7. Voice can be included later using browser microphone APIs, but the first Web demo should prove text chat + writable sandbox first.

## Objective
Build a Web version of the Codex-like desktop experience where other people can try the agent in a safe, writable demo sandbox.

The user should be able to:
- open a Web page;
- enter an access code;
- start a demo session;
- chat with the agent;
- let the agent inspect and modify a sandbox workspace;
- see streamed assistant output, tool progress, permissions, and final diff;
- reset or discard the sandbox without affecting other users or the server.

Success means:
- A demo user can complete a realistic code-editing task in the browser.
- All file writes happen inside a per-session sandbox.
- The server can enforce limits on time, disk, processes, network, and tool permissions.
- Demo sessions are disposable and auditable.
- A malicious or confused demo prompt cannot modify the host machine or another user's workspace.

## Tech Stack
Frontend:
- React + TypeScript + Vite.
- Reuse desktop UI components where possible:
  - conversation canvas;
  - Markdown/code rendering;
  - composer;
  - permission sheet;
  - session/sidebar patterns.

Backend:
- Python 3.12.
- Existing `voice_code` runtime and agent loop.
- WebSocket transport using existing `websockets` dependency for V0.
- Existing session/transcript modules.
- Existing worktree/sandbox primitives where possible.

Sandbox:
- V0 minimum: per-session Git worktree or copied template workspace under a controlled sandbox root.
- Stronger option: container per session.
- All sandbox roots live under one configured path such as `/srv/voice-code-demo/sandboxes`.
- Cleanup job removes expired sandboxes and transcripts.

## Commands
Development:

```bash
cd new
uv sync
uv run reasoning-web-demo --host 127.0.0.1 --port 8787 --sandbox-root ./.demo-sandboxes --template-repo ./examples/demo-repo
cd web
pnpm install
pnpm dev
```

Frontend verification:

```bash
cd new/web
pnpm test -- --run
pnpm lint
pnpm build
```

Backend verification:

```bash
cd new
uv run pytest tests/test_web_demo_server.py tests/test_web_demo_sandbox.py tests/test_web_demo_permissions.py -q
uv run ruff check src/voice_code/web_demo tests/test_web_demo_server.py tests/test_web_demo_sandbox.py tests/test_web_demo_permissions.py
```

Manual local smoke:

```bash
cd new
uv run reasoning-web-demo --host 127.0.0.1 --port 8787 --access-code dev-demo --sandbox-root ./.demo-sandboxes
```

Full gate when ready:

```bash
cd new
uv run pytest tests/ -v
cd web
pnpm build
```

## Project Structure

```text
web/
  src/
    app/
      WebDemoApp.tsx                 # browser app root
    features/
      conversation/                  # shared/copied desktop conversation UI
      composer/
      permissions/
      sandbox/
        SandboxHeader.tsx            # session status, expires in, reset
        DiffPanel.tsx                # changed files and patch preview
    lib/
      bridge/
        web-demo-client.ts           # websocket client
        protocol.ts                  # typed frontend contracts
    styles/
      web-demo.css

src/voice_code/
  web_demo/
    __init__.py
    cli.py                           # reasoning-web-demo
    protocol.py                      # request/response/event models
    server.py                        # websocket/http server
    runtime.py                       # session runtime manager
    sandbox.py                       # sandbox create/reset/cleanup
    permissions.py                   # web permission approver
    limits.py                        # resource/time/network policies
    audit.py                         # demo audit log

tests/
  test_web_demo_server.py
  test_web_demo_sandbox.py
  test_web_demo_permissions.py
  test_web_demo_limits.py

docs/specs/
  web-demo-sandbox-agent-spec.md
```

## Product Flow

### 1. Entry
User opens the Web demo URL.

V0 entry screen:
- product name;
- short warning: demo sandbox, expires automatically;
- access code input;
- `Start demo` button.

No marketing page. The first useful screen after access is the agent workspace.

### 2. Session Creation
After access code validation:
1. Backend creates a `demoSessionId`.
2. Backend creates an isolated sandbox workspace.
3. Backend bootstraps an agent runtime with `cwd=<sandbox workspace>`.
4. Frontend opens a WebSocket bound to that session.
5. Frontend shows workspace name, expiry, permission mode, and current model.

### 3. Chat
Chat behavior should match the desktop app:
- user messages on the right;
- model messages in the center column;
- Markdown and code blocks rendered;
- tool rows compact, not terminal spam;
- permission requests shown as explicit UI decisions;
- Enter sends.

### 4. Writable Sandbox
The agent may modify files inside the sandbox.

After each completed turn, backend emits:
- changed file list;
- summarized diff stats;
- optional patch text or diff preview;
- sandbox disk usage.

The user may:
- ask follow-up questions;
- request additional edits;
- reset the sandbox;
- download a patch;
- discard the session.

### 5. Session Expiry
Every session has a TTL, for example 30 minutes.

Frontend shows:
- `Expires in 24 min`;
- warning under 5 minutes;
- `Extend` only if enabled by server policy.

On expiry:
- running turn is interrupted;
- sandbox becomes read-only or deleted;
- WebSocket closes with a friendly reason;
- audit record is finalized.

## Architecture

```text
Browser
  |
  | HTTPS + WebSocket
  v
Web Demo Gateway
  |
  | validates access code, session token, rate limit
  v
Demo Session Manager
  |
  | creates sandbox, runtime, permission approver
  v
Agent Runtime
  |
  | tools constrained to sandbox root
  v
Sandbox Workspace
```

### Server Components

`WebDemoServer`
- Accepts WebSocket connections.
- Validates access code/session token.
- Routes requests to a session runtime.
- Emits stable UI events.

`DemoSessionManager`
- Creates sessions.
- Owns lifecycle state.
- Enforces one active turn per session.
- Tracks expiry and cleanup.

`SandboxManager`
- Creates workspace from template.
- Ensures path stays under sandbox root.
- Provides reset/discard.
- Produces diff and patch.

`WebPermissionApprover`
- Converts agent permission requests to Web UI events.
- Waits for user decision with timeout.
- Denies on disconnect/expiry.

`LimitsPolicy`
- Defines max sessions, TTL, turn timeout, max output size, disk budget, process budget, and network policy.

## Sandbox Model

### V0 Sandbox Options
Choose one implementation for V0:

1. Worktree/copy sandbox:
   - create a Git worktree or copy from template repo;
   - run agent with `cwd` set to sandbox path;
   - configure tools to reject paths outside sandbox root.

2. Container sandbox:
   - start one container per demo session;
   - mount template workspace;
   - run agent inside container;
   - enforce CPU/memory/disk/network at container layer.

V0 can start with worktree/copy sandbox if:
- server is access-code protected;
- shell/network policies are strict;
- all path access is rooted and validated;
- no host secrets are mounted.

Public demo should move to container sandbox before opening to strangers.

### Sandbox Root Rules
- Sandbox root must not be `/`, home directory, repo root, or any symlink.
- Every session path must be a direct child of configured sandbox root.
- Any resolved file path used by tools must stay inside session sandbox.
- Symlink escape attempts must be rejected.
- Cleanup must never follow symlinks.

### Template Repo
Template repo options:
- small demo Python/TypeScript project;
- includes tests that run quickly;
- no secrets;
- no large dependencies;
- predictable tasks for demo prompts.

## Protocol Contracts

### Authentication

```json
{
  "type": "request",
  "id": "req-1",
  "method": "demo.session.create",
  "payload": {
    "accessCode": "dev-demo"
  }
}
```

Response:

```json
{
  "type": "response",
  "id": "req-1",
  "ok": true,
  "payload": {
    "sessionId": "demo_abc123",
    "sessionToken": "opaque-session-token",
    "expiresAt": "2026-07-25T07:30:00Z",
    "workspaceLabel": "demo-python-app"
  }
}
```

### Turn Start

```json
{
  "type": "request",
  "id": "turn-1",
  "method": "turn.start",
  "sessionToken": "opaque-session-token",
  "payload": {
    "text": "给这个项目加一个健康检查接口",
    "permissionMode": "default"
  }
}
```

### Permission Request Event

```json
{
  "type": "event",
  "event": "permission.request",
  "sessionId": "demo_abc123",
  "turnId": 1,
  "payload": {
    "requestId": "perm_1",
    "toolName": "file_write",
    "reason": "Agent wants to modify app.py",
    "filePreview": "app.py"
  }
}
```

### Sandbox Diff Event

```json
{
  "type": "event",
  "event": "sandbox.diff.updated",
  "sessionId": "demo_abc123",
  "payload": {
    "changedFiles": [
      { "path": "app.py", "status": "modified", "additions": 12, "deletions": 2 }
    ],
    "patchAvailable": true
  }
}
```

## Security Boundaries

### Access Control
- V0 requires access code.
- Access code is configured server-side.
- Session token is random, opaque, and scoped to one demo session.
- WebSocket messages without valid session token are rejected.
- Rate limits apply per IP/access code/session.

### Tool Restrictions
Allowed in V0:
- read files inside sandbox;
- grep/glob inside sandbox;
- edit/write files inside sandbox with permission;
- run allowlisted test commands;
- run safe Git status/diff commands.

Denied by default:
- reading outside sandbox;
- writing outside sandbox;
- network egress from agent tools unless explicitly allowlisted;
- `git push`, deploy commands, cloud CLIs;
- package manager install commands unless explicitly enabled;
- long-running background processes;
- commands that inspect `/Users`, `/home`, `/etc`, SSH keys, env files, or process table.

### Secrets
- Provider API keys live only in server process config.
- API keys are never written into sandbox env files.
- Agent output is scanned for obvious secret leaks before sending to browser if feasible.
- Audit logs redact access code, tokens, API keys, and environment variables.

### Resource Limits
Recommended V0 defaults:
- session TTL: 30 minutes;
- max active sessions: configurable;
- max turn time: 5 minutes;
- max sandbox disk: 200 MB;
- max transcript chars returned per event: bounded;
- max tool output preview: bounded;
- max parallel turns per session: 1;
- max concurrent sessions per IP/access code: configurable.

## UI Requirements

### Layout
Use the desktop conversation layout as the base:
- left session/sidebar optional for V0;
- main conversation canvas;
- fixed composer at bottom;
- diff panel available as right drawer or bottom panel.

Avoid:
- marketing hero as first screen after access;
- terminal log wall;
- raw JSON protocol display;
- exposing internal server paths.

### Demo Header
Show:
- sandbox label;
- expiry countdown;
- permission mode;
- reset/discard controls;
- changed file count.

### Diff Panel
V0 should show:
- changed files list;
- additions/deletions;
- patch preview;
- download patch button if practical.

No direct merge to a real repo in V0.

## Code Style
Prefer explicit sandbox/session naming:

```python
@dataclass(slots=True)
class DemoSession:
    session_id: str
    sandbox_path: Path
    expires_at: datetime
    transcript_path: Path

    def resolve_sandbox_path(self, user_path: str) -> Path:
        resolved = (self.sandbox_path / user_path).resolve()
        resolved.relative_to(self.sandbox_path.resolve())
        return resolved
```

Frontend mode names should be product-facing, not infrastructure-facing:

```ts
type DemoSessionStatus =
  | "creating"
  | "ready"
  | "running"
  | "waiting_permission"
  | "expired"
  | "failed";
```

Conventions:
- Validate all incoming WebSocket payloads.
- Use structured error codes.
- Keep protocol models separate from UI components.
- Never expose absolute server paths to the browser; send sandbox-relative paths.
- Prefer short, Chinese user-facing copy for this product version.

## Testing Strategy

### Backend Unit Tests
- access code required;
- invalid session token rejected;
- sandbox path escape rejected;
- symlink escape rejected;
- permission request times out safely;
- disconnect denies pending permission;
- session expiry interrupts running turn;
- cleanup removes expired sandbox without following symlinks;
- diff reports sandbox-relative changed files only.

### Backend Integration Tests
- create session -> run turn -> stream events -> diff emitted;
- two users get separate sandboxes;
- writes in one sandbox do not affect another;
- reset sandbox restores template;
- denied permission prevents file mutation;
- tool output over limit is truncated.

### Frontend Tests
- access screen validates empty code;
- chat renders streamed user/assistant/tool events;
- permission sheet resolves allow/deny;
- diff panel shows changed files;
- expiry state disables composer;
- reset/discard actions confirm before calling backend.

### Manual QA
1. Start server with a local template repo.
2. Open Web demo.
3. Enter access code.
4. Ask agent to modify a file.
5. Approve permission.
6. Confirm changed file appears in diff panel.
7. Ask agent to run tests.
8. Reset sandbox and confirm diff clears.
9. Start second browser session and confirm isolation.
10. Wait for expiry and confirm session closes cleanly.

## Deployment Model

### V0 Internal Demo
Acceptable:
- one VM;
- reverse proxy with HTTPS;
- access code;
- strict sandbox root;
- no arbitrary repo clone;
- no public indexing;
- short retention.

Required:
- process supervisor;
- logs and metrics;
- sandbox cleanup;
- disk usage alert;
- max concurrent sessions;
- manual kill switch.

### Not Yet Public-Safe
Before public launch, add:
- per-session containers or stronger OS sandboxing;
- robust auth;
- abuse detection;
- stronger egress policy;
- persistent audit trail;
- quota/billing or hard capacity limits;
- privacy/data retention policy shown to users.

## Boundaries
- Always: every demo session gets its own sandbox workspace.
- Always: sandbox writes are allowed only inside that workspace.
- Always: reject resolved paths outside sandbox root.
- Always: require access code in V0.
- Always: make write permissions explicit in UI.
- Always: bound session time, tool output, disk usage, and turn duration.
- Always: show diffs using sandbox-relative paths.
- Always: delete or archive expired sandboxes according to retention policy.
- Ask first: enabling arbitrary GitHub repo cloning.
- Ask first: enabling package installs or network egress from tools.
- Ask first: allowing users to upload files.
- Ask first: storing transcripts longer than the demo retention window.
- Ask first: exposing the demo publicly beyond invited users.
- Never: mount host home directory into a demo sandbox.
- Never: expose provider keys, environment variables, SSH keys, or server paths.
- Never: run demo user commands in the app repo or host project checkout.
- Never: allow one user's session token to access another sandbox.
- Never: auto-deploy demo-generated code.

## Success Criteria
- Web demo can be started locally with one backend command and one frontend command.
- A user can create a sandbox session with an access code.
- Agent can read and write files inside the sandbox after permission approval.
- Browser shows streamed conversation and changed file diff.
- Path escape and symlink escape tests pass.
- Two sessions are isolated from each other.
- Expired sessions are interrupted and cleaned up.
- No host absolute paths or secrets appear in normal browser UI events.

## Open Questions
1. Should V0 frontend live in a new `web/` folder, or share/build from `desktop/src` with a web-specific entry point?
2. Should the first sandbox use Git worktree from a template repo, or a copied template directory?
3. What demo template project should we ship first?
4. Should package install commands be blocked entirely in V0?
5. What default TTL and max disk budget do you want for demos?
6. Should demo users be able to download patch files?
7. Should voice be added in V0.1 after text demo works, or included in V0?
