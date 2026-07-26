use std::{
    env,
    path::PathBuf,
    process::{Command, Stdio},
};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Clone, Copy)]
#[allow(dead_code)]
enum HostPlatform {
    Windows,
    Unix,
}

pub fn pick_workspace_folder() -> Result<Option<PathBuf>, String> {
    pick_workspace_folder_impl()
}

pub fn terminate_child_processes(parent_pid: u32) {
    terminate_child_processes_impl(parent_pid);
}

pub fn uv_candidates() -> Vec<PathBuf> {
    uv_candidate_paths_for_platform(
        current_platform(),
        env::var("HOME").ok().as_deref(),
        env::var("USERPROFILE").ok().as_deref(),
        env::var("LOCALAPPDATA").ok().as_deref(),
    )
}

pub fn find_executable_on_path(name: &str) -> Option<PathBuf> {
    let path_var = env::var_os("PATH")?;
    for dir in env::split_paths(&path_var) {
        for executable in executable_names(name) {
            let candidate = dir.join(executable);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

pub fn open_path(path: &std::path::Path) -> Result<(), String> {
    let (program, args) = open_path_command_for_platform(current_platform(), path);
    let mut command = Command::new(program);
    command.args(args).stdin(Stdio::null());
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);
    let status = command
        .status()
        .map_err(|error| format!("Failed to open diagnostics path: {error}"))?;
    if status.success() {
        return Ok(());
    }
    Err(format!(
        "Open diagnostics path exited with status: {status}"
    ))
}

#[cfg(target_os = "macos")]
fn pick_workspace_folder_impl() -> Result<Option<PathBuf>, String> {
    let output = Command::new("/usr/bin/osascript")
        .args([
            "-e",
            "POSIX path of (choose folder with prompt \"选择项目文件夹\")",
        ])
        .stdin(Stdio::null())
        .output()
        .map_err(|error| format!("Failed to open folder picker: {error}"))?;

    if output.status.success() {
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if path.is_empty() {
            return Ok(None);
        }
        return Ok(Some(PathBuf::from(path)));
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    if stderr.contains("User canceled") || stderr.contains("-128") {
        return Ok(None);
    }

    Err(format!("Folder picker failed: {}", stderr.trim()))
}

#[cfg(target_os = "windows")]
fn pick_workspace_folder_impl() -> Result<Option<PathBuf>, String> {
    let script = [
        "Add-Type -AssemblyName System.Windows.Forms;",
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;",
        "$dialog.Description = '选择项目文件夹';",
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
        "  [Console]::Out.Write($dialog.SelectedPath)",
        "}",
    ]
    .join(" ");
    let mut command = Command::new("powershell.exe");
    command
        .args(["-NoProfile", "-STA", "-Command", &script])
        .stdin(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW);
    let output = command
        .output()
        .map_err(|error| format!("Failed to open folder picker: {error}"))?;

    if output.status.success() {
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if path.is_empty() {
            return Ok(None);
        }
        return Ok(Some(PathBuf::from(path)));
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!("Folder picker failed: {}", stderr.trim()))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn pick_workspace_folder_impl() -> Result<Option<PathBuf>, String> {
    Err("Folder selection is only available in the desktop app on macOS and Windows.".to_string())
}

#[cfg(target_os = "windows")]
fn terminate_child_processes_impl(parent_pid: u32) {
    let mut command = Command::new("taskkill");
    command
        .args(["/PID", &parent_pid.to_string(), "/T", "/F"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW);
    let _ = command.status();
}

#[cfg(not(target_os = "windows"))]
fn terminate_child_processes_impl(parent_pid: u32) {
    let _ = Command::new("/usr/bin/pkill")
        .args(["-TERM", "-P", &parent_pid.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(target_os = "windows")]
fn current_platform() -> HostPlatform {
    HostPlatform::Windows
}

#[cfg(not(target_os = "windows"))]
fn current_platform() -> HostPlatform {
    HostPlatform::Unix
}

fn executable_names(name: &str) -> Vec<String> {
    if matches!(current_platform(), HostPlatform::Windows) && !name.ends_with(".exe") {
        return vec![name.to_string(), format!("{name}.exe")];
    }
    vec![name.to_string()]
}

fn open_path_command_for_platform(
    platform: HostPlatform,
    path: &std::path::Path,
) -> (&'static str, Vec<String>) {
    let path = path.to_string_lossy().to_string();
    match platform {
        HostPlatform::Windows => (
            "powershell.exe",
            vec![
                "-NoProfile".to_string(),
                "-Command".to_string(),
                "Start-Process -LiteralPath $args[0]".to_string(),
                path,
            ],
        ),
        HostPlatform::Unix => {
            if cfg!(target_os = "macos") {
                ("/usr/bin/open", vec![path])
            } else {
                ("xdg-open", vec![path])
            }
        }
    }
}

fn uv_candidate_paths_for_platform(
    platform: HostPlatform,
    home: Option<&str>,
    user_profile: Option<&str>,
    local_app_data: Option<&str>,
) -> Vec<PathBuf> {
    match platform {
        HostPlatform::Windows => {
            let mut candidates = Vec::new();
            if let Some(profile) = user_profile.or(home) {
                let profile = PathBuf::from(profile);
                candidates.push(profile.join(".local").join("bin").join("uv.exe"));
                candidates.push(profile.join(".cargo").join("bin").join("uv.exe"));
            }
            if let Some(local_app_data) = local_app_data {
                candidates.push(
                    PathBuf::from(local_app_data)
                        .join("Programs")
                        .join("uv")
                        .join("uv.exe"),
                );
            }
            candidates
        }
        HostPlatform::Unix => {
            let mut candidates = Vec::new();
            if let Some(home) = home {
                candidates.push(PathBuf::from(home).join(".local/bin/uv"));
            }
            candidates.push(PathBuf::from("/opt/homebrew/bin/uv"));
            candidates.push(PathBuf::from("/usr/local/bin/uv"));
            candidates
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{open_path_command_for_platform, uv_candidate_paths_for_platform, HostPlatform};
    use std::path::{Path, PathBuf};

    #[test]
    fn open_path_command_uses_powershell_literal_path_on_windows() {
        let (program, args) =
            open_path_command_for_platform(HostPlatform::Windows, Path::new(r"C:\Logs\App"));

        assert_eq!(program, "powershell.exe");
        assert_eq!(args[0], "-NoProfile");
        assert!(args[2].contains("Start-Process"));
        assert_eq!(args[3], r"C:\Logs\App");
    }

    #[test]
    fn windows_uv_candidates_include_common_exe_locations() {
        let candidates = uv_candidate_paths_for_platform(
            HostPlatform::Windows,
            None,
            Some(r"C:\Users\dev"),
            Some(r"C:\Users\dev\AppData\Local"),
        );

        assert_eq!(
            candidates,
            vec![
                PathBuf::from(r"C:\Users\dev").join(".local/bin/uv.exe"),
                PathBuf::from(r"C:\Users\dev").join(".cargo/bin/uv.exe"),
                PathBuf::from(r"C:\Users\dev\AppData\Local").join("Programs/uv/uv.exe"),
            ]
        );
    }

    #[test]
    fn unix_uv_candidates_preserve_existing_locations() {
        let candidates =
            uv_candidate_paths_for_platform(HostPlatform::Unix, Some("/Users/dev"), None, None);

        assert_eq!(
            candidates,
            vec![
                PathBuf::from("/Users/dev/.local/bin/uv"),
                PathBuf::from("/opt/homebrew/bin/uv"),
                PathBuf::from("/usr/local/bin/uv"),
            ]
        );
    }
}
