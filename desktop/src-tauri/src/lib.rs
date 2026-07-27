use serde::{Deserialize, Serialize};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStderr, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

mod desktop_runtime;
mod platform;

use desktop_runtime::{BridgeCommandKind, DesktopRuntime};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const MODEL_KEYRING_SERVICE: &str = "voice-code-desktop-model";
const MODEL_CONFIG_FILE: &str = "model-configs.json";
const VOICE_KEYRING_SERVICE: &str = "voice-code-desktop-voice";
const VOICE_CONFIG_FILE: &str = "voice-config.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopBridgeConfig {
    url: String,
    token: String,
    session_id: String,
    cwd: String,
    model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopVoiceBridgeConfig {
    url: String,
    token: String,
    session_id: String,
    cwd: String,
    model: String,
    backend: String,
    tts_muted: bool,
}

#[derive(Default)]
struct BridgeProcessState {
    child: Mutex<Option<Child>>,
    config: Mutex<Option<DesktopBridgeConfig>>,
    voice_child: Mutex<Option<Child>>,
    voice_config: Mutex<Option<DesktopVoiceBridgeConfig>>,
    selected_workspace: Mutex<Option<PathBuf>>,
    model_configs: Mutex<()>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredModelConfig {
    id: String,
    display_name: String,
    base_url: String,
    model_name: String,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModelConfigStore {
    #[serde(default)]
    active_config_id: Option<String>,
    #[serde(default)]
    configs: Vec<StoredModelConfig>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelConfigSummary {
    id: String,
    label: String,
    base_url: String,
    model_name: String,
    has_api_key: bool,
    source: &'static str,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelConfigList {
    configs: Vec<ModelConfigSummary>,
    active_config_id: Option<String>,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModelConfigInput {
    id: Option<String>,
    display_name: String,
    base_url: String,
    api_key: String,
    model_name: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ResolvedModelConfig {
    id: String,
    display_name: String,
    base_url: String,
    api_key: String,
    model_name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredVoiceConfig {
    provider: String,
    stepfun_voice: String,
    stt_url: String,
    tts_url: String,
    tts_enabled: bool,
}

impl Default for StoredVoiceConfig {
    fn default() -> Self {
        Self {
            provider: "custom".to_string(),
            stepfun_voice: "cixingnansheng".to_string(),
            stt_url: "http://localhost:8765".to_string(),
            tts_url: "http://localhost:8775".to_string(),
            tts_enabled: false,
        }
    }
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct VoiceSettingsInput {
    provider: String,
    stt_url: String,
    tts_url: String,
    stepfun_voice: String,
    stepfun_key: String,
    tts_enabled: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct VoiceSettings {
    provider: String,
    stt_url: String,
    tts_url: String,
    stepfun_voice: String,
    stepfun_key: String,
    has_stepfun_key: bool,
    tts_enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceSelection {
    label: String,
    path: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopDiagnostics {
    runtime_mode: &'static str,
    runtime_executable: Option<String>,
    uv_path: Option<String>,
    workspace_root: Option<String>,
    app_log_dir: Option<String>,
    text_bridge_log: Option<String>,
    voice_bridge_log: Option<String>,
}

impl Drop for BridgeProcessState {
    fn drop(&mut self) {
        self.stop_bridge();
        self.stop_voice_bridge();
    }
}

impl BridgeProcessState {
    fn stop_bridge(&self) {
        let Ok(mut child) = self.child.lock() else {
            return;
        };
        let Some(mut process) = child.take() else {
            return;
        };
        platform::terminate_child_processes(process.id());
        let _ = process.kill();
        let _ = process.wait();
        if let Ok(mut config) = self.config.lock() {
            *config = None;
        }
    }

    fn stop_voice_bridge(&self) {
        let Ok(mut child) = self.voice_child.lock() else {
            return;
        };
        let Some(mut process) = child.take() else {
            return;
        };
        platform::terminate_child_processes(process.id());
        let _ = process.kill();
        let _ = process.wait();
        if let Ok(mut config) = self.voice_config.lock() {
            *config = None;
        }
    }
}

#[tauri::command]
async fn ensure_desktop_bridge(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
) -> Result<DesktopBridgeConfig, String> {
    if let Some(config) = state
        .config
        .lock()
        .map_err(|_| "bridge config lock is poisoned".to_string())?
        .clone()
    {
        return Ok(config);
    }

    let runtime = resolve_desktop_runtime(&app)?;
    let default_workspace = default_workspace_for_runtime(&runtime);
    let workspace = state
        .selected_workspace
        .lock()
        .map_err(|_| "selected workspace lock is poisoned".to_string())?
        .clone()
        .unwrap_or(default_workspace);
    let active_model = {
        let _guard = state
            .model_configs
            .lock()
            .map_err(|_| "model config lock is poisoned".to_string())?;
        resolve_active_model_config(&app)?
    };
    let log_path = bridge_log_path(&app, "desktop-bridge.log");
    let (child, config) = tauri::async_runtime::spawn_blocking(move || {
        start_desktop_bridge_process(runtime, workspace, active_model, log_path)
    })
    .await
    .map_err(|error| format!("Desktop bridge startup task failed: {error}"))??;

    *state
        .child
        .lock()
        .map_err(|_| "bridge process lock is poisoned".to_string())? = Some(child);
    *state
        .config
        .lock()
        .map_err(|_| "bridge config lock is poisoned".to_string())? = Some(config.clone());

    Ok(config)
}

fn start_desktop_bridge_process(
    runtime: DesktopRuntime,
    workspace: PathBuf,
    active_model: Option<ResolvedModelConfig>,
    log_path: Option<PathBuf>,
) -> Result<(Child, DesktopBridgeConfig), String> {
    initialize_bridge_log(
        log_path.as_deref(),
        "desktop bridge",
        runtime.mode_name(),
        runtime.executable(),
        runtime.app_workspace(),
        &workspace,
    );
    let mut command = runtime.command(BridgeCommandKind::Text, &workspace);
    command.stdin(Stdio::null()).stdout(Stdio::piped());
    configure_bridge_stderr(&mut command, log_path.as_ref());
    if let Some(model) = active_model {
        command
            .env("REASONING_DESKTOP_MODEL_CONFIG_ID", model.id)
            .env("REASONING_DESKTOP_MODEL_BASE_URL", model.base_url)
            .env("REASONING_DESKTOP_MODEL_API_KEY", model.api_key)
            .env("REASONING_DESKTOP_MODEL_NAME", model.model_name);
    }
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);

    let startup_log_path = log_path.clone();
    let mut child = command.spawn().map_err(|error| {
        format_startup_error(
            "Desktop bridge",
            &format!(
                "Failed to start {} with {}: {error}",
                runtime.mode_name(),
                runtime.executable().display()
            ),
            startup_log_path.as_deref(),
        )
    })?;
    pipe_bridge_stderr(child.stderr.take(), log_path.clone());

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Desktop bridge stdout was unavailable.".to_string())?;
    let line = read_first_line_with_timeout(stdout, Duration::from_secs(60)).map_err(|error| {
        let _ = child.kill();
        let _ = child.wait();
        format_startup_error("Desktop bridge", &error, log_path.as_deref())
    })?;
    let config: DesktopBridgeConfig = serde_json::from_str(line.trim()).map_err(|error| {
        format_startup_error(
            "Desktop bridge",
            &format!("Returned invalid startup JSON: {error}"),
            log_path.as_deref(),
        )
    })?;

    Ok((child, config))
}

#[tauri::command]
fn list_model_configs(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
) -> Result<ModelConfigList, String> {
    let _guard = state
        .model_configs
        .lock()
        .map_err(|_| "model config lock is poisoned".to_string())?;
    let store = read_model_config_store(&app)?;
    let configs = store.configs.iter().map(model_config_summary).collect();
    Ok(ModelConfigList {
        configs,
        active_config_id: store.active_config_id,
    })
}

#[tauri::command]
fn desktop_diagnostics(app: AppHandle) -> DesktopDiagnostics {
    let app_log_dir = app.path().app_log_dir().ok();
    let runtime = resolve_desktop_runtime(&app).ok();
    desktop_diagnostics_payload(
        app_log_dir,
        runtime.as_ref(),
        find_uv_binary(),
        find_app_workspace_root(),
    )
}

#[tauri::command]
fn open_diagnostics_path(path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    if !path.is_absolute() {
        return Err("Diagnostics path must be absolute.".to_string());
    }
    if !path.exists() {
        return Err(format!(
            "Diagnostics path does not exist: {}",
            display_path(&path)
        ));
    }
    platform::open_path(&path)
}

#[tauri::command]
fn save_model_config(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
    input: ModelConfigInput,
) -> Result<ModelConfigSummary, String> {
    let _guard = state
        .model_configs
        .lock()
        .map_err(|_| "model config lock is poisoned".to_string())?;
    let display_name = required_value(input.display_name, "Display name")?;
    let base_url = required_value(input.base_url, "API URL")?;
    if !base_url.starts_with("http://") && !base_url.starts_with("https://") {
        return Err("API URL must start with http:// or https://.".to_string());
    }
    let model_name = required_value(input.model_name, "Model name")?;
    let id = input
        .id
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(new_model_config_id);
    let mut store = read_model_config_store(&app)?;
    let exists = store.configs.iter().any(|config| config.id == id);
    let api_key = input.api_key.trim();
    if api_key.is_empty() && (!exists || read_model_api_key(&id).is_err()) {
        return Err("API Key is required for a new model configuration.".to_string());
    }
    if !api_key.is_empty() {
        write_model_api_key(&id, api_key)?;
    }

    let config = StoredModelConfig {
        id: id.clone(),
        display_name,
        base_url,
        model_name,
    };
    if let Some(current) = store.configs.iter_mut().find(|item| item.id == id) {
        *current = config.clone();
    } else {
        store.configs.push(config.clone());
    }
    write_model_config_store(&app, &store)?;
    Ok(model_config_summary(&config))
}

#[tauri::command]
fn delete_model_config(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
    id: String,
) -> Result<bool, String> {
    let _guard = state
        .model_configs
        .lock()
        .map_err(|_| "model config lock is poisoned".to_string())?;
    let mut store = read_model_config_store(&app)?;
    let original_len = store.configs.len();
    store.configs.retain(|config| config.id != id);
    if store.configs.len() == original_len {
        return Ok(false);
    }
    if store.active_config_id.as_deref() == Some(id.as_str()) {
        store.active_config_id = None;
    }
    write_model_config_store(&app, &store)?;
    if let Ok(entry) = keyring::Entry::new(MODEL_KEYRING_SERVICE, &id) {
        let _ = entry.delete_credential();
    }
    Ok(true)
}

#[tauri::command]
fn resolve_model_config(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
    id: String,
) -> Result<ResolvedModelConfig, String> {
    let _guard = state
        .model_configs
        .lock()
        .map_err(|_| "model config lock is poisoned".to_string())?;
    resolve_model_config_by_id(&app, &id)
}

#[tauri::command]
fn set_active_model_config(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
    id: Option<String>,
) -> Result<(), String> {
    let _guard = state
        .model_configs
        .lock()
        .map_err(|_| "model config lock is poisoned".to_string())?;
    let mut store = read_model_config_store(&app)?;
    if let Some(value) = id.as_deref() {
        if !store.configs.iter().any(|config| config.id == value) {
            return Err(format!("Unknown model configuration: {value}"));
        }
    }
    store.active_config_id = id;
    write_model_config_store(&app, &store)?;
    state.stop_voice_bridge();
    Ok(())
}

#[tauri::command]
async fn desktop_metadata(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
) -> Result<serde_json::Value, String> {
    let runtime = resolve_desktop_runtime(&app)?;
    let default_workspace = default_workspace_for_runtime(&runtime);
    let workspace = state
        .selected_workspace
        .lock()
        .map_err(|_| "selected workspace lock is poisoned".to_string())?
        .clone()
        .unwrap_or(default_workspace);

    tauri::async_runtime::spawn_blocking(move || read_desktop_metadata(runtime, workspace))
        .await
        .map_err(|error| format!("Desktop metadata task failed: {error}"))?
}

fn read_desktop_metadata(
    runtime: DesktopRuntime,
    workspace: PathBuf,
) -> Result<serde_json::Value, String> {
    let mut command = runtime.command(BridgeCommandKind::Metadata, &workspace);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);

    let output = command
        .spawn()
        .and_then(|process| process.wait_with_output())
        .map_err(|error| {
            format!(
                "Failed to read desktop metadata with {}: {error}",
                runtime.executable().display()
            )
        })?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let detail = bounded_error_preview(stderr.trim());
        if detail.is_empty() {
            return Err("Desktop metadata process failed.".to_string());
        }
        return Err(format!("Desktop metadata process failed: {detail}"));
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("Desktop metadata returned invalid JSON: {error}"))
}

#[tauri::command]
async fn ensure_desktop_voice_bridge(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
) -> Result<DesktopVoiceBridgeConfig, String> {
    if let Some(config) = state
        .voice_config
        .lock()
        .map_err(|_| "voice bridge config lock is poisoned".to_string())?
        .clone()
    {
        return Ok(config);
    }

    let runtime = resolve_desktop_runtime(&app)?;
    let default_workspace = default_workspace_for_runtime(&runtime);
    let workspace = state
        .selected_workspace
        .lock()
        .map_err(|_| "selected workspace lock is poisoned".to_string())?
        .clone()
        .unwrap_or(default_workspace);
    let active_model = {
        let _guard = state
            .model_configs
            .lock()
            .map_err(|_| "model config lock is poisoned".to_string())?;
        resolve_active_model_config(&app)?
    };
    let voice_settings = load_voice_settings_impl(&app)?;
    let log_path = bridge_log_path(&app, "desktop-voice-bridge.log");
    let (child, config) = tauri::async_runtime::spawn_blocking(move || {
        start_desktop_voice_bridge_process(
            runtime,
            workspace,
            active_model,
            voice_settings,
            log_path,
        )
    })
    .await
    .map_err(|error| format!("Desktop voice bridge startup task failed: {error}"))??;

    *state
        .voice_child
        .lock()
        .map_err(|_| "voice bridge process lock is poisoned".to_string())? = Some(child);
    *state
        .voice_config
        .lock()
        .map_err(|_| "voice bridge config lock is poisoned".to_string())? = Some(config.clone());

    Ok(config)
}

fn start_desktop_voice_bridge_process(
    runtime: DesktopRuntime,
    workspace: PathBuf,
    active_model: Option<ResolvedModelConfig>,
    voice_settings: VoiceSettings,
    log_path: Option<PathBuf>,
) -> Result<(Child, DesktopVoiceBridgeConfig), String> {
    initialize_bridge_log(
        log_path.as_deref(),
        "desktop voice bridge",
        runtime.mode_name(),
        runtime.executable(),
        runtime.app_workspace(),
        &workspace,
    );
    let mut command = runtime.command(BridgeCommandKind::Voice, &workspace);
    command.stdin(Stdio::null()).stdout(Stdio::piped());
    configure_bridge_stderr(&mut command, log_path.as_ref());
    if voice_settings.provider == "stepfun" {
        if !voice_settings.stepfun_key.trim().is_empty() {
            command.env("STEPFUN_API_KEY", voice_settings.stepfun_key.trim());
        }
        command
            .arg("--stepfun-voice")
            .arg(voice_settings.stepfun_voice.trim());
    } else {
        command.arg("--stt-url").arg(voice_settings.stt_url.trim());
        command.arg("--tts-url").arg(voice_settings.tts_url.trim());
    }
    if voice_settings.tts_enabled {
        command.arg("--tts-enabled");
    }
    if let Some(model) = active_model {
        command
            .env("REASONING_DESKTOP_MODEL_CONFIG_ID", model.id)
            .env("REASONING_DESKTOP_MODEL_BASE_URL", model.base_url)
            .env("REASONING_DESKTOP_MODEL_API_KEY", model.api_key)
            .env("REASONING_DESKTOP_MODEL_NAME", model.model_name);
    }
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);

    let startup_log_path = log_path.clone();
    let mut child = command.spawn().map_err(|error| {
        format_startup_error(
            "Desktop voice bridge",
            &format!(
                "Failed to start {} with {}: {error}",
                runtime.mode_name(),
                runtime.executable().display()
            ),
            startup_log_path.as_deref(),
        )
    })?;
    pipe_bridge_stderr(child.stderr.take(), log_path.clone());

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Desktop voice bridge stdout was unavailable.".to_string())?;
    let line = read_first_line_with_timeout(stdout, Duration::from_secs(60)).map_err(|error| {
        let _ = child.kill();
        let _ = child.wait();
        format_startup_error("Desktop voice bridge", &error, log_path.as_deref())
    })?;
    let config: DesktopVoiceBridgeConfig = serde_json::from_str(line.trim()).map_err(|error| {
        format_startup_error(
            "Desktop voice bridge",
            &format!("Returned invalid startup JSON: {error}"),
            log_path.as_deref(),
        )
    })?;

    Ok((child, config))
}

#[tauri::command]
fn load_voice_settings(app: AppHandle) -> Result<VoiceSettings, String> {
    load_voice_settings_impl(&app)
}

#[tauri::command]
fn save_voice_settings(
    app: AppHandle,
    state: tauri::State<'_, BridgeProcessState>,
    input: VoiceSettingsInput,
) -> Result<VoiceSettings, String> {
    let saved = save_voice_settings_impl(&app, input)?;
    state.stop_voice_bridge();
    Ok(saved)
}

#[tauri::command]
fn select_workspace_folder(
    state: tauri::State<'_, BridgeProcessState>,
) -> Result<Option<WorkspaceSelection>, String> {
    let Some(path) = platform::pick_workspace_folder()? else {
        return Ok(None);
    };

    state.stop_bridge();
    state.stop_voice_bridge();
    *state
        .selected_workspace
        .lock()
        .map_err(|_| "selected workspace lock is poisoned".to_string())? = Some(path.clone());

    Ok(Some(workspace_selection(path)))
}

#[tauri::command]
fn open_voice_window(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("voice") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }

    WebviewWindowBuilder::new(&app, "voice", WebviewUrl::App("index.html#/voice".into()))
        .title("语码 · 语音")
        .inner_size(520.0, 680.0)
        .min_inner_size(420.0, 520.0)
        .resizable(true)
        .decorations(true)
        .build()
        .map_err(|error| error.to_string())?;

    Ok(())
}

fn read_first_line_with_timeout(
    stdout: impl std::io::Read + Send + 'static,
    timeout: Duration,
) -> Result<String, String> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let result = reader
            .read_line(&mut line)
            .map(|_| line)
            .map_err(|error| format!("Failed to read desktop bridge startup JSON: {error}"));
        let _ = sender.send(result);
    });

    receiver
        .recv_timeout(timeout)
        .map_err(|_| "Timed out waiting for desktop bridge startup JSON.".to_string())?
}

fn bridge_log_path(app: &AppHandle, file_name: &str) -> Option<PathBuf> {
    app.path()
        .app_log_dir()
        .ok()
        .map(|directory| directory.join(file_name))
}

fn initialize_bridge_log(
    log_path: Option<&Path>,
    bridge_name: &str,
    runtime_mode: &str,
    executable: &Path,
    app_workspace: &Path,
    workspace: &Path,
) {
    let Some(path) = log_path else {
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = File::create(path) {
        let _ = writeln!(file, "{bridge_name} starting");
        let _ = writeln!(file, "runtime={runtime_mode}");
        let _ = writeln!(file, "executable={}", executable.display());
        let _ = writeln!(file, "app_workspace={}", app_workspace.display());
        let _ = writeln!(file, "workspace={}", workspace.display());
        let _ = writeln!(file, "stderr:");
    }
}

fn configure_bridge_stderr(command: &mut Command, log_path: Option<&PathBuf>) {
    if log_path.is_some() {
        command.stderr(Stdio::piped());
    } else {
        command.stderr(Stdio::null());
    }
}

fn pipe_bridge_stderr(stderr: Option<ChildStderr>, log_path: Option<PathBuf>) {
    let (Some(stderr), Some(path)) = (stderr, log_path) else {
        return;
    };
    thread::spawn(move || {
        let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) else {
            return;
        };
        let mut reader = BufReader::new(stderr);
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    let _ = file.write_all(line.as_bytes());
                }
                Err(error) => {
                    let _ = writeln!(file, "stderr read failed: {error}");
                    break;
                }
            }
        }
    });
}

