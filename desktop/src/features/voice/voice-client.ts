import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "../../lib/bridge/tauri-client";
import { broadcastVoicePermissionRequest } from "./voice-broadcast";
import { toVoiceUiEvent } from "./voice-event-filter";
import type { VoiceContextMessage } from "./voice-launch-context";
import type { VoiceUiEvent } from "./voice-types";

type VoiceBridgeConfig = {
  url: string;
  token: string;
  sessionId: string;
  cwd: string;
  model: string;
  backend: string;
  ttsMuted: boolean;
};

type VoiceResponse = {
  type: "response";
  id: string;
  ok: boolean;
  payload?: Record<string, unknown>;
  error?: {
    code: string;
    message: string;
  };
};

type PendingRequest = {
  resolve: (payload: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

export type DesktopVoiceClient = {
  connect: () => Promise<VoiceBridgeConfig>;
  startTurn: (text: string, options?: VoiceTurnStartOptions) => Promise<void>;
  transcribeAudio: (audioBase64: string) => Promise<string>;
  interruptTurn: () => Promise<boolean>;
  setTtsMuted: (muted: boolean) => Promise<boolean>;
  resolvePermission: (payload: {
    requestId: string;
    behavior: "allow" | "deny";
    rememberScope: "" | "session" | "workspace";
  }) => Promise<boolean>;
  close: () => void;
};

export type VoiceTurnStartOptions = {
  mode: "sharedSession" | "independentSession" | "parallelWorktree";
  contextVersion: number;
  contextMessages: VoiceContextMessage[];
  sourceSessionId: string;
  workspacePath: string;
};

export function createDesktopVoiceClient(emit: (event: VoiceUiEvent) => void): DesktopVoiceClient {
  if (!isTauriRuntime()) {
    return new MockVoiceClient(emit);
  }

  return new TauriVoiceClient(emit);
}

class TauriVoiceClient implements DesktopVoiceClient {
  private socket: WebSocket | null = null;
  private config: VoiceBridgeConfig | null = null;
  private opening: Promise<WebSocket> | null = null;
  private openingReject: ((error: Error) => void) | null = null;
  private connecting: Promise<VoiceBridgeConfig> | null = null;
  private lifecycleVersion = 0;
  private requestIndex = 0;
  private pending = new Map<string, PendingRequest>();

  constructor(private readonly emit: (event: VoiceUiEvent) => void) {}

  async connect(): Promise<VoiceBridgeConfig> {
    if (this.socket?.readyState === WebSocket.OPEN && this.config) {
      return this.config;
    }
    if (this.connecting) {
      return this.connecting;
    }

    const lifecycleVersion = this.lifecycleVersion;
    const connecting = this.connectInternal(lifecycleVersion);
    this.connecting = connecting;
    try {
      return await connecting;
    } finally {
      if (this.connecting === connecting) {
        this.connecting = null;
      }
    }
  }

  async startTurn(text: string, options?: VoiceTurnStartOptions): Promise<void> {
    await this.request("voice.turn.start", {
      text,
      mode: options?.mode ?? "independentSession",
      contextVersion: options?.contextVersion ?? 0,
      contextMessages: options?.contextMessages ?? [],
      sourceSessionId: options?.sourceSessionId ?? "",
      workspacePath: options?.workspacePath ?? ""
    });
  }

  async transcribeAudio(audioBase64: string): Promise<string> {
    const payload = await this.request("voice.transcribe", { audioBase64 });
    return typeof payload.text === "string" ? payload.text : "";
  }

  async interruptTurn(): Promise<boolean> {
    const payload = await this.request("voice.turn.interrupt", {});
    return payload.interrupted === true;
  }

  async setTtsMuted(muted: boolean): Promise<boolean> {
    const payload = await this.request("voice.tts.setMuted", { muted });
    return payload.ttsMuted === true;
  }

  async resolvePermission(payload: {
    requestId: string;
    behavior: "allow" | "deny";
    rememberScope: "" | "session" | "workspace";
  }): Promise<boolean> {
    const response = await this.request("permission.resolve", payload);
    return response.resolved === true;
  }

  close(): void {
    this.lifecycleVersion += 1;
    const error = new Error("Desktop voice bridge connection closed.");
    this.rejectPending(error);
    this.openingReject?.(error);
    this.openingReject = null;
    this.opening = null;
    const socket = this.socket;
    this.socket = null;
    this.config = null;
    socket?.close();
    this.connecting = null;
  }

  private async request(method: string, payload: Record<string, unknown>) {
    const socket = await this.openSocket();
    const id = `voice-${Date.now()}-${this.requestIndex}`;
    this.requestIndex += 1;

    return new Promise<Record<string, unknown>>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      socket.send(
        JSON.stringify({
          type: "request",
          id,
          method,
          token: this.config?.token ?? "",
          payload
        })
      );
    });
  }

  private async openSocket(): Promise<WebSocket> {
    const connectedSocket = this.socket;
    if (connectedSocket && connectedSocket.readyState === WebSocket.OPEN) {
      return connectedSocket;
    }
    if (this.opening) {
      return this.opening;
    }
    if (!this.config) {
      await this.connect();
    }

    const reconnectedSocket = this.socket;
    if (reconnectedSocket && reconnectedSocket.readyState === WebSocket.OPEN) {
      return reconnectedSocket;
    }
    if (this.opening) {
      return this.opening;
    }

    const config = this.config;
    if (!config) {
      throw new Error("Desktop voice bridge connection was closed.");
    }

    const socket = new WebSocket(config.url);
    this.socket = socket;
    let rejectOpening: (error: Error) => void = () => undefined;
    const opening = new Promise<WebSocket>((resolve, reject) => {
      rejectOpening = reject;

      const clearOpening = () => {
        if (this.opening === opening) {
          this.opening = null;
          this.openingReject = null;
        }
      };

      socket.addEventListener("open", () => {
        if (this.socket !== socket) {
          return;
        }
        clearOpening();
        resolve(socket);
      });
      socket.addEventListener("message", (event) => this.handleMessage(event.data));
      socket.addEventListener("error", () => {
        if (this.socket !== socket) {
          return;
        }
        const error = new Error("Desktop voice bridge connection failed.");
        const wasOpening = this.opening === opening;
        clearOpening();
        this.socket = null;
        this.config = null;
        if (wasOpening) {
          rejectOpening(error);
        }
        this.rejectPending(error);
      });
      socket.addEventListener("close", () => {
        if (this.socket !== socket) {
          return;
        }
        const error = new Error("Desktop voice bridge connection closed.");
        const wasOpening = this.opening === opening;
        clearOpening();
        this.socket = null;
        this.config = null;
        if (wasOpening) {
          rejectOpening(error);
        }
        this.rejectPending(error);
      });
    });
    this.opening = opening;
    this.openingReject = rejectOpening;

    return opening;
  }

  private async connectInternal(lifecycleVersion: number): Promise<VoiceBridgeConfig> {
    if (!this.config) {
      const rawConfig = await invoke<VoiceBridgeConfig>("ensure_desktop_voice_bridge");
      const config = validateVoiceBridgeConfig(rawConfig);
      if (lifecycleVersion !== this.lifecycleVersion) {
        throw new Error("Desktop voice bridge connection was cancelled.");
      }
      this.config = config;
    }
    await this.openSocket();
    if (!this.config) {
      throw new Error("Desktop voice bridge connection was closed.");
    }
    return this.config;
  }

  private handleMessage(rawData: unknown): void {
    if (typeof rawData !== "string") {
      return;
    }

    let message: unknown;
    try {
      message = JSON.parse(rawData);
    } catch {
      return;
    }

    if (isPermissionRequestEvent(message)) {
      broadcastVoicePermissionRequest({
        requestId: readPayloadString(message, "requestId"),
        toolName: readPayloadString(message, "toolName"),
        reason: readPayloadString(message, "reason")
      });
    }

    const voiceEvent = toVoiceUiEvent(message);
    if (voiceEvent) {
      this.emit(voiceEvent);
      return;
    }

    if (isVoiceResponse(message)) {
      this.resolveResponse(message);
    }
  }

  private resolveResponse(response: VoiceResponse): void {
    const pending = this.pending.get(response.id);
    if (!pending) {
      return;
    }
    this.pending.delete(response.id);

    if (response.ok) {
      pending.resolve(response.payload ?? {});
      return;
    }

    pending.reject(new Error(response.error?.message ?? "Desktop voice request failed."));
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
  }
}

