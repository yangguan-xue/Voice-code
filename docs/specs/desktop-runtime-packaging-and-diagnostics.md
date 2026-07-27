# Spec: Desktop Runtime Packaging And Diagnostics

## Objective
Make the macOS and Windows desktop apps move from developer-run shells to installable products.

The desktop app must eventually start the Python agent without requiring a source checkout or a
preinstalled `uv` on the user's machine. Until that bundled runtime exists, the app must fail
clearly, leave useful logs, and keep the current `uv run` developer flow working.

Success means:
- Mac users can install a signed `.dmg`, open a workspace, start text and voice turns, and inspect
  logs when startup fails.
- Windows users can install an `.exe`/NSIS package, open a workspace, start text and voice turns,
  and inspect logs when startup fails.
- The desktop runtime strategy is explicit enough that packaging, CI, signing, and support work can
  proceed without re-deciding the architecture in each PR.

## Tech Stack
Desktop shell:
- Tauri v2
- Rust 2021
- React + TypeScript + Vite

Agent runtime:
- Python `>=3.12`
- `voice-code` wheel entry points:
  - `reasoning-desktop-bridge`
  - `reasoning-desktop-voice-bridge`
- Current development runtime: `uv run ...`
- Target release runtime: app-owned sidecar/runtime, not an implicit network download.

## Commands
Focused checks:

```bash
cd new/desktop
pnpm test
pnpm build
cargo test --manifest-path src-tauri/Cargo.toml
```

Python bridge checks:

```bash
cd new
uv run pytest tests/test_desktop_server.py tests/test_desktop_bridge.py tests/test_desktop_runtime.py -q
uv run pytest tests/test_desktop_voice_server.py tests/test_desktop_voice_bridge.py -q
uv run pytest tests/test_desktop_runtime_launcher.py tests/test_build_desktop_runtime.py -q
```

Runtime resource layout, no downloads or dependency installs:

```bash
cd new/desktop
pnpm runtime:layout
```

Script launcher layout, still no downloads or dependency installs:

```bash
cd new/desktop
pnpm runtime:launchers
```

Explicit runtime environment install, may download Python packages through `uv`:

```bash
cd new/desktop
pnpm runtime:install
```

Use explicit flags in CI:

```bash
node scripts/build-runtime.mjs \
  --output src-tauri/resources/runtime \
  --install \
  --uv /path/to/uv \
  --python 3.12 \
  --project-root /path/to/new
```

Release smoke, once packaging exists:

```bash
cd new/desktop
pnpm runtime:install
pnpm tauri build
pnpm tauri:build:windows
```

Or use the combined release commands:

```bash
cd new/desktop
pnpm tauri:build:runtime
pnpm tauri:build:windows:runtime
```

## Project Structure
Expected ownership:

```text
desktop/src-tauri/
  tauri.conf.json                 # shared app metadata and macOS bundle target
  tauri.macos.runtime.conf.json   # macOS app-owned runtime resource filter
  tauri.windows.conf.json         # Windows bundle target
  src/lib.rs                      # runtime discovery, bridge launch, diagnostics
  src/platform.rs                 # platform-specific helpers

src/voice_code/desktop/      # text bridge runtime
src/voice_code/desktop_voice/# voice bridge runtime

docs/specs/
  desktop-runtime-packaging-and-diagnostics.md
  mac-desktop-technical-spec.md
  windows-desktop-app-spec.md
```

## Runtime Strategy
### Phase 0: Developer Runtime
Keep the existing `uv run` launch path:
- Locate `uv` via `REASONING_UV_PATH`, `PATH`, or known platform install paths.
- Locate the repository workspace via `REASONING_WORKSPACE`, current directory, executable
  ancestors, or `CARGO_MANIFEST_DIR`.
- Start bridge processes on loopback with a random token.
- Capture bridge stderr to the app log directory.

This phase is for development and internal smoke testing only. It is not a final user distribution
model.

### Phase 1: App-Owned Runtime
Package the Python agent as an app-owned runtime:
- Build a wheel for `voice-code`.
- Install dependencies into a platform-specific runtime directory during release packaging.
- Ship either a small Python launcher sidecar or a frozen executable wrapper.
- Resolve runtime paths relative to the app bundle/installer location, not the source checkout.
- Keep `REASONING_UV_PATH` and `REASONING_WORKSPACE` as developer overrides.

No app path should silently download Python, `uv`, Rust, Git, or build tools at first launch.

Runtime resource contract:
- Runtime release overlay configs map selected files from `desktop/src-tauri/resources/runtime` to
  the app resource path `runtime`.
- Generated files under `desktop/src-tauri/resources/runtime` are ignored by Git except the
  `.gitkeep` placeholder; release packaging is responsible for creating them before `tauri build`.