fn format_startup_error(label: &str, detail: &str, log_path: Option<&Path>) -> String {
    let mut message = format!("{label} startup failed: {detail}");
    if let Some(path) = log_path {
        message.push_str(&format!(" Diagnostics: {}", path.display()));
    }
    message
}

fn bounded_error_preview(value: &str) -> String {
    const LIMIT: usize = 600;
    let normalized = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.len() <= LIMIT {
        return normalized;
    }
    let preview = normalized.chars().take(LIMIT).collect::<String>();
    format!("{preview}...")
}

fn model_config_store_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(MODEL_CONFIG_FILE))
        .map_err(|error| format!("Could not resolve app config directory: {error}"))
}

fn voice_config_store_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(VOICE_CONFIG_FILE))
        .map_err(|error| format!("Could not resolve app config directory: {error}"))
}

fn read_model_config_store(app: &AppHandle) -> Result<ModelConfigStore, String> {
    let path = model_config_store_path(app)?;
    if !path.exists() {
        return Ok(ModelConfigStore::default());
    }
    let content = fs::read_to_string(&path)
        .map_err(|error| format!("Could not read {}: {error}", path.display()))?;
    serde_json::from_str(&content)
        .map_err(|error| format!("Invalid model configuration file: {error}"))
}

fn write_model_config_store(app: &AppHandle, store: &ModelConfigStore) -> Result<(), String> {
    let path = model_config_store_path(app)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Could not create {}: {error}", parent.display()))?;
    }
    let content = serde_json::to_vec_pretty(store)
        .map_err(|error| format!("Could not serialize model configurations: {error}"))?;
    fs::write(&path, content)
        .map_err(|error| format!("Could not write {}: {error}", path.display()))
}

