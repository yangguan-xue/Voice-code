# Spec: Mac Desktop Technical Architecture

## Assumptions
1. The desktop app is a new surface over the existing Python agent, not a rewrite of the agent in TypeScript or Rust.
2. V0 runs only on the user's machine and binds any local bridge to loopback.
3. The frontend talks to a single local agent bridge process owned by the desktop app.
4. TUI, CLI and voice mode remain supported during and after the desktop work.
5. Existing `voice.AgentBridge` is a useful prototype but should be generalized before desktop depends on it directly.

## Objective
Create a technical path for a Codex-like macOS page while preserving the current Python architecture.

The implementation should:
- Add a desktop UI project under `desktop/`.
- Add a typed local bridge between the UI and Python runtime.
- Expose sessions, turns, streaming agent events, permission requests and interruption through stable contracts.
- Keep all core agent execution in `voice_code`.
- Make the first implementation shippable in small phases.

## Tech Stack
Python side:
- Python `>=3.12`
- Existing `voice_code` modules:
  - `runtime.bootstrap_runtime`
  - `agent.loop.agent_loop`
  - `agent.types.AgentEvent`
  - `permissions.PermissionContext`
  - `session.manager`
  - `session.transcript`
  - `session.state`
  - `subagents.events`
- Local bridge transport: WebSocket JSON messages over `127.0.0.1`, or an equivalent stdio transport if Tauri sidecar constraints make WebSocket harder.

Desktop side:
- Tauri macOS shell, version pinned during implementation.
- React + TypeScript + Vite.
- CSS variables and component CSS modules or plain CSS.
- lucide-react for icons.
- Test runner: Vitest plus Testing Library.
- Browser verification: Playwright or Tauri-compatible WebView smoke tests.

## Commands
Existing verification:

```bash
cd new
uv run ruff check src/
uv run pytest tests/ -v
```

Target backend commands:

```bash
cd new
uv run reasoning-desktop-bridge --workspace "$PWD" --profile deepseek --host 127.0.0.1 --port 38671
uv run pytest tests/test_desktop_bridge.py tests/test_desktop_sessions.py tests/test_desktop_permissions.py -v
```

Target frontend commands:

```bash
cd new/desktop
pnpm install
pnpm dev
pnpm test
pnpm lint
pnpm build
pnpm tauri dev
pnpm tauri build
```

Target full smoke:

```bash
cd new
uv run pytest tests/test_desktop_bridge.py -v
cd desktop
pnpm test
pnpm build
```

## Project Structure

```text
new/
  desktop/
    package.json
    vite.config.ts
    tsconfig.json
    src/
      app/
        App.tsx
        AppShell.tsx
        use-app-store.ts
      components/
        IconButton.tsx
        Tooltip.tsx
      features/
        conversation/
          ConversationCanvas.tsx
          TurnView.tsx
          ToolEventRow.tsx
          conversation-reducer.ts
          types.ts
        composer/
          Composer.tsx
          PromptTextArea.tsx
          ComposerContextBar.tsx
        permissions/
          PermissionSheet.tsx
          permission-reducer.ts
        sessions/
          SessionSidebar.tsx
          SessionGroup.tsx
          session-types.ts
        workspace/
          WorkspaceLabel.tsx
        runtime/
          RuntimeBadges.tsx
      lib/
        bridge/
          client.ts
          protocol.ts
          event-router.ts
        design-tokens.ts
      styles/
        tokens.css
        app.css
    src-tauri/
      tauri.conf.json
      src/
        main.rs
        sidecar.rs
  src/voice_code/
    desktop/
      __init__.py
      bridge.py              # session runtime manager and event fanout
      protocol.py            # Pydantic contracts for UI messages
      server.py              # local transport entrypoint
      permissions.py         # UI approver bridge
      sessions.py            # session list/resume helpers
    voice/
      agent_bridge.py        # can delegate to shared bridge after refactor
  tests/
    test_desktop_bridge.py
    test_desktop_sessions.py
    test_desktop_permissions.py
```

## Runtime Design

### 1. Shared Bridge Layer
Create a general Python bridge that can be used by desktop and, later, voice:
- Own one `RuntimeBootstrap` per active workspace/session.
- Maintain `resume_messages` by reading from `TranscriptWriter`.
- Serialize turns per session with an async lock.
- Keep an `AbortSignal` per active turn.
- Forward every `AgentEvent` to subscribers.
- Route permission requests to a pending UI approval queue.

Do not copy the large `AgentScreen` class into desktop. Extract the reusable pieces:
- session bootstrap from `runtime.py`
- streaming execution from `agent_loop`
- event shaping from `agent.types`
- permission request behavior from `tui_permission_dialog.py`
- session listing from `session.manager`

