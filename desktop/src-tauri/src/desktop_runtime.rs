use std::{
    path::{Path, PathBuf},
    process::Command,
};

#[derive(Debug, Clone)]
pub enum DesktopRuntime {
    DeveloperUv(DeveloperUvRuntime),
    AppOwned(AppOwnedRuntime),
}

#[derive(Debug, Clone)]
pub struct DeveloperUvRuntime {
    uv: PathBuf,
    app_workspace: PathBuf,
}

#[derive(Debug, Clone)]
pub struct AppOwnedRuntime {
    runtime_root: PathBuf,
    launcher: PathBuf,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum BridgeCommandKind {
    Text,
    Voice,
    Metadata,
}

impl DesktopRuntime {
    pub fn developer_uv(uv: PathBuf, app_workspace: PathBuf) -> Self {
        Self::DeveloperUv(DeveloperUvRuntime { uv, app_workspace })
    }

    pub fn app_owned(runtime_root: PathBuf, launcher: PathBuf) -> Self {
        Self::AppOwned(AppOwnedRuntime {
            runtime_root,
            launcher,
        })
    }

    pub fn discover_app_owned(resource_dir: &Path) -> Option<Self> {
        let runtime_root = resource_dir.join("runtime");
        app_owned_launcher_names()
            .iter()
            .map(|name| runtime_root.join(name))
            .find(|launcher| launcher.is_file())
            .map(|launcher| Self::app_owned(runtime_root, launcher))
    }

    pub fn command(&self, kind: BridgeCommandKind, workspace: &Path) -> Command {
        match self {
            Self::DeveloperUv(runtime) => runtime.command(kind, workspace),
            Self::AppOwned(runtime) => runtime.command(kind, workspace),
        }
    }

    pub fn executable(&self) -> &Path {
        match self {
            Self::DeveloperUv(runtime) => &runtime.uv,
            Self::AppOwned(runtime) => &runtime.launcher,
        }
    }

    pub fn app_workspace(&self) -> &Path {
        match self {
            Self::DeveloperUv(runtime) => &runtime.app_workspace,
            Self::AppOwned(runtime) => &runtime.runtime_root,
        }
    }

    pub fn mode_name(&self) -> &'static str {
        match self {
            Self::DeveloperUv(_) => "developer-uv",
            Self::AppOwned(_) => "app-owned",
        }
    }
}

impl DeveloperUvRuntime {
    fn command(&self, kind: BridgeCommandKind, workspace: &Path) -> Command {
        let mut command = Command::new(&self.uv);
        command.current_dir(&self.app_workspace);
        match kind {
            BridgeCommandKind::Text => {
                command
                    .args(["run", "reasoning-desktop-bridge", "--port", "0"])
                    .arg("--workspace")
                    .arg(workspace);
            }
            BridgeCommandKind::Voice => {
                command
                    .args(["run", "reasoning-desktop-voice-bridge", "--port", "0"])
                    .arg("--workspace")
                    .arg(workspace);
            }
            BridgeCommandKind::Metadata => {
                command
                    .args(["run", "python", "-m", "voice_code.desktop.metadata"])
                    .arg("--workspace")
                    .arg(workspace);
            }
        }
        command
    }
}

impl AppOwnedRuntime {
    fn command(&self, kind: BridgeCommandKind, workspace: &Path) -> Command {
        let mut command = Command::new(&self.launcher);
        command.current_dir(&self.runtime_root);
        command
            .arg(kind.entrypoint())
            .args(kind.bridge_args())
            .arg("--workspace")
            .arg(workspace);
        command
    }
}

impl BridgeCommandKind {
    fn entrypoint(self) -> &'static str {
        match self {
            Self::Text => "reasoning-desktop-bridge",
            Self::Voice => "reasoning-desktop-voice-bridge",
            Self::Metadata => "voice-code-desktop-metadata",
        }
    }

    fn bridge_args(self) -> &'static [&'static str] {
        match self {
            Self::Text | Self::Voice => &["--port", "0"],
            Self::Metadata => &[],
        }
    }
}

fn app_owned_launcher_names() -> &'static [&'static str] {
    app_owned_launcher_names_for(cfg!(target_os = "windows"))
}

fn app_owned_launcher_names_for(is_windows: bool) -> &'static [&'static str] {
    if is_windows {
        &["voice-code-agent.exe", "voice-code-agent.cmd"]
    } else {
        &["voice-code-agent"]
    }
}

#[cfg(test)]
mod tests {
    use super::{
        app_owned_launcher_names, app_owned_launcher_names_for, BridgeCommandKind, DesktopRuntime,
    };
    use std::{
        fs::{self, File},
        path::{Path, PathBuf},
        time::{SystemTime, UNIX_EPOCH},
    };

    #[test]
    fn developer_runtime_builds_text_bridge_command() {
        let runtime = DesktopRuntime::developer_uv(
            PathBuf::from("/usr/local/bin/uv"),
            PathBuf::from("/repo/new"),
        );
        let command = runtime.command(BridgeCommandKind::Text, Path::new("/work/project"));
        let args = command
            .get_args()
            .map(|arg| arg.to_string_lossy().to_string())
            .collect::<Vec<_>>();

        assert_eq!(command.get_program(), Path::new("/usr/local/bin/uv"));
        assert_eq!(command.get_current_dir(), Some(Path::new("/repo/new")));
        assert_eq!(
            args,
            vec![
                "run",
                "reasoning-desktop-bridge",
                "--port",
                "0",
                "--workspace",
                "/work/project",
            ]
        );
        assert_eq!(runtime.mode_name(), "developer-uv");
    }