fn read_voice_config_store(app: &AppHandle) -> Result<StoredVoiceConfig, String> {
    let path = voice_config_store_path(app)?;
    if !path.exists() {
        return Ok(StoredVoiceConfig::default());
    }
    let content = fs::read_to_string(&path)
        .map_err(|error| format!("Could not read {}: {error}", path.display()))?;
    serde_json::from_str(&content)
        .map_err(|error| format!("Invalid voice configuration file: {error}"))
}

fn write_voice_config_store(app: &AppHandle, store: &StoredVoiceConfig) -> Result<(), String> {
    let path = voice_config_store_path(app)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Could not create {}: {error}", parent.display()))?;
    }
    let content = serde_json::to_vec_pretty(store)
        .map_err(|error| format!("Could not serialize voice configuration: {error}"))?;
    fs::write(&path, content)
        .map_err(|error| format!("Could not write {}: {error}", path.display()))
}

fn model_config_summary(config: &StoredModelConfig) -> ModelConfigSummary {
    ModelConfigSummary {
        id: config.id.clone(),
        label: config.display_name.clone(),
        base_url: config.base_url.clone(),
        model_name: config.model_name.clone(),
        has_api_key: read_model_api_key(&config.id).is_ok(),
        source: "custom",
    }
}

fn resolve_active_model_config(app: &AppHandle) -> Result<Option<ResolvedModelConfig>, String> {
    let store = read_model_config_store(app)?;
    let Some(id) = store.active_config_id else {
        return Ok(None);
    };
    resolve_model_config_by_id(app, &id).map(Some)
}

