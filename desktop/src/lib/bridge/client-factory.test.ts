import { describe, expect, it } from "vitest";
import { MockBridgeClient } from "./mock-client";
import { createDesktopBridgeClient } from "./client-factory";

describe("createDesktopBridgeClient", () => {
  it("falls back to the mock bridge outside Tauri when env bridge config is absent", () => {
    const client = createDesktopBridgeClient(() => undefined);

    expect(client).toBeInstanceOf(MockBridgeClient);
    client.close();
  });
});
