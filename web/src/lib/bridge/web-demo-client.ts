import {
  isWebDemoEvent,
  isWebDemoResponse,
  type DemoSessionPayload,
  type PermissionResolvePayload,
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

  async createSession(accessCode: string): Promise<DemoSessionPayload> {
    const payload = await this.request("demo.session.create", "", { accessCode });
    return {
      sessionId: String(payload.sessionId ?? ""),
      sessionToken: String(payload.sessionToken ?? ""),
      expiresAt: String(payload.expiresAt ?? ""),
      workspaceLabel: String(payload.workspaceLabel ?? ""),
      permissionMode: String(payload.permissionMode ?? "default"),
      model: String(payload.model ?? "")
    };
  }

  async startTurn(
    text: string,
    options: { sessionToken: string; permissionMode: string }
  ): Promise<TurnStartResult> {
    const payload = await this.request("turn.start", options.sessionToken, {
      text,
      permissionMode: options.permissionMode
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
  ): Promise<{ path: string; content: string }> {
    const response = await this.request("sandbox.file.get", sessionToken, { path });
    return {
      path: String(response.path ?? ""),
      content: String(response.content ?? "")
    };
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

export function createWebDemoClient(emit: (event: WebDemoEvent) => void): WebDemoClientLike {
  return new WebDemoClient({
    url: resolveWebDemoSocketUrl(import.meta.env.VITE_WEB_DEMO_WS_URL),
    emit
  });
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