fn resolve_model_config_by_id(app: &AppHandle, id: &str) -> Result<ResolvedModelConfig, String> {
    let store = read_model_config_store(app)?;
    let config = store
        .configs
        .into_iter()
        .find(|config| config.id == id)
        .ok_or_else(|| format!("Unknown model configuration: {id}"))?;
    Ok(ResolvedModelConfig {
        api_key: read_model_api_key(id)?,
        id: config.id,
        display_name: config.display_name,
        base_url: config.base_url,
        model_name: config.model_name,
    })
}

fn read_model_api_key(id: &str) -> Result<String, String> {
    keyring::Entry::new(MODEL_KEYRING_SERVICE, id)
        .map_err(|error| format!("Could not open system credential store: {error}"))?
        .get_password()
        .map_err(|error| format!("Could not read API Key from system credential store: {error}"))
}

fn write_model_api_key(id: &str, api_key: &str) -> Result<(), String> {
    keyring::Entry::new(MODEL_KEYRING_SERVICE, id)
        .map_err(|error| format!("Could not open system credential store: {error}"))?
        .set_password(api_key)
        .map_err(|error| format!("Could not save API Key to system credential store: {error}"))
}

fn read_voice_stepfun_key() -> Result<String, String> {
    keyring::Entry::new(VOICE_KEYRING_SERVICE, "stepfun")
        .map_err(|error| format!("Could not open system credential store: {error}"))?
        .get_password()
        .map_err(|error| format!("Could not read StepFun Key from system credential store: {error}"))
}

