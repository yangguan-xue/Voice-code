# Spec: Windows Platform Adaptation

## Assumptions
1. This spec targets native Windows 10/11 with PowerShell or Windows Terminal, not WSL.
2. The supported launch path is `uv run reasoning`, `uv run reasoning --plain`, and `uv run reasoning-voice` from the repository `new/` directory.
3. Python remains `>=3.12`; no new runtime dependency should be required just for Windows compatibility unless a feature cannot be implemented with the standard library.
4. Windows support must preserve existing macOS/Linux behavior, especially TUI goal streaming, session durability, permission prompts, and goal worktree isolation.
5. The public package path `voice_code` should receive the same Windows compatibility fixes when a changed module is mirrored for publishing.
6. Windows-specific limits that cannot match POSIX exactly, such as `resource.setrlimit`, must degrade explicitly and be covered by tests.

## Objective
Make the agent usable and testable on native Windows without requiring WSL-specific assumptions.

Users should be able to:
- Start CLI, TUI, and voice entry points from PowerShell.
- Run normal turns and `/goal` turns with visible progress.
- Use file, shell, clipboard, session, memory, MCP, and goal worktree features with Windows paths.
- Avoid terminal corruption from raw telemetry logs.
- Run the focused Windows smoke suite and understand which features are intentionally degraded.

Success means Windows users can perform the same core coding workflows as macOS/Linux users, with documented platform differences and deterministic tests guarding regressions.

## Tech Stack
- Python `>=3.12`
- `uv`
- Textual + Rich
- LangChain + `ChatOpenAI`
- Git for Windows
- PowerShell 7+ preferred; Windows PowerShell 5.1 acceptable for smoke tests
- Optional voice dependencies:
  - microphone/audio stack available to the current Windows account
  - Step Fun API or local STT/TTS service

## Commands
PowerShell setup:

```powershell
cd D:\workspace\voice-code
uv sync
uv run reasoning --help
uv run reasoning --plain
uv run reasoning
uv run reasoning-voice --help
```

Quality gates:

```powershell
uv run ruff check src/ tests/
uv run pytest tests/ -v
```

Targeted Windows smoke:

```powershell
uv run pytest tests/test_tools_bash.py tests/test_mcp_security.py tests/test_goal_workspace.py tests/test_goal_scope.py tests/test_telemetry_entrypoints.py tests/test_tui_event_metrics.py tests/test_models.py -v
```

Manual TUI goal check:

```text
/goal --allow . --verify "uv run pytest tests/test_tui_event_metrics.py::test_goal_event_queue_renders_text_and_keeps_turn_open -v" --max-iterations 2 检查 TUI goal 模式是否能显示实时 reasoning/text/tool 进度，并确认新增的 focused test 通过；不要做无关改动。
```

## Project Structure
- `src/voice_code/tools/bash.py` owns shell execution, timeouts, sandbox downgrade semantics, and command invocation behavior.
- `src/voice_code/clipboard.py` owns cross-platform clipboard copy.
- `src/voice_code/tui.py` owns Textual rendering, telemetry log routing, and UI copy fallback behavior.
- `src/voice_code/goals/workspace.py` owns isolated Git worktree creation, attach, restore, and discard.
- `src/voice_code/goals/scope.py` owns allowed-path normalization and diff scope checks.
- `src/voice_code/goals/store.py` and `src/voice_code/session/state.py` own durable JSON writes and filesystem permissions.
- `src/voice_code/integrations/mcp.py` owns MCP server process creation and Windows resource-limit downgrade.
- `src/voice_code/llm/models.py` and `src/voice_code/runtime.py` own profile resolution and context window display state.
- `tests/` owns unit and smoke coverage. Windows-specific tests should use `platform.system()` monkeypatches when possible and real Windows CI for end-to-end confidence.

## Functional Requirements

### 1. Windows Path Handling Must Be Native And Safe
All user-facing path inputs must accept Windows path forms:
- drive paths such as `D:\repo\new`
- forward-slash equivalents such as `D:/repo/new`
- relative paths such as `.`, `src`, `tests/test_file.py`
- paths containing spaces

Required behavior:
- Path comparisons must use `Path.resolve()` or `os.path` semantics, not string prefix checks.
- Internal slash-normalized paths may be used for Git diff output, but filesystem access must use `Path`.
- Scope gates must reject path escapes including `..`, absolute paths outside workspace, symlink/junction escapes where detectable, and mixed-separator bypasses.
- `/goal --allow .` and `/goal --allow ./` must mean "allow changes inside the current goal execution workspace", not the whole repository when the workspace is a subdirectory.

Implementation direction:
- Keep `PurePosixPath` only for Git-relative path matching.
- Normalize user allow paths with a helper that understands Windows separators before converting to Git-relative slash paths.
- Add table-driven tests for `src\foo.py`, `src/foo.py`, `.`, `./`, `..\outside`, `C:\outside`, and paths with spaces.

