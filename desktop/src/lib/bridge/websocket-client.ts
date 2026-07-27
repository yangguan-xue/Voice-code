import {
  isBridgeEvent,
  isBridgeResponse,
  readBoolean,
  readNumber,
  readString,
  type BridgeEvent,
  type BridgeRequestMethod,
  type BridgeResponse,
  type BootstrapResult,
  type DesktopBridgeClient,
  type ModelProfileSummary,
  type ModelConfigInput,
  type ResolvedModelConfig,
  type PermissionResolvePayload,
  type SessionResumeResult,
  type TurnStartOptions,
  type TurnStartResult,
  type VoiceSettings,
  type VoiceSettingsInput
} from "./protocol";

type EmitBridgeEvent = (event: BridgeEvent) => void;
type PendingRequest = {
  resolve: (payload: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

export class BridgeClientError extends Error {
  constructor(
    message: string,
    readonly code = "BRIDGE_ERROR"
  ) {
    super(message);
    this.name = "BridgeClientError";
  }
}

export class WebSocketBridgeClient implements DesktopBridgeClient {
  private socket: WebSocket | null = null;
  private opening: Promise<WebSocket> | null = null;
  private requestIndex = 0;
  private pending = new Map<string, PendingRequest>();

  constructor(
    private readonly options: {
      url: string;
      token: string;
      emit: EmitBridgeEvent;
    }
  ) {}

  async connect(): Promise<void> {
    await this.openSocket();
  }

  async bootstrap(): Promise<BootstrapResult> {
    const payload = await this.request("app.bootstrap", {});
    return {
      sessionId: readString(payload, "sessionId"),
      sessionGroups: readSessionGroups(payload),
      workspacePath: readString(payload, "workspacePath"),
      branch: readString(payload, "branch"),
      branches: readStringArray(payload, "branches"),
      isDirty: readBoolean(payload, "isDirty"),
      activeProfile: readString(payload, "activeProfile"),
      modelProfiles: readModelProfiles(payload)
    };
  }

  async archiveSession(sessionId: string): Promise<boolean> {
    const payload = await this.request("session.archive", { sessionId });
    return readBoolean(payload, "archived");
  }

  async createSession(): Promise<{ sessionId: string; workspacePath: string }> {
    const payload = await this.request("session.create", {});
    return { sessionId: readString(payload, "sessionId"), workspacePath: readString(payload, "workspacePath") };
  }

  async resumeSession(sessionId: string): Promise<SessionResumeResult> {
    const payload = await this.request("session.resume", { sessionId });
    return {
      sessionId: readString(payload, "sessionId"),
      workspacePath: readString(payload, "workspacePath"),
      branch: readString(payload, "branch"),
      branches: readStringArray(payload, "branches"),
      isDirty: readBoolean(payload, "isDirty"),
      conversation: readConversation(payload)
    };
  }

  async checkoutBranch(branch: string): Promise<{ branch: string; branches: string[]; isDirty: boolean }> {
    const payload = await this.request("git.checkout", { branch });
    return {
      branch: readString(payload, "branch"),
      branches: readStringArray(payload, "branches"),
      isDirty: readBoolean(payload, "isDirty")
    };
  }

  async selectModelProfile(profile: string): Promise<{ profile: string; modelName: string }> {
    const payload = await this.request("model.select", { profile });
    return { profile: readString(payload, "profile"), modelName: readString(payload, "modelName") };
  }

  async configureModel(config: ResolvedModelConfig): Promise<{ profile: string; modelName: string }> {
    const payload = await this.request("model.configure", config);
    return { profile: readString(payload, "profile"), modelName: readString(payload, "modelName") };
  }

  async saveModelConfig(_input: ModelConfigInput): Promise<ModelProfileSummary> {
    throw new BridgeClientError("Model configuration storage requires the desktop app.", "UNAVAILABLE");
  }

  async deleteModelConfig(_id: string): Promise<boolean> {
    throw new BridgeClientError("Model configuration storage requires the desktop app.", "UNAVAILABLE");
  }

  async loadVoiceSettings(): Promise<VoiceSettings> {
    throw new BridgeClientError("Voice settings require the desktop app.", "UNAVAILABLE");
  }

  async saveVoiceSettings(_input: VoiceSettingsInput): Promise<VoiceSettings> {
    throw new BridgeClientError("Voice settings require the desktop app.", "UNAVAILABLE");
  }

  async startTurn(text: string, options?: TurnStartOptions): Promise<TurnStartResult> {
    const payload = await this.request("turn.start", {
      text,
      permissionMode: options?.permissionMode ?? ""
    });
    return {
      sessionId: readString(payload, "sessionId"),
      turnId: readNumber(payload, "turnId"),
      finishReason: readString(payload, "finishReason")
    };
  }

  async interruptTurn(turnId?: number): Promise<boolean> {
    const payload = await this.request("turn.interrupt", turnId ? { turnId } : {});
    return readBoolean(payload, "interrupted");
  }

  async resolvePermission(payload: PermissionResolvePayload): Promise<boolean> {
    const response = await this.request("permission.resolve", payload);
    return readBoolean(response, "resolved");
  }

  close(): void {
    this.rejectPending(new BridgeClientError("Desktop bridge connection closed.", "CLOSED"));
    this.socket?.close();
    this.socket = null;
    this.opening = null;
  }

  private async request(
    method: BridgeRequestMethod,
    payload: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    const socket = await this.openSocket();
    const id = `desktop-${Date.now()}-${this.requestIndex}`;
    this.requestIndex += 1;

    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      socket.send(
        JSON.stringify({
          type: "request",
          id,
          method,
          token: this.options.token,
          payload
        })
      );
    });
  }

  private async openSocket(): Promise<WebSocket> {
    if (this.socket?.readyState === WebSocket.OPEN) {
      return this.socket;
    }
    if (this.opening) {
      return this.opening;
    }

    this.opening = new Promise((resolve, reject) => {
      const socket = new WebSocket(this.options.url);
      this.socket = socket;

      socket.addEventListener("open", () => {
        this.opening = null;
        resolve(socket);
      });
      socket.addEventListener("message", (event) => this.handleMessage(event.data));
      socket.addEventListener("error", () => {
        const error = new BridgeClientError("Desktop bridge connection failed.", "CONNECTION_ERROR");
        this.opening = null;
        reject(error);
        this.rejectPending(error);
      });
      socket.addEventListener("close", () => {
        this.opening = null;
        this.socket = null;
        this.rejectPending(new BridgeClientError("Desktop bridge connection closed.", "CLOSED"));
      });
    });

    return this.opening;
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

    if (isBridgeEvent(message)) {
      this.options.emit(message);
      return;
    }

    if (isBridgeResponse(message)) {
      this.resolveResponse(message);
    }
  }

  private resolveResponse(response: BridgeResponse): void {
    const pending = this.pending.get(response.id);
    if (!pending) {
      return;
    }
    this.pending.delete(response.id);

    if (response.ok) {
      pending.resolve(response.payload ?? {});
      return;
    }

    pending.reject(
      new BridgeClientError(
        response.error?.message ?? "Desktop bridge request failed.",
        response.error?.code
      )
    );
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
  }
}