fn write_voice_stepfun_key(api_key: &str) -> Result<(), String> {
    keyring::Entry::new(VOICE_KEYRING_SERVICE, "stepfun")
        .map_err(|error| format!("Could not open system credential store: {error}"))?
        .set_password(api_key)
        .map_err(|error| format!("Could not save StepFun Key to system credential store: {error}"))
}

fn load_voice_settings_impl(app: &AppHandle) -> Result<VoiceSettings, String> {
    let stored = read_voice_config_store(app)?;
    let stepfun_key = read_voice_stepfun_key().unwrap_or_default();
    Ok(VoiceSettings {
        provider: normalize_voice_provider(&stored.provider).to_string(),
        stt_url: stored.stt_url,
        tts_url: stored.tts_url,
        stepfun_voice: stored.stepfun_voice,
        stepfun_key: String::new(),
        has_stepfun_key: !stepfun_key.trim().is_empty(),
        tts_enabled: stored.tts_enabled,
    })
}

fn save_voice_settings_impl(
    app: &AppHandle,
    input: VoiceSettingsInput,
) -> Result<VoiceSettings, String> {
    let provider = normalize_voice_provider(&input.provider).to_string();
    let stt_url = required_value(input.stt_url, "STT URL")?;
    let tts_url = required_value(input.tts_url, "TTS URL")?;
    let stepfun_voice = required_value(input.stepfun_voice, "StepFun voice")?;
    let stepfun_key = input.stepfun_key.trim().to_string();
    let existing_stepfun_key = read_voice_stepfun_key().unwrap_or_default();

    if provider == "stepfun" && stepfun_key.is_empty() && existing_stepfun_key.trim().is_empty() {
        return Err("StepFun Key is required when StepFun voice is enabled.".to_string());
    }
    if !stepfun_key.is_empty() {
        write_voice_stepfun_key(&stepfun_key)?;
    }

    let stored = StoredVoiceConfig {
        provider: provider.clone(),
        stepfun_voice: stepfun_voice.clone(),
        stt_url: stt_url.clone(),
        tts_url: tts_url.clone(),
        tts_enabled: input.tts_enabled,
    };
    write_voice_config_store(app, &stored)?;

    Ok(VoiceSettings {
        provider,
        stt_url,
        tts_url,
        stepfun_voice,
        stepfun_key: String::new(),
        has_stepfun_key: !stepfun_key.is_empty() || !existing_stepfun_key.trim().is_empty(),
        tts_enabled: input.tts_enabled,
    })
}

