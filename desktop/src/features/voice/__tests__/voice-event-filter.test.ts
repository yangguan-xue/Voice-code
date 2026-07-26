import { describe, expect, it } from "vitest";
import type { BridgeEvent } from "../../../lib/bridge/protocol";
import { toVoiceUiEvent } from "../voice-event-filter";

describe("toVoiceUiEvent", () => {
  it("drops tool, routing, classifier, bridge and debug events", () => {
    const events = [
      { type: "tool.call", text: "bash command=ls" },
      { type: "route.decision", text: "COMMAND" },
      { type: "classifier.result", text: "DELEGATE" },
      { type: "bridge.raw", text: "payload" },
      { type: "debug.log", text: "stack" }
    ];

    expect(events.map(toVoiceUiEvent)).toEqual([null, null, null, null, null]);
  });

  it("maps desktop bridge user and assistant text without tool detail", () => {
    const events: BridgeEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "s1",
        turnId: 1,
        payload: { userText: "总结这个项目" }
      },
      {
        type: "event",
        event: "agent.text.delta",
        sessionId: "s1",
        turnId: 1,
        payload: { content: "这是一个桌面 agent。" }
      },
      {
        type: "event",
        event: "agent.tool.call",
        sessionId: "s1",
        turnId: 1,
        payload: { toolName: "bash", toolArgs: { command: "pwd" } }
      }
    ];

    expect(events.map(toVoiceUiEvent)).toEqual([
      { type: "voice.user.final", text: "总结这个项目" },
      { type: "voice.assistant.delta", text: "这是一个桌面 agent。" },
      null
    ]);
  });

  it("strips tagged runtime errors before showing them", () => {
    expect(
      toVoiceUiEvent({
        type: "voice.error",
        message: "<tool_use_error>Error: microphone unavailable</tool_use_error>"
      })
    ).toEqual({
      type: "voice.error",
      message: "Error: microphone unavailable",
      recoverable: true
    });
  });

  it("maps desktop voice bridge envelopes to voice UI events", () => {
    expect(
      toVoiceUiEvent({
        type: "event",
        event: "voice.assistant.final",
        sessionId: "voice-session",
        payload: { text: "完成了。" }
      })
    ).toEqual({
      type: "voice.assistant.final",
      text: "完成了。",
      sessionId: "voice-session"
    });
  });
});