### 2. Transport
Use a typed message envelope.

```json
{
  "id": "req_123",
  "type": "request",
  "method": "turn.start",
  "payload": {
    "sessionId": "20260723-121314-abcd",
    "text": "整理一下这个项目"
  }
}
```

Response:

```json
{
  "id": "req_123",
  "type": "response",
  "ok": true,
  "payload": {
    "turnId": 1
  }
}
```

Event:

```json
{
  "type": "event",
  "event": "agent.text.delta",
  "sessionId": "20260723-121314-abcd",
  "turnId": 1,
  "payload": {
    "content": "我先看项目结构。"
  }
}
```

### 3. Methods
V0 request methods:
- `app.bootstrap`: returns bridge version, cwd, profile, model, permission mode and active session.
- `workspace.open`: bootstraps runtime for a path.
- `session.list`: returns grouped recent sessions.
- `session.create`: creates a fresh session in the current workspace.
- `session.resume`: loads transcript records and runtime sidecar state.
- `turn.start`: runs one user prompt.
- `turn.interrupt`: triggers the active `AbortSignal`.
- `permission.resolve`: resolves a pending permission request.
- `config.get`: returns models/profiles and default permission mode.

Deferred methods:
- `session.delete`
- `session.export`
- `task.list`
- `task.stop`
- `goal.start`
- `voice.start`
- `memory.candidates`

### 4. Events
Map Python events to UI events without leaking Python enum names into React components.

| Python source | UI event | Notes |
|---|---|---|
| `EventType.TEXT` | `agent.text.delta` | Append to visible assistant text. |
| `EventType.REASONING` | `agent.reasoning.delta` | Render as live thinking. |
| `EventType.TOOL_CALL` | `agent.tool.call` | Create or update tool row. |
| `EventType.TOOL_RESULT` | `agent.tool.result` | Complete tool row with preview. |
| `EventType.ERROR` | `agent.error` | Render inline error or recoverable status. |
| `EventType.FINISH` | `agent.turn.finish` | Mark turn complete and flush buffers. |
| `PermissionRequest` | `permission.request` | Open permission sheet and pause execution. |
| `TaskEvent` | `task.event` | Deferred for V0 if subagent view is not implemented. |
| session state save | `session.updated` | Refresh sidebar title/time. |

### 5. Permission Bridge
Implement a desktop approver equivalent to the TUI pending permission flow:
- Python creates a `PermissionRequest`.
- Bridge emits `permission.request` with a generated `requestId`.
- Agent execution waits on an async future or thread event.
- UI sends `permission.resolve`.
- Python returns `PermissionDecision` to the permission engine.
- If the UI disconnects, default to deny with a clear message.

Remember scopes:
- `allow once`: no stored rule.
- `session`: update session rules and persist sidecar state.
- `workspace`: update workspace rules using existing permission helpers.

### 6. Session And Transcript Loading
Desktop session list should use existing helpers:
- `list_session_summaries`
- `group_session_summaries`
- `load_session_state`
- `TranscriptReader`

The UI needs a renderable transcript shape. Add a Python adapter that converts stored LangChain messages into a stable DTO:

```python
class DesktopTurn(BaseModel):
    id: str
    user_text: str = ""
    entries: list[DesktopTurnEntry] = []
    status: str = "completed"
```

V0 may render historical turns with less detail than live turns, but it must not lose user and assistant text.

## Frontend State Model
Use a small explicit store:
- `workspace`: current path, label, git branch.
- `sessions`: groups, active session id, loading/error.
- `conversation`: ordered turns, live buffers, sticky follow state.
- `runtime`: model, profile, permission mode, busy phase, tool count.
- `permissions`: pending request, selected action, submission state.
- `composer`: text, attachments placeholder, disabled/busy state.

Streaming event reducer target:

```ts
export function conversationReducer(
  state: ConversationState,
  event: BridgeEvent,
): ConversationState {
  switch (event.event) {
    case "agent.text.delta":
      return appendAssistantText(state, event);
    case "agent.tool.call":
      return upsertToolCall(state, event);
    case "agent.turn.finish":
      return finishTurn(state, event);
    default:
      return state;
  }
}
```

## Security And Privacy
Even without login, the local bridge needs defensive boundaries:
- Bind only to `127.0.0.1`.
- Use a random per-launch bridge token shared from Tauri sidecar to frontend.
- Reject requests without the token.
- Reject non-local origins where applicable.
- Do not write API keys into frontend storage.
- Redact secrets before emitting tool args/results when existing redaction helpers apply.
- Default permission request timeout/disconnect behavior to deny.

## Implementation Plan

### Phase 1: Contract And Mock UI
- Add `desktop/` React app.
- Build static shell matching the product spec.
- Implement event reducer with fixture events.
- No Python bridge yet.
- Verify layout and accessibility.