fn normalize_voice_provider(provider: &str) -> &str {
    if provider == "stepfun" {
        "stepfun"
    } else {
        "custom"
    }
}

fn required_value(value: String, label: &str) -> Result<String, String> {
    let value = value.trim().to_string();
    if value.is_empty() {
        Err(format!("{label} is required."))
    } else {
        Ok(value)
    }
}

fn new_model_config_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("custom-{nanos}")
}

fn find_app_workspace_root() -> Option<PathBuf> {
    if let Ok(explicit) = env::var("REASONING_WORKSPACE") {
        let path = PathBuf::from(explicit);
        if is_reasoning_workspace(&path) {
            return Some(path);
        }
    }

    for root in workspace_search_roots() {
        for candidate in root.ancestors() {
            if is_reasoning_workspace(candidate) {
                return Some(candidate.to_path_buf());
            }
        }
    }
    None
}

fn workspace_search_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(current) = env::current_dir() {
        roots.push(current);
    }
    if let Ok(exe) = env::current_exe() {
        roots.push(exe);
    }
    roots.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")));
    roots
}

fn find_uv_binary() -> Option<PathBuf> {
    if let Ok(explicit) = env::var("REASONING_UV_PATH") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }

    if let Some(path) = platform::find_executable_on_path("uv") {
        return Some(path);
    }

    platform::uv_candidates()
        .into_iter()
        .find(|path| path.is_file())
}

