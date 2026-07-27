import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "../bridge/tauri-client";

export async function openVoiceWindow(): Promise<void> {
  if (!isTauriRuntime()) {
    window.open("#/voice", "voice", "popup,width=520,height=680");
    return;
  }

  await invoke("open_voice_window");
}
