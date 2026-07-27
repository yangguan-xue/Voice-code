import { afterEach, describe, expect, it, vi } from "vitest";
import {
  broadcastVoiceInterruptRequest,
  subscribeToVoiceInterruptRequests
} from "../voice-context-sync";

class FakeBroadcastChannel {
  static channels = new Map<string, Set<FakeBroadcastChannel>>();

  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;

  constructor(readonly name: string) {
    const channels = FakeBroadcastChannel.channels.get(name) ?? new Set();
    channels.add(this);
    FakeBroadcastChannel.channels.set(name, channels);
  }

  postMessage(payload: unknown) {
    for (const channel of FakeBroadcastChannel.channels.get(this.name) ?? []) {
      if (channel !== this) {
        channel.onmessage?.({ data: payload } as MessageEvent<unknown>);
      }
    }
  }

  close() {
    FakeBroadcastChannel.channels.get(this.name)?.delete(this);
  }
}

afterEach(() => {
  FakeBroadcastChannel.channels.clear();
  vi.unstubAllGlobals();
});

describe("voice control synchronization", () => {
  it("broadcasts an interrupt request for the active shared session", () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    const listener = vi.fn();
    const unsubscribe = subscribeToVoiceInterruptRequests(listener);

    broadcastVoiceInterruptRequest("session-1");

    expect(listener).toHaveBeenCalledWith({
      type: "interrupt",
      sourceSessionId: "session-1"
    });
    unsubscribe();
  });

  it("filters malformed voice control messages", () => {
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    const listener = vi.fn();
    const unsubscribe = subscribeToVoiceInterruptRequests(listener);
    const sender = new FakeBroadcastChannel("voice-control-requests");

    sender.postMessage({ type: "interrupt" });

    expect(listener).not.toHaveBeenCalled();
    sender.close();
    unsubscribe();
  });
});