fn resolve_desktop_runtime(app: &AppHandle) -> Result<DesktopRuntime, String> {
    if let Some(runtime) = app_owned_runtime(app) {
        return Ok(runtime);
    }

    let app_workspace = find_app_workspace_root().ok_or_else(|| {
        "Could not locate bundled desktop runtime or voice-code pyproject.toml. For development, run from the repository or set REASONING_WORKSPACE.".to_string()
    })?;
    let uv = find_uv_binary().ok_or_else(|| {
        "Could not locate bundled desktop runtime or uv. For development, set REASONING_UV_PATH to the full uv path.".to_string()
    })?;
    Ok(DesktopRuntime::developer_uv(uv, app_workspace))
}

fn app_owned_runtime(app: &AppHandle) -> Option<DesktopRuntime> {
    app.path()
        .resource_dir()
        .ok()
        .and_then(|directory| DesktopRuntime::discover_app_owned(&directory))
}

fn default_workspace_for_runtime(runtime: &DesktopRuntime) -> PathBuf {
    if runtime.mode_name() == "developer-uv" {
        return runtime.app_workspace().to_path_buf();
    }

    env::current_dir().unwrap_or_else(|_| runtime.app_workspace().to_path_buf())
}

fn is_reasoning_workspace(path: &Path) -> bool {
    let pyproject = path.join("pyproject.toml");
    fs::read_to_string(pyproject)
        .map(|content| content.contains("name = \"voice-code\""))
        .unwrap_or(false)
}