    #[test]
    fn developer_runtime_builds_voice_bridge_command() {
        let runtime = DesktopRuntime::developer_uv(
            PathBuf::from("/usr/local/bin/uv"),
            PathBuf::from("/repo/new"),
        );
        let command = runtime.command(BridgeCommandKind::Voice, Path::new("/work/project"));
        let args = command
            .get_args()
            .map(|arg| arg.to_string_lossy().to_string())
            .collect::<Vec<_>>();

        assert_eq!(
            args,
            vec![
                "run",
                "reasoning-desktop-voice-bridge",
                "--port",
                "0",
                "--workspace",
                "/work/project",
            ]
        );
    }

    #[test]
    fn developer_runtime_builds_metadata_command() {
        let runtime = DesktopRuntime::developer_uv(
            PathBuf::from("/usr/local/bin/uv"),
            PathBuf::from("/repo/new"),
        );
        let command = runtime.command(BridgeCommandKind::Metadata, Path::new("/work/project"));
        let args = command
            .get_args()
            .map(|arg| arg.to_string_lossy().to_string())
            .collect::<Vec<_>>();

        assert_eq!(
            args,
            vec![
                "run",
                "python",
                "-m",
                "voice_code.desktop.metadata",
                "--workspace",
                "/work/project",
            ]
        );
    }

    #[test]
    fn app_owned_runtime_builds_text_bridge_command() {
        let runtime = DesktopRuntime::app_owned(
            PathBuf::from("/app/resources/runtime"),
            PathBuf::from("/app/resources/runtime/voice-code-agent"),
        );
        let command = runtime.command(BridgeCommandKind::Text, Path::new("/work/project"));
        let args = command
            .get_args()
            .map(|arg| arg.to_string_lossy().to_string())
            .collect::<Vec<_>>();

        assert_eq!(
            command.get_program(),
            Path::new("/app/resources/runtime/voice-code-agent")
        );
        assert_eq!(
            command.get_current_dir(),
            Some(Path::new("/app/resources/runtime"))
        );
        assert_eq!(
            args,
            vec![
                "reasoning-desktop-bridge",
                "--port",
                "0",
                "--workspace",
                "/work/project",
            ]
        );
        assert_eq!(runtime.mode_name(), "app-owned");
    }

    #[test]
    fn app_owned_runtime_builds_metadata_command() {
        let runtime = DesktopRuntime::app_owned(
            PathBuf::from("/app/resources/runtime"),
            PathBuf::from("/app/resources/runtime/voice-code-agent"),
        );
        let command = runtime.command(BridgeCommandKind::Metadata, Path::new("/work/project"));
        let args = command
            .get_args()
            .map(|arg| arg.to_string_lossy().to_string())
            .collect::<Vec<_>>();

        assert_eq!(
            args,
            vec![
                "voice-code-desktop-metadata",
                "--workspace",
                "/work/project",
            ]
        );
    }

    #[test]
    fn discover_app_owned_requires_existing_launcher() {
        let resource_dir = unique_test_dir("desktop-runtime");
        let runtime_dir = resource_dir.join("runtime");
        fs::create_dir_all(&runtime_dir).unwrap();
        assert!(DesktopRuntime::discover_app_owned(&resource_dir).is_none());

        File::create(runtime_dir.join(app_owned_launcher_names()[0])).unwrap();
        let runtime = DesktopRuntime::discover_app_owned(&resource_dir).unwrap();

        assert_eq!(runtime.mode_name(), "app-owned");
        assert_eq!(runtime.app_workspace(), runtime_dir.as_path());
        fs::remove_dir_all(resource_dir).unwrap();
    }

    #[test]
    fn app_owned_launcher_names_are_platform_specific() {
        assert_eq!(app_owned_launcher_names_for(false), &["voice-code-agent"]);
        assert_eq!(
            app_owned_launcher_names_for(true),
            &["voice-code-agent.exe", "voice-code-agent.cmd"]
        );
    }

    #[test]
    fn windows_discovery_accepts_cmd_launcher_when_exe_is_missing() {
        let resource_dir = unique_test_dir("desktop-runtime-windows-cmd");
        let runtime_dir = resource_dir.join("runtime");
        fs::create_dir_all(&runtime_dir).unwrap();
        File::create(runtime_dir.join("voice-code-agent.cmd")).unwrap();

        let runtime = super::app_owned_launcher_names_for(true)
            .iter()
            .map(|name| runtime_dir.join(name))
            .find(|launcher| launcher.is_file())
            .map(|launcher| DesktopRuntime::app_owned(runtime_dir.clone(), launcher))
            .unwrap();

        assert_eq!(
            runtime.executable(),
            runtime_dir.join("voice-code-agent.cmd").as_path()
        );
        fs::remove_dir_all(resource_dir).unwrap();
    }

    fn unique_test_dir(prefix: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("{prefix}-{nanos}"))
    }
}
