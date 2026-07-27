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
  | "session.expired"
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
  | "demo.session.create"
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

export type DemoSessionPayload = {
  sessionId: string;
  sessionToken: string;
  expiresAt: string;
  workspaceLabel: string;
  permissionMode: string;
  model: string;
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

export type WebDemoClientLike = {
  connect: () => Promise<void>;
  createSession: (accessCode: string) => Promise<DemoSessionPayload>;
  startTurn: (
    text: string,
    options: { sessionToken: string; permissionMode: string }
  ) => Promise<TurnStartResult>;
  resolvePermission: (
    sessionToken: string,
    payload: PermissionResolvePayload
  ) => Promise<boolean>;
  getSandboxFile: (sessionToken: string, path: string) => Promise<{ path: string; content: string }>;
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