- macOS runtime release builds use `tauri.macos.runtime.conf.json` to bundle `python/`, README,
  manifest, and `voice-code-agent` only.
- Windows runtime release builds use `tauri.windows.runtime.conf.json` to bundle `python/`, README,
  manifest, and `voice-code-agent.cmd` only.
- The app resource directory may contain `runtime/voice-code-agent`.
- On Windows the launcher discovery order is `runtime/voice-code-agent.exe`, then
  `runtime/voice-code-agent.cmd`. The `.exe` form is preferred for frozen release builds; the
  `.cmd` form exists for an app-owned managed Python runtime layout.
- The launcher source is the Python console script `voice-code-agent`, implemented by
  `voice_code.desktop_runtime.launcher:main`.
- If the launcher exists, Tauri selects runtime mode `app-owned`.
- If the launcher is missing, Tauri falls back to runtime mode `developer-uv`.
- The launcher receives the desired entry point as its first argument:
  - `reasoning-desktop-bridge --port 0 --workspace <path>`
  - `reasoning-desktop-voice-bridge --port 0 --workspace <path>`
  - `voice-code-desktop-metadata --workspace <path>`
- The launcher must print the same startup JSON contracts currently printed by the Python entry
  points. Frontend and WebSocket contracts must not change between runtime modes.
- `tools/build_desktop_runtime.py` owns the deterministic resource layout and manifest. It must not
  download Python or install dependencies unless `--install` is passed.
- `--install` creates a managed Python runtime with `uv python install --install-dir <runtime>/python`
  and installs the project with `uv pip install --system --break-system-packages --python
  <runtime-python> <project-root>`. This command is suitable for release packaging jobs, not first
  app launch.

### Phase 2: Signed Release Runtime
Add platform release hardening:
- macOS: Developer ID signing, notarization, microphone privacy string, `.dmg` validation.
- Windows: code signing certificate, NSIS installer, WebView2 handling, SmartScreen runbook.
- CI: macOS and Windows build artifacts, checksums, smoke tests, and release notes.

## Diagnostics
Always do:
- Write text bridge stderr to `desktop-bridge.log`.
- Write voice bridge stderr to `desktop-voice-bridge.log`.
- Include the log path in startup errors.
- Expose `desktop_diagnostics` with `runtimeMode`, `runtimeExecutable`, `appLogDir`,
  `textBridgeLog`, and `voiceBridgeLog`.
- Never log API keys, model config secrets, or raw user prompts from the Rust launcher.
- Preserve bridge stdout for startup JSON only.

Ask first:
- Adding external crash-reporting services.
- Uploading logs automatically.
- Bundling a new runtime technology such as PyInstaller, Nuitka, or a custom Python distribution.

Never do:
- Hide dependency downloads behind a frozen UI.
- Require WSL for Windows support.
- Treat developer `uv run` success as proof that packaged app distribution works.

## Code Style
Keep runtime selection explicit and boring:

```rust
let runtime = DesktopRuntime::developer_uv(uv, app_workspace);
let launch = runtime.launch("reasoning-desktop-bridge", workspace, log_path)?;
```

Conventions:
- Put platform differences behind `platform.rs`.
- Keep release-runtime discovery separate from developer-runtime discovery.
- Return actionable errors with a next step and a diagnostics path.
- Avoid logging environment variables wholesale.

## Testing Strategy
Unit tests:
- Runtime path formatting.
- App-owned runtime discovery and launcher command shape.
- Python launcher dispatch and unknown-entrypoint behavior.
- Log-path error formatting.
- Windows drive and path label handling.
- `uv` candidate discovery.

Integration tests:
- `permission.resolve` remains responsive during active turns.
- Bridge startup returns valid JSON.
- Invalid bridge startup surfaces a bounded error and log path.
- Voice and text bridges can be started and stopped independently.

Manual release smoke:
- Install app.
- Open workspace with spaces and non-ASCII characters in the path.
- Send one read-only prompt.
- Trigger one permission prompt and approve after a delay.
- Open voice window, send typed voice fallback, close voice without closing main window.
- Confirm logs exist after a forced bridge failure.

## Success Criteria
- Current development bridge launches still work on macOS.
- Bridge startup failures include the relevant log file path.
- The spec names the target packaging phases and non-goals.
- A follow-up PR can add bundled runtime discovery without changing frontend contracts.
- Mac and Windows release work can be tracked as separate implementation slices.

## Open Questions
- Should the release runtime be a Python sidecar plus wheel environment, or a frozen executable?
- Should Windows V1 install WebView2 if missing, or only document it as a prerequisite?
- Should auto-update wait until after signed Mac and Windows builds are stable?