### Phase 2: Python Bridge
- Add `src/voice_code/desktop/protocol.py`.
- Add bridge runtime manager around `bootstrap_runtime` and `agent_loop`.
- Add fake transport tests with in-memory event sink.
- Add `reasoning-desktop-bridge` script entry.

### Phase 3: Live Turn Streaming
- Connect frontend bridge client to Python.
- Implement `app.bootstrap`, `session.create`, `turn.start`, `turn.interrupt`.
- Render text, reasoning, tool events, errors and finish.

### Phase 4: Sessions And Resume
- Implement `session.list` and `session.resume`.
- Render grouped sidebar from transcript summaries.
- Load historical transcript into conversation canvas.

### Phase 5: Permissions
- Implement desktop approver.
- Add permission sheet UI.
- Persist session/workspace remember scopes.
- Test disconnect and denial behavior.

### Phase 6: Mac Packaging
- Launch Python bridge as app-owned sidecar.
- Add app icon/name/build config.
- Produce a local `.app` build.
- Document development and release commands.

## Code Style
Python:
- Keep protocol schemas in Pydantic models.
- Keep transport code thin; business behavior belongs in bridge/session helpers.
- Avoid Textual imports in `voice_code.desktop`.
- Use async APIs end to end where possible.

Example:

```python
class BridgeEvent(BaseModel):
    type: Literal["event"] = "event"
    event: str
    session_id: str
    turn_id: int | None = None
    payload: dict[str, object] = Field(default_factory=dict)
```

TypeScript:
- Protocol types mirror Python DTO names.
- Reducers are pure and tested with fixture events.
- Components do not parse raw bridge payloads directly.
- Use semantic CSS classes and design tokens.

Example:

```ts
export type BridgeEvent =
  | AgentTextDeltaEvent
  | AgentReasoningDeltaEvent
  | AgentToolCallEvent
  | AgentToolResultEvent
  | AgentTurnFinishEvent
  | PermissionRequestEvent;
```

## Testing Strategy
Python unit tests:
- Bridge bootstrap creates runtime with temp workspace.
- `turn.start` emits ordered text/tool/finish events for a fake agent runner.
- `turn.interrupt` triggers abort.
- Permission request blocks until `permission.resolve`.
- UI disconnect denies pending permission.
- Session list groups summaries by project path.

Frontend tests:
- Conversation reducer handles out-of-order safe updates.
- Composer submit and interrupt buttons call bridge client correctly.
- Permission sheet traps focus and returns the selected decision.
- Sidebar renders empty, loading and grouped states.

Integration tests:
- Start bridge on a random local port.
- Connect a test client.
- Create a session and run a stubbed turn.
- Confirm transcript file is created and resume returns visible messages.

Manual QA:
- `pnpm tauri dev` launches the app.
- App opens with no login.
- New task appears in sidebar.
- A prompt streams live output.
- Permission modal resolves a write request.
- Interrupt changes running state.
- Relaunch resumes the previous session.

## Boundaries
Always:
- Keep desktop-specific code isolated from TUI.
- Keep local bridge protocol typed and versioned.
- Deny unresolved permissions on disconnect.
- Preserve existing transcript/session files.
- Run Python and frontend focused tests before full gates.

Ask first:
- Adding FastAPI, Electron, Tailwind, shadcn/ui, SQLite schema changes, or any non-trivial dependency.
- Changing how transcripts are stored.
- Changing default permission mode.
- Making the bridge reachable outside loopback.
- Shipping auto-update or cloud features.

Never:
- Import Textual widgets into desktop bridge code.
- Execute tools directly from TypeScript.
- Store secrets in the frontend.
- Skip permission checks for desktop turns.
- Break `uv run reasoning`, `uv run reasoning-tui`, or `uv run reasoning-voice`.

## Success Criteria
- The technical path is clear enough to start Phase 1 without more architecture discovery.
- The first live desktop turn uses the existing Python agent loop.
- UI can handle all core `AgentEvent` types.
- Permission flow is as safe as the current TUI flow.
- Session history remains compatible with current CLI/TUI transcripts.
- The work can be split into focused PRs.

## Open Questions
1. Should transport be WebSocket JSON over loopback, or Tauri sidecar stdio JSON-RPC?
2. Should the Python bridge live under `voice_code.desktop`, or should we first extract a generic `voice_code.agent_bridge` used by voice and desktop?
3. Should V0 include only one active workspace, or allow switching among recent workspaces in one running app?
4. Should historical tool results render from raw LangChain transcript records, or do we need richer transcript records for UI replay?
5. Should desktop V0 pin to Tauri, or do we want an Electron prototype for speed before native packaging?
