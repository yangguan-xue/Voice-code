import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "../bridge/tauri-client";

export type WorkspaceSelection = {
  label: string;
  path: string;
};

export async function selectWorkspaceFolder(): Promise<WorkspaceSelection | null> {
  if (!isTauriRuntime()) {
    return null;
  }

  return invoke<WorkspaceSelection | null>("select_workspace_folder");
}