function readSessionGroups(payload: Record<string, unknown>): BootstrapResult["sessionGroups"] {
  const groups = payload.sessionGroups;
  if (!Array.isArray(groups)) {
    return [];
  }
  return groups
    .filter((group): group is Record<string, unknown> => Boolean(group && typeof group === "object"))
    .map((group) => {
      const rawSessions = group.sessions;
      const sessions = Array.isArray(rawSessions) ? rawSessions : [];
      return {
        id: readString(group, "id"),
        folderName: readString(group, "folderName"),
        workspacePath: readString(group, "workspacePath"),
        sessions: sessions
          .filter((session): session is Record<string, unknown> =>
            Boolean(session && typeof session === "object")
          )
          .map((session) => ({
            id: readString(session, "id"),
            title: readString(session, "title"),
            isActive: readBoolean(session, "isActive"),
            isEmpty: readBoolean(session, "isEmpty")
          }))
      };
    })
    .filter((group) => group.id && group.folderName);
}

function readStringArray(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function readModelProfiles(payload: Record<string, unknown>): ModelProfileSummary[] {
  const value = payload.modelProfiles;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return [];
    }
    const profile = item as Record<string, unknown>;
    const id = typeof profile.id === "string" ? profile.id : "";
    if (!id) {
      return [];
    }
    return [{
      id,
      label: typeof profile.label === "string" ? profile.label : id,
      modelName: typeof profile.modelName === "string" ? profile.modelName : id
    }];
  });
}

function readConversation(payload: Record<string, unknown>): SessionResumeResult["conversation"] {
  const candidate = payload.conversation;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    return { turns: [], activeTurnId: null };
  }
  const conversation = candidate as SessionResumeResult["conversation"];
  return {
    turns: Array.isArray(conversation.turns) ? conversation.turns : [],
    activeTurnId: typeof conversation.activeTurnId === "number" ? conversation.activeTurnId : null
  };
}
