import { describe, expect, it, vi } from "vitest";
import { resolveWebDemoHttpUrl, resolveWebDemoSocketUrl, WebDemoClient } from "./web-demo-client";

describe("resolveWebDemoSocketUrl", () => {
  it("prefers an explicit websocket url", () => {
    expect(resolveWebDemoSocketUrl("wss://demo.example.com/ws-demo")).toBe(
      "wss://demo.example.com/ws-demo"
    );
  });

  it("derives a same-origin websocket url in the browser", () => {
    const originalWindow = globalThis.window;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        location: {
          protocol: "https:",
          host: "demo.example.com"
        }
      }
    });

    expect(resolveWebDemoSocketUrl()).toBe("wss://demo.example.com/ws-demo");

    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: originalWindow
    });
  });
});

describe("resolveWebDemoHttpUrl", () => {
  it("maps ws urls to http and strips the socket path", () => {
    expect(resolveWebDemoHttpUrl("ws://127.0.0.1:8787")).toBe("http://127.0.0.1:8787");
    expect(resolveWebDemoHttpUrl("wss://demo.example.com/ws-demo")).toBe(
      "https://demo.example.com"
    );
    expect(resolveWebDemoHttpUrl("wss://demo.example.com/ws")).toBe("https://demo.example.com");
  });
});

describe("WebDemoClient.uploadFile", () => {
  it("posts raw bytes with bearer token", async () => {
    const requests: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      requests.push({ url, init });
      return new Response(JSON.stringify({ path: "uploads/a.png", size: 4 }), {
        status: 201
      });
    });
    const client = new WebDemoClient({
      url: "ws://127.0.0.1:8787/ws-demo",
      emit: () => {}
    });
    const file = new File(["data"], "a.png", { type: "image/png" });

    const result = await client.uploadFile("token-1", "uploads/a.png", file);

    expect(result).toEqual({ path: "uploads/a.png", size: 4 });
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toBe(
      "http://127.0.0.1:8787/api/conversations/current/files?path=uploads%2Fa.png"
    );
    expect(requests[0].init?.method).toBe("POST");
    const headers = requests[0].init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer token-1");
    vi.unstubAllGlobals();
  });
});
