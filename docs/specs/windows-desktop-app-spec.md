# Spec: Windows Desktop App

## Assumptions
1. This spec targets the Windows desktop app for the existing Tauri + React desktop surface, not a separate Electron app or a Windows-only rewrite.
2. The first supported desktop target is native Windows 10/11, not WSL. WSL may be used by advanced users later, but it is not required for the support claim.
3. The React UI, bridge protocol, conversation reducer, permission sheet, and session sidebar should be reused from `desktop/` with minimal platform-specific branches.
4. Python remains the agent runtime. Windows desktop work must not rewrite `voice_code` in Rust or TypeScript.
5. V0 may require users to have Python 3.12, `uv`, and Git installed. Bundling Python or `uv` into the installer is a later packaging phase.
6. Existing macOS behavior must keep working. Windows branches belong at platform boundaries, not scattered through shared UI and agent logic.
7. Login, cloud sync, remote execution, and team accounts remain out of scope.

## Objective
Ship a native Windows version of the `语码` desktop app that can open a local workspace, start the Python desktop bridge, run agent turns, show streaming conversation output, and handle permission requests without deadlocks.

The Windows app should feel like the same product as the macOS app:
- Same Codex-like shell and conversation layout.
- Same sidebar grouping by workspace folder.
- Same composer, model indicator, permission mode selector, and permission modal.
- Same local bridge protocol and transcript persistence model.

Success means a Windows user can install or run the app, select a project folder, send a task, approve a permission request, and receive the completed agent response without opening the TUI.

## Tech Stack
Shared desktop app:
- Tauri v2 from `desktop/src-tauri/`
- React + TypeScript + Vite from `desktop/`
- `@tauri-apps/api`
- Python `>=3.12`
- `uv`
- Local WebSocket bridge in `src/voice_code/desktop/`

Windows target:
- Windows 10/11
- WebView2 runtime
- Microsoft C++ Build Tools / MSVC toolchain for local builds
- Git for Windows
- PowerShell 7+ preferred for developer commands

Packaging targets:
- V0 developer build: Tauri Windows executable from `pnpm tauri build`
- V1 installer: NSIS installer first
- Deferred: MSI, auto-updater, signed installer, bundled Python/uv runtime

## Commands

Current shared gates:

```bash
cd new
uv run ruff check src/voice_code/desktop tests/test_desktop_server.py tests/test_desktop_bridge.py tests/test_desktop_runtime.py
uv run pytest tests/test_desktop_server.py tests/test_desktop_bridge.py tests/test_desktop_runtime.py -q
cd desktop
pnpm test
pnpm build
```

Windows developer setup:

```powershell
cd D:\workspace\reasoning\new
uv sync
cd desktop
pnpm install
pnpm tauri dev
```

Windows build:

```powershell
cd D:\workspace\reasoning\new\desktop
pnpm test
pnpm build
pnpm tauri build
```

Windows backend smoke:

```powershell
cd D:\workspace\reasoning\new
uv run reasoning-desktop-bridge --workspace "$PWD" --port 0
uv run pytest tests/test_desktop_server.py tests/test_desktop_bridge.py tests/test_desktop_runtime.py -q
```

Manual Windows app smoke:

```text
1. Launch 语码.exe.
2. Click the workspace card and select a project folder.
3. Send: 查看这个项目结构。
4. If a permission dialog appears, wait at least 30 seconds, then click 允许一次.
5. Confirm the turn continues and completes.
6. Switch permission mode to 只读模式 and confirm write-like tools are denied or ask-free blocked according to backend mode.
```

## Project Structure

Existing shared code:

```text
new/
  desktop/
    src/                         # shared React UI
    src-tauri/
      src/lib.rs                 # Tauri shell, currently mac-biased in a few places
      tauri.conf.json            # bundle targets currently mac-biased
  src/voice_code/desktop/   # local bridge, protocol, permissions, runtime
  tests/
    test_desktop_server.py
    test_desktop_bridge.py
    test_desktop_runtime.py
  docs/specs/
    windows-desktop-app-spec.md
    windows-platform-adaptation.md
```

Target additions:

