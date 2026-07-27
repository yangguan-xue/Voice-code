import { describe, expect, it } from "vitest";
import { resolveWebDemoSocketUrl } from "./web-demo-client";

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