### 2. Shell Execution Must Match Windows Expectations
The `bash` tool name may remain for tool compatibility, but on Windows it should execute commands through the platform shell predictably.

Required behavior:
- On Windows, use `asyncio.create_subprocess_shell()` with `cwd` set to the resolved workspace path.
- Timeouts must terminate the process and return bounded `<tool_use_error>` output.
- POSIX-only CPU and memory limits must not be attempted on Windows.
- Network deny and allowlist policy must still apply before process launch.
- Commands in docs and tests should prefer cross-platform Python one-liners or `uv run ...` over POSIX-only utilities.

Implementation direction:
- Make shell choice explicit in code and docs: PowerShell/cmd behavior is not the same as Bash quoting.
- Avoid relying on `SHELL` on Windows.
- Add tests using `platform.system() == "Windows"` monkeypatches for process kwargs, timeout, and resource-limit downgrade.

### 3. TUI Must Render Cleanly In Windows Terminals
The Textual UI must not be corrupted by raw telemetry or background logs.

Required behavior:
- Normal TUI logs write to `~/.reasoning/logs/reasoning-tui.log` or `REASONING_HOME/logs/reasoning-tui.log`, not to the terminal surface.
- `--debug` may increase log verbosity but must still avoid corrupting the transcript pane.
- The topbar, sidebar, prompt area, permission dialog, and goal streaming output must fit common Windows Terminal sizes.
- Copy actions should use the shared clipboard abstraction rather than macOS-only `pbcopy`.

Implementation direction:
- Replace direct `pbcopy` fallback in TUI with `copy_to_clipboard()`.
- Add Windows clipboard support in `clipboard.py` using `clip.exe`.
- Keep all user-facing TUI status messages semantic, not logger-derived.

### 4. Goal Worktrees Must Work On Git For Windows
Goal execution uses detached Git worktrees under `.reasoning/worktrees/<goal_id>`.

Required behavior:
- Worktree creation, attach, restore, checkpoint, discard, and scope verification work with Windows paths.
- Worktree roots must remain inside the configured workspace.
- Existing dirty primary workspace changes remain isolated from the goal worktree.
- Uncommitted main worktree changes are not silently copied into goal worktrees. This behavior must be documented in the TUI or goal docs so users understand why a goal sees `HEAD`, not dirty edits.
- Goal worktree cleanup must work when files are read-only where possible; failures should be reported cleanly.

Implementation direction:
- Consider a clear startup notice for goals when the primary worktree is dirty: "Goal worktrees start from HEAD; commit or stash current changes if the goal must see them."
- Keep Git commands as `asyncio.create_subprocess_exec("git", ...)` with argument lists, not shell strings.
- Add Windows CI coverage for `tests/test_goal_workspace.py` and `tests/test_goal_scope.py`.

### 5. Durable State Writes Must Degrade Safely
Session, goal, memory, permission, prompt cache, and telemetry files must persist on Windows.

Required behavior:
- `chmod(0o700)` and `chmod(0o600)` calls must not make Windows runs fail.
- Atomic write patterns using temp files and replace/link must account for Windows limitations.
- `os.link(..., follow_symlinks=False)` or POSIX-specific link behavior must have a Windows-compatible fallback where needed.
- Failure messages must not include secrets or full private content.

Implementation direction:
- Wrap permission-setting calls in a helper such as `best_effort_private_permissions(path)`.
- Replace hard-link-only atomic create patterns with a cross-platform strategy, or branch with tests for Windows behavior.
- Keep all state directories under `.reasoning/` or `REASONING_HOME`.

### 6. MCP And Subprocess Integrations Must Be Cross-Platform
MCP server launch and subprocess cleanup must work on Windows.

Required behavior:
- Server command allowlists must support `.exe`, `.cmd`, `.bat`, `node`, `python`, and `uv` where configured.
- Environment filtering must remain strict.
- POSIX process group behavior must not be used on Windows.
- Hung MCP servers must terminate cleanly or surface a bounded error.

Implementation direction:
- Keep Windows process creation branches explicit.
- Add tests that patch `platform.system()` to Windows and assert no `preexec_fn` or `start_new_session` is passed.

### 7. Voice Mode Must Have A Windows Runbook
Voice mode is allowed to remain more environment-sensitive than CLI/TUI, but the expected Windows setup must be documented.

Required behavior:
- `uv run reasoning-voice --help` works.
- Missing microphone, audio device, or API keys should produce actionable errors instead of stack traces.
- STT/TTS provider selection and fallback are documented for Windows.

Implementation direction:
- Add a Windows voice troubleshooting section to the README or runbook after implementation.
- Keep provider clients independent of POSIX-only audio commands.