```text
desktop/src-tauri/src/
  platform.rs                    # platform boundary helpers
  platform_macos.rs              # macOS folder picker/process cleanup, if split is useful
  platform_windows.rs            # Windows folder picker/process cleanup, if split is useful

desktop/src/lib/desktop/
  workspace-picker.ts            # stays platform-neutral from React's perspective

tests/
  test_desktop_windows_contract.py  # optional Python-side platform behavior tests
```

## Current Mac-Specific Surfaces

These are the places that block real Windows support today:

1. Folder picker:
   - Current mac app uses `/usr/bin/osascript`.
   - Windows needs a native folder picker.

2. Process tree cleanup:
   - Current app uses `/usr/bin/pkill -TERM -P <pid>`.
   - Windows needs `taskkill /PID <pid> /T /F` or a Rust-native process-tree cleanup path.

3. `uv` discovery:
   - Current candidates include `~/.local/bin/uv`, `/opt/homebrew/bin/uv`, `/usr/local/bin/uv`.
   - Windows needs `%USERPROFILE%\.local\bin\uv.exe`, `%USERPROFILE%\.cargo\bin\uv.exe`, `%LOCALAPPDATA%\Programs\uv\uv.exe`, PATH lookup with `.exe`, and explicit `REASONING_UV_PATH`.

4. Bundle targets:
   - Current `tauri.conf.json` uses `["app", "dmg"]`.
   - Windows should use `["nsis"]` for V1 or a platform-specific build config.

5. Shell/tool semantics:
   - The agent tool is named `bash`, but native Windows does not guarantee Bash.
   - Desktop V0 can still use the existing Python tool behavior, but Windows support must align with `windows-platform-adaptation.md` before claiming full parity.

6. Paths in UI and bridge:
   - UI must display `C:\...` paths without breaking truncation, labels, or workspace grouping.
   - Backend path comparisons must be `Path`-based, not slash-string based.

## Functional Requirements

### V0 Must Have
- Build and launch as a Windows desktop app.
- Open a single app window with the existing React UI.
- Start the Python desktop bridge from the app.
- Discover `uv.exe` from `REASONING_UV_PATH` or PATH.
- Select a workspace folder.
- Display the selected Windows path and folder name correctly.
- Send a prompt to the agent.
- Stream reasoning, assistant text, tool calls, tool results, tool errors, and finish events.
- Show permission requests.
- Resolve permission requests while a turn is still active.
- Avoid the prior permission deadlock where `turn.start` blocked `permission.resolve`.
- Stop the bridge process when the app exits.
- Run frontend tests and desktop bridge tests on Windows.

### V0 May Stub
- Installer polish.
- Auto-update.
- Code signing.
- Voice mode.
- Native notification integration.
- Bundled Python/uv.
- Windows-specific model/profile management UI.
- Search across sessions.

### V0 Must Not Include
- WSL as a hard requirement.
- Login or cloud account flow.
- Remote workspace execution.
- Silent installation of Python, Git, Build Tools, or `uv`.
- Hidden fallback to macOS-only commands.

## Platform Design

### 1. Tauri Platform Boundary
Move OS-specific behavior behind a small Rust interface.

Target shape:

```rust
fn pick_workspace_folder() -> Result<Option<PathBuf>, String>;
fn terminate_child_processes(parent_pid: u32);
fn uv_candidates() -> Vec<PathBuf>;
```

Windows behavior:
- `pick_workspace_folder` should use a native dialog.
- `terminate_child_processes` should terminate descendants before the bridge parent.
- `uv_candidates` should search PATH and known Windows install locations.

Preferred implementation:
- Use `tauri-plugin-dialog` for cross-platform folder selection if we accept the dependency.
- Keep `taskkill` as a small Windows fallback for process-tree cleanup.

Dependency decision:
- Adding `tauri-plugin-dialog` requires updating `desktop/package.json`, `desktop/src-tauri/Cargo.toml`, and Tauri plugin setup.
- If avoiding new dependencies, use OS-specific commands, but this is less clean and should be treated as a temporary V0 path.

### 2. Bridge Lifecycle
The Windows app should keep the current bridge model:
- Tauri starts one `reasoning-desktop-bridge` process.
- Bridge binds to loopback with a random token.
- Frontend connects over WebSocket.
- Requests can be concurrent on one WebSocket connection.

Critical rule:
- The transport must continue reading while `turn.start` is active. Permission resolution, interrupts, and future progress controls must not wait for the turn to finish.

