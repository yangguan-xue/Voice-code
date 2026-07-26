export type BridgeEventName =
  | "agent.turn.started"
  | "agent.text.delta"
  | "agent.reasoning.delta"
  | "agent.tool.call"
  | "agent.tool.result"
  | "agent.error"
  | "agent.turn.finish"
  | "permission.request"
  | "session.updated";

export type BridgeEvent = {
  type: "event";
  event: BridgeEventName;
  sessionId: string;
  turnId?: number;
  payload: Record<string, unknown>;
};

export type BridgeRequestMethod =
  | "app.bootstrap"
  | "session.archive"
  | "session.create"
  | "session.resume"
  | "git.checkout"
  | "model.select"
  | "model.configure"
  | "turn.start"
  | "turn.interrupt"
  | "permission.resolve";

export type BridgeRequest = {
  type: "request";
  id: string;
  method: BridgeRequestMethod;
  token: string;
  payload: Record<string, unknown>;
};

export type BridgeResponse = {
  type: "response";
  id: string;
  ok: boolean;
  payload?: Record<string, unknown>;
  error?: {
    code: string;
    message: string;
  };
};

export type TurnStartResult = {
  sessionId: string;
  turnId: number;
  finishReason: string;
};

export type BootstrapSessionSummary = {
  id: string;
  title: string;
  isActive?: boolean;
  isEmpty?: boolean;
};

export type BootstrapSessionGroup = {
  id: string;
  folderName: string;
  workspacePath?: string;
  sessions: BootstrapSessionSummary[];
};

export type BootstrapResult = {
  sessionId: string;
  sessionGroups: BootstrapSessionGroup[];
  workspacePath: string;
  branch: string;
  branches: string[];
  isDirty: boolean;
  activeProfile: string;
  modelProfiles: ModelProfileSummary[];
};

export type ModelProfileSummary = {
  id: string;
  label: string;
  modelName: string;
  source?: "builtin" | "custom";
  baseUrl?: string;
  hasApiKey?: boolean;
};

export type ModelConfigInput = {
  id?: string;
  displayName: string;
  baseUrl: string;
  apiKey: string;
  modelName: string;
};

export type VoiceSettings = {
  provider: "custom" | "stepfun";
  sttUrl: string;
  ttsUrl: string;
  stepfunVoice: string;
  stepfunKey: string;
  hasStepfunKey: boolean;
  ttsEnabled: boolean;
};

export type VoiceSettingsInput = {
  provider: "custom" | "stepfun";
  sttUrl: string;
  ttsUrl: string;
  stepfunVoice: string;
  stepfunKey: string;
  ttsEnabled: boolean;
};

export type ResolvedModelConfig = ModelConfigInput & { id: string };

export type RestoredConversation = {
  turns: Array<{
    id: number;
    sessionId: string;
    userText: string;
    assistantText: string;
    reasoningText: string;
    status: "streaming" | "waiting_permission" | "completed" | "error";
    finishReason: string;
    tools: Array<{
      id: string;
      name: string;
      args: Record<string, unknown>;
      result: string;
      resultPreview: string;
      status: "running" | "completed" | "error";
    }>;
    errorText: string;
  }>;
  activeTurnId: number | null;
};

export type SessionResumeResult = {
  sessionId: string;
  workspacePath: string;
  branch: string;
  branches: string[];
  isDirty: boolean;
  conversation: RestoredConversation;
};

export type TurnStartOptions = {
  permissionMode: string;
};

export type PermissionResolveBehavior = "allow" | "deny";
export type PermissionRememberScope = "" | "session" | "workspace";

export type PermissionResolvePayload = {
  requestId: string;
  behavior: PermissionResolveBehavior;
  rememberScope: PermissionRememberScope;
  message?: string;
};

export type DesktopBridgeClient = {
  connect: () => Promise<void>;
  bootstrap: () => Promise<BootstrapResult>;
  archiveSession: (sessionId: string) => Promise<boolean>;
  createSession: () => Promise<{ sessionId: string; workspacePath: string }>;
  resumeSession: (sessionId: string) => Promise<SessionResumeResult>;
  checkoutBranch: (branch: string) => Promise<{ branch: string; branches: string[]; isDirty: boolean }>;
  selectModelProfile: (profile: string) => Promise<{ profile: string; modelName: string }>;
  configureModel: (config: ResolvedModelConfig) => Promise<{ profile: string; modelName: string }>;
  saveModelConfig: (input: ModelConfigInput) => Promise<ModelProfileSummary>;
  deleteModelConfig: (id: string) => Promise<boolean>;
  loadVoiceSettings: () => Promise<VoiceSettings>;
  saveVoiceSettings: (input: VoiceSettingsInput) => Promise<VoiceSettings>;
  startTurn: (text: string, options?: TurnStartOptions) => Promise<TurnStartResult>;
  interruptTurn: (turnId?: number) => Promise<boolean>;
  resolvePermission: (payload: PermissionResolvePayload) => Promise<boolean>;
  close: () => void;
};

export function readString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

export function readRecord(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = payload[key];
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

export function readNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  return typeof value === "number" ? value : 0;
}

export function readBoolean(payload: Record<string, unknown>, key: string): boolean {
  const value = payload[key];
  return typeof value === "boolean" ? value : false;
}

export function isBridgeEvent(value: unknown): value is BridgeEvent {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<BridgeEvent>;
  return candidate.type === "event" && typeof candidate.event === "string";
}

export function isBridgeResponse(value: unknown): value is BridgeResponse {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<BridgeResponse>;
  return candidate.type === "response" && typeof candidate.id === "string";
}
