import {
  isWebDemoEvent,
  isWebDemoResponse,
  type ConversationListPayload,
  type HistoryTurn,
  type PermissionResolvePayload,
  type SandboxFilePayload,
  type TenantLoginResult,
  type TenantProfile,
  type TenantSessionPayload,
  type TurnStartResult,
  type WebDemoClientLike,
  type WebDemoEvent,
  type WebDemoRequest,
  type WebDemoRequestMethod,
  type WebDemoResponse
} from "./protocol";

type WebDemoClientOptions = {
  url: string;
  emit: (event: WebDemoEvent) => void;
};

type PendingRequest = {
  resolve: (payload: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

export class WebDemoClient implements WebDemoClientLike {
  private socket: WebSocket | null = null;
  private opening: Promise<WebSocket> | null = null;
  private pending = new Map<string, PendingRequest>();

  constructor(private readonly options: WebDemoClientOptions) {}

  async connect(): Promise<void> {
    await this.openSocket();
  }

  // --- REST：鉴权 ---

  async login(accessCode: string): Promise<TenantLoginResult> {
    const response = await this.http("/api/auth/login", {
      method: "POST",
      body: { accessCode }
    });
    return {
      tenantToken: String(response.tenantToken ?? ""),
      tenantId: String(response.tenantId ?? ""),
      tenantLabel: String(response.tenantLabel ?? ""),
      expiresAt: String(response.expiresAt ?? "")
    };
  }

  async fetchMe(token: string): Promise<TenantProfile> {
    const response = await this.http("/api/auth/me", { token });
    return {
      tenantId: String(response.tenantId ?? ""),
      tenantLabel: String(response.tenantLabel ?? "")
    };
  }

  async logout(token: string): Promise<void> {
    await this.http("/api/auth/logout", { method: "POST", token });
  }

  // --- REST：会话 ---

  async listConversations(token: string): Promise<ConversationListPayload> {
    const response = await this.http("/api/conversations", { token });
    const rawList = Array.isArray(response.conversations) ? response.conversations : [];
    return {
      conversations: rawList.map((item) => {
        const record = item as Record<string, unknown>;
        return {
          conversationId: String(record.conversationId ?? ""),
          title: String(record.title ?? ""),
          updatedAt: String(record.updatedAt ?? ""),
          isCurrent: Boolean(record.isCurrent)
        };
      }),
      currentConversationId: String(response.currentConversationId ?? "")
    };
  }

  async newConversation(token: string): Promise<TenantSessionPayload> {
    const response = await this.http("/api/conversations/new", { method: "POST", token });
    return readSessionPayload(response);
  }

  async activateConversation(
    token: string,
    conversationId: string
  ): Promise<TenantSessionPayload> {
    const response = await this.http(
      `/api/conversations/${encodeURIComponent(conversationId)}/activate`,
      { method: "POST", token }
    );
    return readSessionPayload(response);
  }

  async getConversationMessages(
    token: string,
    conversationId: string
  ): Promise<HistoryTurn[]> {
    const response = await this.http(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      { token }
    );
    return (Array.isArray(response.turns) ? response.turns : []) as HistoryTurn[];
  }

  // --- WS：会话与回合 ---

  async attachSession(token: string): Promise<TenantSessionPayload> {
    const payload = await this.request("tenant.session.attach", token, {});
    return readSessionPayload(payload);
  }

  async startTurn(
    text: string,
    options: { sessionToken: string; permissionMode: string; images?: string[] }
  ): Promise<TurnStartResult> {
    const payload = await this.request("turn.start", options.sessionToken, {
      text,
      permissionMode: options.permissionMode,
      ...(options.images?.length
        ? { images: options.images.map((path) => ({ path })) }
        : {})
    });
    return {
      sessionId: String(payload.sessionId ?? ""),
      turnId: Number(payload.turnId ?? 0),
      finishReason: String(payload.finishReason ?? "")
    };
  }

  async resolvePermission(
    sessionToken: string,
    payload: PermissionResolvePayload
  ): Promise<boolean> {
    const response = await this.request("permission.resolve", sessionToken, payload);
    return Boolean(response.resolved);
  }

  async getSandboxFile(
    sessionToken: string,
    path: string
  ): Promise<SandboxFilePayload> {
    const response = await this.request("sandbox.file.get", sessionToken, { path });
    return {
      path: String(response.path ?? ""),
      content: String(response.content ?? ""),
      encoding: String(response.encoding ?? "utf-8"),
      mimeType: String(response.mimeType ?? "text/plain;charset=utf-8")
    };
  }

  async uploadFile(
    token: string,
    path: string,
    file: File
  ): Promise<{ path: string; size: number }> {
    const base = resolveWebDemoHttpUrl(this.options.url);
    const response = await fetch(
      `${base}/api/conversations/current/files?path=${encodeURIComponent(path)}`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": file.type || "application/octet-stream"
        },
        body: file
      }
    );
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`上传失败（HTTP ${response.status}）${detail ? `: ${detail.slice(0, 120)}` : ""}`);
    }
    const payload = (await response.json()) as Record<string, unknown>;
    return { path: String(payload.path ?? ""), size: Number(payload.size ?? 0) };
  }

  async resetSandbox(sessionToken: string): Promise<boolean> {
    const response = await this.request("sandbox.reset", sessionToken, {});
    return Boolean(response.reset);
  }

  close(): void {
    this.socket?.close();
    this.socket = null;
    this.opening = null;
    this.rejectPending(new Error("Web demo connection closed."));
  }

  // --- 内部 ---

  private async http(
    path: string,
    options: { method?: string; token?: string; body?: Record<string, unknown> }
  ): Promise<Record<string, unknown>> {
    const base = resolveWebDemoHttpUrl(this.options.url);
    const headers: Record<string, string> = {};
    if (options.token) {
      headers.Authorization = `Bearer ${options.token}`;
    }
    if (options.body) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(`${base}${path}`, {
      method: options.method ?? "GET",
      headers,
      ...(options.body ? { body: JSON.stringify(options.body) } : {})
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`请求失败（HTTP ${response.status}）${detail ? `: ${detail.slice(0, 120)}` : ""}`);
    }
    if (response.status === 204) {
      return {};
    }
    return (await response.json()) as Record<string, unknown>;
  }

  private async request(
    method: WebDemoRequestMethod,
    sessionToken: string,
    payload: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    const socket = await this.openSocket();
    const id = `${method}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const request: WebDemoRequest = {
      type: "request",
      id,
      method,
      sessionToken,
      payload
    };
    const promise = new Promise<Record<string, unknown>>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    socket.send(JSON.stringify(request));
    return promise;
  }

  private async openSocket(): Promise<WebSocket> {
    if (this.socket?.readyState === WebSocket.OPEN) {
      return this.socket;
    }
    if (this.opening) {
      return this.opening;
    }

    this.opening = new Promise<WebSocket>((resolve, reject) => {
      const socket = new WebSocket(this.options.url);
      socket.addEventListener("open", () => {
        this.socket = socket;
        this.opening = null;
        resolve(socket);
      });
      socket.addEventListener("message", (event) => this.handleMessage(event.data));
      socket.addEventListener("error", () => {
        const error = new Error("Web demo connection failed.");
        this.opening = null;
        this.rejectPending(error);
        reject(error);
      });
      socket.addEventListener("close", () => {
        this.socket = null;
        this.opening = null;
        this.rejectPending(new Error("Web demo connection closed."));
      });
    });

    return this.opening;
  }

  private handleMessage(raw: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return;
    }
    if (isWebDemoEvent(parsed)) {
      this.options.emit(parsed);
      return;
    }
    if (isWebDemoResponse(parsed)) {
      this.resolveResponse(parsed);
    }
  }

  private resolveResponse(response: WebDemoResponse): void {
    const pending = this.pending.get(response.id);
    if (!pending) {
      return;
    }
    this.pending.delete(response.id);
    if (response.ok) {
      pending.resolve(response.payload ?? {});
      return;
    }
    pending.reject(new Error(response.error?.message || "Web demo request failed."));
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
  }
}

function readSessionPayload(payload: Record<string, unknown>): TenantSessionPayload {
  return {
    sessionId: String(payload.sessionId ?? ""),
    conversationId: String(payload.conversationId ?? payload.sessionId ?? ""),
    tenantId: String(payload.tenantId ?? ""),
    tenantLabel: String(payload.tenantLabel ?? ""),
    title: String(payload.title ?? ""),
    workspaceLabel: String(payload.workspaceLabel ?? ""),
    permissionMode: String(payload.permissionMode ?? "default"),
    model: String(payload.model ?? "")
  };
}

export function createWebDemoClient(emit: (event: WebDemoEvent) => void): WebDemoClientLike {
  return new WebDemoClient({
    url: resolveWebDemoSocketUrl(import.meta.env.VITE_WEB_DEMO_WS_URL),
    emit
  });
}

export function resolveWebDemoHttpUrl(wsUrl: string): string {
  const parsed = new URL(wsUrl);
  parsed.protocol = parsed.protocol === "wss:" ? "https:" : "http:";
  parsed.pathname = parsed.pathname
    .replace(/\/(ws-demo|ws)\/?$/, "")
    .replace(/\/$/, "");
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString().replace(/\/$/, "");
}

export function resolveWebDemoSocketUrl(explicitUrl?: string): string {
  const configured = explicitUrl?.trim();
  if (configured) {
    return configured;
  }
  if (typeof window === "undefined") {
    return "ws://127.0.0.1:8787";
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws-demo`;
}