Already-required backend behavior:
- `DesktopBridgeServer` handles each raw WebSocket request in its own task.
- WebSocket writes are serialized through a connection-level send lock.
- Disconnect denies pending permissions.

### 3. Permission Flow
Windows must behave exactly like macOS:
- Permission request appears as a modal sheet.
- User may wait before clicking.
- Clicking `允许一次`, `拒绝`, `本会话始终允许`, or `信任当前工作区` resolves the pending request.
- Late clicks after backend timeout should not leave the UI stuck.

Acceptance behavior:
- If resolve succeeds, the modal closes and the turn continues.
- If resolve returns `resolved: false`, the modal closes and the UI shows an inline stale-permission message rather than staying blocked.
- If WebSocket disconnects, pending permission modals are cleared.

### 4. Permission Mode Selector
The UI labels map to backend modes:

| UI label | Backend mode | Behavior |
|---|---|---|
| `完全访问` | `bypassPermissions` | Allows tools according to backend bypass semantics. |
| `按需询问` | `default` | Non-readonly tools ask. |
| `只读模式` | `dontAsk` | Ask-path tools are denied without prompting. |

Windows parity requirement:
- The selected mode must be sent in `turn.start.payload.permissionMode`.
- The backend must apply it for the current turn and restore the previous mode afterward.

### 5. Process Management
On Windows:
- Starting bridge uses `Command::new(uv_path)` with `.args(["run", "reasoning-desktop-bridge", "--port", "0"])`.
- `current_dir` should be the app workspace containing `pyproject.toml`, not necessarily the user-selected target workspace.
- The selected workspace is passed with `--workspace <path>`.
- Closing the app terminates bridge descendants.

Implementation candidate:

```rust
#[cfg(target_os = "windows")]
fn terminate_child_processes(parent_pid: u32) {
    let _ = Command::new("taskkill")
        .args(["/PID", &parent_pid.to_string(), "/T", "/F"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}
```

### 6. Workspace Picker
Expected behavior:
- Clicking the workspace card opens a native Windows folder picker.
- Cancel does nothing.
- Selecting a folder:
  - stops the current bridge,
  - updates workspace label/path,
  - clears current draft conversation,
  - starts the bridge against the selected workspace on the next prompt.

UI requirements:
- Long Windows paths truncate cleanly.
- Drive roots such as `D:\` render with a fallback label, for example `D`.
- UNC paths are deferred unless they work naturally through `PathBuf`.

### 7. Packaging
V0:
- Developer can run `pnpm tauri build` on Windows and get a launchable artifact.

V1:
- Configure NSIS installer.
- Document prerequisites:
  - WebView2 runtime
  - Python 3.12
  - `uv`
  - Git

V2:
- Bundle or bootstrap Python/uv.
- Add code signing.
- Add updater.

`tauri.conf.json` direction:
- Keep one config if Tauri can express platform-specific bundle targets cleanly.
- Otherwise add documented platform build commands or config variants.

## Code Style
Keep platform branching small and explicit.

Good:

```rust
fn terminate_child_processes(parent_pid: u32) {
    platform::terminate_child_processes(parent_pid);
}
```

Avoid:

```rust
if cfg!(windows) {
    // windows command here
} else if cfg!(macos) {
    // mac command here
}
```

inside unrelated bridge startup logic.

Frontend conventions:
- React components should not inspect OS directly for normal UI behavior.
- `workspace-picker.ts` stays a small platform-neutral API.
- Permission and conversation state should be driven by bridge events, not timers or UI guesses.

Backend conventions:
- Bridge protocol stays OS-neutral.
- Use `Path`/`PathBuf` for filesystem paths.
- Keep Windows shell/tool differences in the tool/runtime layer, not the desktop transport.

## Testing Strategy

### Frontend Unit Tests
Run on all platforms:

```powershell
cd desktop
pnpm test
pnpm build
```

Coverage:
- App renders with Windows-like workspace path.
- Workspace card remains clickable.
- Permission selector sends the mapped backend mode.
- Conversation reducer closes tool rows on finish/error.
- Permission modal clears on successful and failed resolve.

### Python Bridge Tests
Run on all platforms:

```powershell
uv run pytest tests/test_desktop_server.py tests/test_desktop_bridge.py tests/test_desktop_runtime.py -q
```

Coverage:
- WebSocket streams turn events.
- Bad tokens and validation errors return bounded responses.
- `permission.resolve` works during an active `turn.start`.
- Tool errors map to tool results.
- Permission mode is applied for one turn and restored afterward.

### Rust/Tauri Tests
Minimum:

```powershell
cd desktop\src-tauri
cargo check
```

Target coverage:
- `find_uv_binary` finds explicit `REASONING_UV_PATH`.
- Windows PATH lookup finds `.exe`.
- Process cleanup calls Windows branch.
- Workspace picker cancel returns `None`.

If Rust unit tests become awkward because of Tauri context, keep these helpers in plain Rust modules with unit-testable functions.

### Manual Windows Smoke
Required before marking V0 complete:
- Launch app.
- Select folder.
- Send prompt.
- Approve permission after waiting 30+ seconds.
- Deny a permission and confirm the UI does not hang.
- Quit app and confirm no bridge process remains.
- Reopen app and send another prompt.

### CI
Add a Windows job after V0 compiles locally:

```yaml
windows-desktop-smoke:
  runs-on: windows-latest
  steps:
    - checkout
    - setup node
    - setup uv
    - run: uv sync
      working-directory: new
    - run: uv run pytest tests/test_desktop_server.py tests/test_desktop_bridge.py tests/test_desktop_runtime.py -q
      working-directory: new
    - run: pnpm install
      working-directory: new/desktop
    - run: pnpm test
      working-directory: new/desktop
    - run: pnpm build
      working-directory: new/desktop
    - run: cargo check
      working-directory: new/desktop/src-tauri
