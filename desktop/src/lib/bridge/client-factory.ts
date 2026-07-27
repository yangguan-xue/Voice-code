import { MockBridgeClient } from "./mock-client";
import type { BridgeEvent, DesktopBridgeClient } from "./protocol";
import { isTauriRuntime, TauriBridgeClient } from "./tauri-client";
import { WebSocketBridgeClient } from "./websocket-client";

type EmitBridgeEvent = (event: BridgeEvent) => void;

export function createDesktopBridgeClient(emit: EmitBridgeEvent): DesktopBridgeClient {
  const url = import.meta.env.VITE_DESKTOP_BRIDGE_URL;
  const token = import.meta.env.VITE_DESKTOP_BRIDGE_TOKEN;

  if (url && token) {
    return new WebSocketBridgeClient({ url, token, emit });
  }

  if (isTauriRuntime()) {
    return new TauriBridgeClient(emit);
  }

  return new MockBridgeClient(emit);
}
