export type WebDemoEventName =
  | "agent.turn.started"
  | "agent.text.delta"
  | "agent.reasoning.delta"
  | "agent.tool.call"
  | "agent.tool.result"
  | "agent.error"
  | "agent.turn.finish"
  | "permission.request"
  | "session.updated"
  | "runtime.recycled"
  | "sandbox.diff.updated"
  | "sandbox.reset";

export type WebDemoEvent = {
  type: "event";
  event: WebDemoEventName;
  sessionId: string;
  turnId?: number;
  payload: Record<string, unknown>;
};

export type WebDemoRequestMethod =
  | "tenant.session.attach"
  | "turn.start"
  | "turn.interrupt"
  | "permission.resolve"
  | "sandbox.diff.get"
  | "sandbox.file.get"
  | "sandbox.reset"
  | "session.discard";

export type WebDemoRequest = {
  type: "request";
  id: string;
  method: WebDemoRequestMethod;
  sessionToken?: string;
  payload: Record<string, unknown>;
};

export type WebDemoResponse = {
  type: "response";
  id: string;
  ok: boolean;
  payload?: Record<string, unknown>;
  error?: {
    code: string;
    message: string;
  };
};

// 租户模型：sessionToken 即 tenantToken；sessionId 即 conversationId
export type TenantSessionPayload = {
  sessionId: string;
  conversationId: string;
  tenantId: string;
  tenantLabel: string;
  title: string;
  workspaceLabel: string;
  permissionMode: string;
  model: string;
};

export type TenantLoginResult = {
  tenantToken: string;
  tenantId: string;
  tenantLabel: string;
  expiresAt: string;
};

export type TenantProfile = {
  tenantId: string;
  tenantLabel: string;
};

export type ConversationSummary = {
  conversationId: string;
  title: string;
  updatedAt: string;
  isCurrent: boolean;
};

export type ConversationListPayload = {
  conversations: ConversationSummary[];
  currentConversationId: string;
};

// 历史回放：与 ConversationTurn 同构（后端已映射好）
export type HistoryToolEvent = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result: string;
  resultPreview: string;
  status: "running" | "completed" | "error";
};

export type HistoryTurn = {
  id: number;
  userText: string;
  assistantText: string;
  reasoningText: string;
  status: "streaming" | "completed" | "error";
  finishReason: string;
  tools: HistoryToolEvent[];
};

export type TurnStartResult = {
  sessionId: string;
  turnId: number;
  finishReason: string;
};

export type ChangedFile = {
  path: string;
  status: string;
  additions: number;
  deletions: number;
};

export type SandboxDiffPayload = {
  changedFiles: ChangedFile[];
  patchAvailable: boolean;
  patch?: string;
  diskUsageBytes: number;
};

export type PermissionResolveBehavior = "allow" | "deny";
export type PermissionRememberScope = "" | "session" | "workspace";

export type PermissionResolvePayload = {
  requestId: string;
  behavior: PermissionResolveBehavior;
  rememberScope: PermissionRememberScope;
  message?: string;
};

export type SandboxFilePayload = {
  path: string;
  content: string;
  encoding: string;
  mimeType: string;
};

export type WebDemoClientLike = {
  connect: () => Promise<void>;
  login: (accessCode: string) => Promise<TenantLoginResult>;
  fetchMe: (token: string) => Promise<TenantProfile>;
  logout: (token: string) => Promise<void>;
  attachSession: (token: string) => Promise<TenantSessionPayload>;
  listConversations: (token: string) => Promise<ConversationListPayload>;
  newConversation: (token: string) => Promise<TenantSessionPayload>;
  activateConversation: (token: string, conversationId: string) => Promise<TenantSessionPayload>;
  getConversationMessages: (token: string, conversationId: string) => Promise<HistoryTurn[]>;
  startTurn: (
    text: string,
    options: { sessionToken: string; permissionMode: string; images?: string[] }
  ) => Promise<TurnStartResult>;
  resolvePermission: (sessionToken: string, payload: PermissionResolvePayload) => Promise<boolean>;
  getSandboxFile: (sessionToken: string, path: string) => Promise<SandboxFilePayload>;
  uploadFile: (token: string, path: string, file: File) => Promise<{ path: string; size: number }>;
  resetSandbox: (sessionToken: string) => Promise<boolean>;
  close: () => void;
};

export function isWebDemoEvent(value: unknown): value is WebDemoEvent {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<WebDemoEvent>;
  return candidate.type === "event" && typeof candidate.event === "string";
}

export function isWebDemoResponse(value: unknown): value is WebDemoResponse {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<WebDemoResponse>;
  return candidate.type === "response" && typeof candidate.id === "string";
}

export function readString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

export function readNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  return typeof value === "number" ? value : 0;
}

export function readRecord(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = payload[key];
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

export function readChangedFiles(payload: Record<string, unknown>): ChangedFile[] {
  const value = payload.changedFiles;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const record = item as Record<string, unknown>;
    const path = readString(record, "path");
    if (!path) {
      return [];
    }
    return [
      {
        path,
        status: readString(record, "status") || "modified",
        additions: readNumber(record, "additions"),
        deletions: readNumber(record, "deletions")
      }
    ];
  });
}