```

## Implementation Plan

### Phase 0: Contract Hardening
- Ensure bridge request concurrency is covered by tests.
- Ensure permission timeout/stale resolve behavior is explicit.
- Ensure frontend permission modal does not stay open after `resolved: false`.

### Phase 1: Rust Platform Boundary
- Extract folder picker, child termination, and uv discovery into platform helpers.
- Add Windows branches.
- Keep macOS behavior unchanged.
- Verify with `cargo check`.

### Phase 2: Windows Build Config
- Add Windows bundle target.
- Document required system prerequisites.
- Build a Windows artifact locally or in CI.

### Phase 3: Windows Runtime Smoke
- Run the app on Windows.
- Select workspace.
- Start bridge.
- Send prompt.
- Approve permission after a delay.
- Quit and verify bridge cleanup.

### Phase 4: Installer And Polish
- Add NSIS installer.
- Add user-facing startup diagnostics for missing `uv`, Python, or Git.
- Decide whether to add bundled runtime.

## Boundaries

Always:
- Keep macOS app behavior working.
- Keep React UI platform-neutral unless there is a real Windows-only UX need.
- Keep bridge protocol OS-neutral.
- Preserve the permission deadlock regression test.
- Make late/stale permission decisions visible and non-blocking.

Ask first:
- Adding Tauri plugins or Rust crates.
- Bundling Python or `uv`.
- Changing installer type from NSIS to MSI.
- Requiring Git Bash, WSL, or PowerShell 7 as a hard runtime requirement.
- Adding auto-update or code signing infrastructure.

Never:
- Claim Windows support if it requires WSL.
- Leave macOS-only commands in shared code paths.
- Start hidden downloads or installers from inside the app.
- Block WebSocket request handling behind a long-running agent turn.
- Let a stale permission dialog keep the UI stuck.

## Success Criteria
- Windows app launches.
- Workspace folder selection works.
- Bridge starts with selected workspace.
- A normal prompt streams to completion.
- A delayed permission approval continues the active turn.
- A denied permission does not hang the UI.
- Closing the app terminates the bridge process tree.
- `pnpm test`, `pnpm build`, `cargo check`, and desktop bridge pytest gates pass on Windows.
- macOS build still passes after platform extraction.

## Open Questions
1. Should V0 require users to install Python/uv/Git manually, or should the Windows installer detect and guide missing prerequisites?
2. Are we comfortable adding `tauri-plugin-dialog` for cross-platform folder picking?
3. Should the first Windows installer be NSIS only, or do we need MSI for enterprise environments?
4. Should the tool named `bash` execute through PowerShell/cmd on Windows, or should full Windows support wait for the broader `windows-platform-adaptation.md` work?
5. Do we want CI to build a full Windows Tauri artifact immediately, or start with `cargo check` + frontend/backend tests first?
