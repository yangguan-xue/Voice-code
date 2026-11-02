import { afterEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { createDesktopVoiceClient } from "../voice-client";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn()
}));

type Listener = (event: unknown) => void;

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static autoOpen = true;
  readonly url: string;
  readyState = 0;
  private readonly listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    fakeSockets.push(this);
    if (FakeWebSocket.autoOpen) {
      queueMicrotask(() => this.open());
    }
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", {});
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open", {});
  }

  send(data: string): void {
    const request = JSON.parse(data) as { id: string };
    queueMicrotask(() => {
      this.emit("message", {
        data: JSON.stringify({
          type: "response",
          id: request.id,
          ok: true,
          payload: { text: "识别结果" }
        })
      });
    });
  }

  private emit(type: string, event: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

const fakeSockets: FakeWebSocket[] = [];

const bridgeConfig = {
  url: "ws://127.0.0.1:1234",
  token: "token",
  sessionId: "session",
  cwd: "/workspace",
  model: "model",
  backend: "remote",
  ttsMuted: true
};

afterEach(() => {
  FakeWebSocket.autoOpen = true;
  fakeSockets.length = 0;
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
});

describe("Tauri voice client lifecycle", () => {
  it("deduplicates concurrent bridge startup and socket opening", async () => {
    installTauriGlobals();
    vi.mocked(invoke).mockResolvedValue(bridgeConfig);
    const client = createDesktopVoiceClient(() => undefined);

    await Promise.all([client.connect(), client.connect(), client.connect()]);

    expect(invoke).toHaveBeenCalledTimes(1);
    expect(fakeSockets).toHaveLength(1);
  });

  it("does not open a second socket when the first request bootstraps the client", async () => {
    installTauriGlobals();
    vi.mocked(invoke).mockResolvedValue(bridgeConfig);
    const client = createDesktopVoiceClient(() => undefined);

    await expect(client.transcribeAudio("audio")).resolves.toBe("识别结果");

    expect(invoke).toHaveBeenCalledTimes(1);
    expect(fakeSockets).toHaveLength(1);
  });

  it("refreshes bridge config after the bridge socket closes", async () => {
    installTauriGlobals();
    vi.mocked(invoke)
      .mockResolvedValueOnce(bridgeConfig)
      .mockResolvedValueOnce({ ...bridgeConfig, url: "ws://127.0.0.1:5678" });
    const client = createDesktopVoiceClient(() => undefined);

    await client.connect();
    fakeSockets[0].close();
    await client.connect();

    expect(invoke).toHaveBeenCalledTimes(2);
    expect(fakeSockets.map((socket) => socket.url)).toEqual([
      "ws://127.0.0.1:1234",
      "ws://127.0.0.1:5678"
    ]);
  });

  it("rejects an in-flight connection when the client closes", async () => {
    installTauriGlobals();
    FakeWebSocket.autoOpen = false;
    vi.mocked(invoke).mockResolvedValue(bridgeConfig);
    const client = createDesktopVoiceClient(() => undefined);

    const connectPromise = client.connect();
    await vi.waitFor(() => expect(fakeSockets).toHaveLength(1));
    client.close();

    const outcome = await Promise.race([
      connectPromise.then(
        () => "resolved",
        (error: Error) => `rejected:${error.message}`
      ),
      new Promise<string>((resolve) => {
        setTimeout(() => resolve("timeout"), 100);
      })
    ]);
    expect(outcome).toBe("rejected:Desktop voice bridge connection closed.");
  });
});

function installTauriGlobals(): void {
  Object.defineProperty(window, "__TAURI_INTERNALS__", {
    configurable: true,
    value: {}
  });
  vi.stubGlobal("WebSocket", FakeWebSocket);
}
