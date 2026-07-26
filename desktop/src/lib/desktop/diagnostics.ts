import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "../bridge/tauri-client";

export type DesktopDiagnostics = {
  runtimeMode: string;
  runtimeExecutable: string | null;
  uvPath: string | null;
  workspaceRoot: string | null;
  appLogDir: string | null;
  textBridgeLog: string | null;
  voiceBridgeLog: string | null;
};

export async function readDesktopDiagnostics(): Promise<DesktopDiagnostics | null> {
  if (!isTauriRuntime()) {
    return null;
  }

  return invoke<DesktopDiagnostics>("desktop_diagnostics");
}

export async function openDiagnosticsPath(path: string): Promise<boolean> {
  if (!isTauriRuntime()) {
    return false;
  }

  await invoke("open_diagnostics_path", { path });
  return true;
}