## Code Style
Prefer platform boundary helpers over scattered `platform.system()` checks.

Example target style:

```python
def best_effort_private_permissions(path: Path, *, directory: bool = False) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        logger.debug("private permission chmod skipped", extra={"event": "fs.permission.skipped"})
```

Conventions:
- Use `Path` for filesystem paths and list-style subprocess arguments for Git.
- Use `PurePosixPath` only for Git-relative path matching.
- Do not hard-code `/tmp`, `pbcopy`, `bash`, or POSIX shell syntax in shared code.
- Prefer explicit Windows branches at integration boundaries over implicit behavior.
- Keep tests deterministic and avoid requiring a real Windows terminal unless the test is an end-to-end smoke.

## Testing Strategy

Unit tests:
- Path normalization and scope gate:
  - `tests/test_goal_scope.py`
  - Windows separators, root scope, escape rejection, subdirectory workspace boundaries.
- Worktree lifecycle:
  - `tests/test_goal_workspace.py`
  - Git worktree create/restore/discard under Windows CI.
- Shell sandbox:
  - `tests/test_tools_bash.py`
  - Windows branch does not use POSIX process groups or `resource`.
- MCP:
  - `tests/test_mcp_security.py`
  - Windows process kwargs and command allowlists.
- TUI/logging:
  - `tests/test_telemetry_entrypoints.py`
  - `tests/test_tui_event_metrics.py`
  - logs do not write to terminal stream; goal event forwarding remains visible.
- Clipboard:
  - new tests for `clip.exe` success/failure and existing macOS/Linux behavior.
- Model/profile display:
  - `tests/test_models.py`
  - context window uses the actual profile from `--profile`, `LLM_PROFILE`, or `.env`.

CI:
- Add a Windows job using `windows-latest`.
- Install `uv`.
- Run:

```powershell
uv sync
uv run ruff check src/ tests/
uv run pytest tests/test_tools_bash.py tests/test_mcp_security.py tests/test_goal_workspace.py tests/test_goal_scope.py tests/test_telemetry_entrypoints.py tests/test_tui_event_metrics.py tests/test_models.py -v
```

Manual verification:
- Start `uv run reasoning --plain`; run a normal prompt and `/help`.
- Start `uv run reasoning`; verify TUI renders without raw `INFO [...]` lines.
- Run the manual `/goal --allow . ...` check and confirm:
  - reasoning/text/tool progress is visible,
  - focused test passes,
  - token/context display updates,
  - no terminal log noise appears.
- Run `uv run reasoning-voice --help`; if hardware/API keys are configured, run one short voice turn.

## Boundaries

Always:
- Preserve macOS/Linux behavior.
- Keep Windows-specific behavior documented and tested.
- Prefer cross-platform stdlib APIs.
- Keep telemetry privacy guarantees intact.
- Run focused Windows-related tests before broad gates.

Ask first:
- Adding a Windows-only dependency.
- Changing the public CLI command names.
- Changing goal persistence layout under `.reasoning/goals`.
- Changing the isolation model from worktrees to in-place execution.
- Dropping support for Windows PowerShell 5.1.

Never:
- Require WSL for the native Windows support claim.
- Use string prefix checks for workspace security.
- Let raw logs corrupt the TUI.
- Copy dirty primary-worktree changes into goal worktrees silently.
- Store secrets in `models.toml`, logs, transcripts, goal evidence, or crash output.

## Success Criteria
- `uv run reasoning --plain` works on native Windows.
- `uv run reasoning` opens a clean Textual TUI on Windows Terminal.
- Manual `/goal --allow . ...` runs without path normalization errors and shows visible progress.
- `clip.exe` clipboard copy works when available and fails gracefully when unavailable.
- Goal worktree lifecycle tests pass on Windows.
- Shell and MCP subprocess tests prove Windows branches avoid POSIX-only kwargs.
- Context window display matches the actual active profile from `--profile`, `LLM_PROFILE`, or `.env`.
- Raw telemetry logs are routed to a log file and do not appear inside the TUI surface.
- Windows CI smoke passes on `windows-latest`.

## Open Questions
1. Should Windows TUI logs use `REASONING_HOME/logs` instead of always `Path.home() / ".reasoning" / "logs"`?
2. Should the shell tool invoke PowerShell explicitly on Windows, or keep `create_subprocess_shell()` defaulting to `cmd.exe`?
3. Should goal mode offer an opt-in `--include-dirty` workflow, or should users always commit/stash before starting an isolated goal?
4. Which voice stack is the supported default on Windows: Step Fun API only, local VoxCPM2/Fish-Speech, or both?
5. Should Windows CI run the full suite or only the smoke subset until all platform-sensitive tests are hardened?
