/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DESKTOP_BRIDGE_URL?: string;
  readonly VITE_DESKTOP_BRIDGE_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  readonly __TAURI_INTERNALS__?: unknown;
}