pub fn run() {
    let app = tauri::Builder::default()
        .manage(BridgeProcessState::default())
        .invoke_handler(tauri::generate_handler![
            desktop_diagnostics,
            open_diagnostics_path,
            desktop_metadata,
            ensure_desktop_bridge,
            ensure_desktop_voice_bridge,
            load_voice_settings,
            save_voice_settings,
            select_workspace_folder,
            open_voice_window,
            list_model_configs,
            save_model_config,
            delete_model_config,
            resolve_model_config,
            set_active_model_config
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if window.label() == "main" {
                    if let Some(state) = window.try_state::<BridgeProcessState>() {
                        state.stop_bridge();
                        state.stop_voice_bridge();
                    }
                } else if window.label() == "voice" {
                    if let Some(state) = window.try_state::<BridgeProcessState>() {
                        state.stop_voice_bridge();
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app_handle.try_state::<BridgeProcessState>() {
                state.stop_voice_bridge();
                state.stop_bridge();
            }
        }
    });
}

fn workspace_selection(path: PathBuf) -> WorkspaceSelection {
    let label = workspace_label(&path);
    let path = path.to_string_lossy().to_string();
    WorkspaceSelection { label, path }
}

fn workspace_label(path: &Path) -> String {
    let rendered = path.to_string_lossy();
    let trimmed = rendered.trim_end_matches(|ch| ch == '\\' || ch == '/');
    if let Some(drive) = trimmed.strip_suffix(':') {
        if drive.len() == 1 && drive.chars().all(|ch| ch.is_ascii_alphabetic()) {
            return drive.to_string();
        }
    }

    if let Some(name) = path
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
    {
        return name.to_string();
    }

    if let Some(name) = trimmed
        .rsplit(|ch| ch == '\\' || ch == '/')
        .next()
        .filter(|name| !name.is_empty())
    {
        return name.to_string();
    }

    "workspace".to_string()
}

fn desktop_diagnostics_payload(
    app_log_dir: Option<PathBuf>,
    runtime: Option<&DesktopRuntime>,
    uv_path: Option<PathBuf>,
    workspace_root: Option<PathBuf>,
) -> DesktopDiagnostics {
    DesktopDiagnostics {
        runtime_mode: runtime
            .map(DesktopRuntime::mode_name)
            .unwrap_or("unavailable"),
        runtime_executable: runtime.map(|value| display_path(value.executable())),
        uv_path: uv_path.map(display_path),
        workspace_root: workspace_root.map(display_path),
        text_bridge_log: app_log_dir
            .as_ref()
            .map(|directory| display_path(directory.join("desktop-bridge.log"))),
        voice_bridge_log: app_log_dir
            .as_ref()
            .map(|directory| display_path(directory.join("desktop-voice-bridge.log"))),
        app_log_dir: app_log_dir.map(display_path),
    }
}

fn display_path(path: impl AsRef<Path>) -> String {
    path.as_ref().to_string_lossy().to_string()
}

#[cfg(test)]
mod tests {
    use super::{
        bounded_error_preview, desktop_diagnostics_payload, format_startup_error,
        open_diagnostics_path, workspace_label,
    };
    use std::{
        path::{Path, PathBuf},
        time::{SystemTime, UNIX_EPOCH},
    };

    #[test]
    fn workspace_label_uses_folder_name() {
        assert_eq!(workspace_label(Path::new("/tmp/project")), "project");
    }

    #[test]
    fn workspace_label_uses_windows_drive_when_path_is_drive_root() {
        assert_eq!(workspace_label(Path::new(r"D:\")), "D");
    }

    #[test]
    fn startup_error_points_to_diagnostics_log() {
        let message = format_startup_error(
            "Desktop bridge",
            "Timed out waiting for desktop bridge startup JSON.",
            Some(Path::new("/tmp/voice-code/desktop-bridge.log")),
        );

        assert!(message.contains("Desktop bridge startup failed"));
        assert!(message.contains("Timed out waiting"));
        assert!(message.contains("/tmp/voice-code/desktop-bridge.log"));
    }

    #[test]
    fn bounded_error_preview_normalizes_and_truncates_safely() {
        let message = format!("{}\n{}", "错误".repeat(500), "tail");
        let preview = bounded_error_preview(&message);

        assert!(preview.ends_with("..."));
        assert!(preview.len() < message.len());
    }

    #[test]
    fn desktop_diagnostics_payload_reports_runtime_and_logs() {
        let diagnostics = desktop_diagnostics_payload(
            Some(PathBuf::from("/tmp/voice-code/logs")),
            Some(&super::DesktopRuntime::developer_uv(
                PathBuf::from("/usr/local/bin/uv"),
                PathBuf::from("/repo/new"),
            )),
            Some(PathBuf::from("/usr/local/bin/uv")),
            Some(PathBuf::from("/repo/new")),
        );

        assert_eq!(diagnostics.runtime_mode, "developer-uv");
        assert_eq!(
            diagnostics.runtime_executable.as_deref(),
            Some("/usr/local/bin/uv")
        );
        assert_eq!(diagnostics.uv_path.as_deref(), Some("/usr/local/bin/uv"));
        assert_eq!(diagnostics.workspace_root.as_deref(), Some("/repo/new"));
        assert_eq!(
            diagnostics.text_bridge_log.as_deref(),
            Some("/tmp/voice-code/logs/desktop-bridge.log")
        );
        assert_eq!(
            diagnostics.voice_bridge_log.as_deref(),
            Some("/tmp/voice-code/logs/desktop-voice-bridge.log")
        );
    }

    #[test]
    fn desktop_diagnostics_payload_reports_unavailable_runtime() {
        let diagnostics = desktop_diagnostics_payload(None, None, None, None);

        assert_eq!(diagnostics.runtime_mode, "unavailable");
        assert!(diagnostics.runtime_executable.is_none());
        assert!(diagnostics.uv_path.is_none());
    }

    #[test]
    fn open_diagnostics_path_rejects_relative_path() {
        let error = open_diagnostics_path("logs".to_string()).unwrap_err();

        assert!(error.contains("must be absolute"));
    }

    #[test]
    fn open_diagnostics_path_rejects_missing_path() {
        let missing = std::env::temp_dir().join(format!(
            "voice-code-missing-diagnostics-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let error = open_diagnostics_path(super::display_path(&missing)).unwrap_err();

        assert!(error.contains("does not exist"));
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn system_keyring_round_trip() {
        use super::MODEL_KEYRING_SERVICE;

        let id = format!("test-{}", std::process::id());
        let entry = keyring::Entry::new(MODEL_KEYRING_SERVICE, &id).unwrap();
        entry.set_password("temporary-test-secret").unwrap();
        assert_eq!(entry.get_password().unwrap(), "temporary-test-secret");
        entry.delete_credential().unwrap();
    }
}