class MockVoiceClient implements DesktopVoiceClient {
  constructor(private readonly emit: (event: VoiceUiEvent) => void) {}

  async connect(): Promise<VoiceBridgeConfig> {
    return {
      url: "mock://voice",
      token: "mock",
      sessionId: "mock-voice",
      cwd: "",
      model: "mock",
      backend: "mock",
      ttsMuted: true
    };
  }

  async startTurn(text: string): Promise<void> {
    this.emit({ type: "voice.user.final", text });
    this.emit({ type: "voice.state", state: "thinking", text: "正在处理" });
    await Promise.resolve();
    this.emit({
      type: "voice.assistant.final",
      text: ["收到。", "", "```ts", "const voice = \"ready\";", "```"].join("\n")
    });
    this.emit({ type: "voice.state", state: "idle", text: "准备好了" });
  }

  async transcribeAudio(_audioBase64: string): Promise<string> {
    const text = "查看语音窗口状态";
    this.emit({ type: "voice.user.partial", text });
    return text;
  }

  async interruptTurn(): Promise<boolean> {
    this.emit({ type: "voice.state", state: "paused", text: "已暂停" });
    return false;
  }

  async setTtsMuted(muted: boolean): Promise<boolean> {
    return muted;
  }

  async resolvePermission(): Promise<boolean> {
    return true;
  }

  close(): void {
    return undefined;
  }
}

function isVoiceResponse(value: unknown): value is VoiceResponse {
  if (!value || typeof value !== "object") {
    return false;
  }
  const response = value as Partial<VoiceResponse>;
  return response.type === "response" && typeof response.id === "string";
}

function isPermissionRequestEvent(value: unknown): value is {
  type: "event";
  event: "permission.request";
  payload: Record<string, unknown>;
} {
  if (!value || typeof value !== "object") {
    return false;
  }
  const event = value as {
    type?: unknown;
    event?: unknown;
    payload?: unknown;
  };
  return (
    event.type === "event" &&
    event.event === "permission.request" &&
    typeof event.payload === "object" &&
    event.payload !== null
  );
}

function readPayloadString(
  event: { payload: Record<string, unknown> },
  key: string
): string {
  const value = event.payload[key];
  return typeof value === "string" ? value : "";
}

function validateVoiceBridgeConfig(config: VoiceBridgeConfig): VoiceBridgeConfig {
  if (!config.url || !config.token) {
    throw new Error("Desktop voice bridge config is missing url or token.");
  }
  return config;
}
