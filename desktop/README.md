# Voice Code Desktop

Tauri desktop shell for Voice Code. The shared React UI can run in a browser with a mock
bridge, or inside Tauri with the local Python desktop bridge.

## Development

```bash
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5173/`.

## macOS Build

```bash
pnpm test
pnpm build
pnpm tauri build
```

The default Tauri config builds the current macOS `.app` and `.dmg` targets.

For a release build with the app-owned Python runtime prepared first:

```bash
pnpm tauri:build:runtime
```

This uses `src-tauri/tauri.macos.runtime.conf.json` so the macOS bundle only includes
the POSIX launcher and does not ship Windows launcher files.

## Windows Build

The normal Windows preview build expects Python 3.12, `uv`, Git, WebView2, and the
MSVC/Rust toolchain to be installed by the developer or user. It does not silently
download or install them.

```powershell
pnpm install
pnpm test
pnpm build
pnpm tauri:build:windows
```

`pnpm tauri:build:windows` uses `src-tauri/tauri.windows.conf.json` so Windows builds do
not try to package macOS `.app` or `.dmg` targets. This preview build uses developer
runtime discovery and does not bundle Python or `uv`.

Set `REASONING_UV_PATH` if `uv.exe` is not on `PATH`.

For a Windows release build with the app-owned Python runtime prepared first:

```powershell
pnpm tauri:build:windows:runtime
```

That runtime build uses `src-tauri/tauri.windows.runtime.conf.json` and includes the
managed Python runtime plus the `.cmd` launcher used when a frozen `.exe` launcher is
not present.

## Bridge Mode

Inside the Tauri app, the shell invokes `ensure_desktop_bridge`, starts the Python
bridge automatically, and passes the returned config to the React bridge client.

For browser-only development, start the Python bridge manually:

```bash
uv run reasoning-desktop-bridge --port 8765
```

The command prints a JSON payload with `url`, `token`, `sessionId`, `cwd`, and `model`.
Set both environment variables to connect the page to that bridge:

```bash
VITE_DESKTOP_BRIDGE_URL=ws://127.0.0.1:8765 \
VITE_DESKTOP_BRIDGE_TOKEN=<local-random-token> \
pnpm dev
```

If either value is missing, the app falls back to the mock bridge.

## App-Owned Runtime Layout

The packaged app first looks for an app resource directory named `runtime/`. If a
`voice-code-agent` launcher exists there, Tauri uses that app-owned runtime instead of
developer `uv run`.

Tauri maps `src-tauri/resources/runtime` into that app resource path. The generated
files in that directory are ignored by Git except for the placeholder that keeps the
directory present.

Create the runtime manifest and README without downloading or installing anything:

```bash
pnpm runtime:layout
```

Create script launchers too, still without downloading or installing dependencies:

```bash
pnpm runtime:launchers
```

Create the app-owned managed Python explicitly during release packaging:

```bash
pnpm runtime:install
```

`runtime:install` runs `uv python install` and `uv pip install`, so it may download
Python and Python packages. Those downloads happen during packaging only, never at
first app launch. The launchers fail clearly if the managed Python runtime is missing
instead of freezing the UI or fetching tools in the background.
