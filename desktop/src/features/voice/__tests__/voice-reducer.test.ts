import { describe, expect, it } from "vitest";
import { createInitialVoiceState, voiceReducer } from "../voice-reducer";
import type { VoiceUiEvent } from "../voice-types";

describe("voiceReducer", () => {
  it("creates a right-side user message from final speech", () => {
    const state = voiceReducer(createInitialVoiceState(), {
      type: "voice.user.final",
      text: "你好"
    });

    expect(state.status).toBe("thinking");
    expect(state.messages).toMatchObject([{ role: "user", text: "你好" }]);
  });

  it("streams assistant deltas into one assistant block", () => {
    const events: VoiceUiEvent[] = [
      { type: "voice.assistant.delta", text: "你好！" },
      { type: "voice.assistant.delta", text: "我在。" }
    ];

    const state = events.reduce(voiceReducer, createInitialVoiceState());

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      role: "assistant",
      text: "你好！我在。",
      isStreaming: true
    });
  });

  it("keeps separate assistant turns as separate blocks", () => {
    const events: VoiceUiEvent[] = [
      { type: "voice.assistant.final", text: "第一轮回答。" },
      { type: "voice.user.final", text: "继续" },
      { type: "voice.assistant.final", text: "第二轮回答。" }
    ];

    const state = events.reduce(voiceReducer, createInitialVoiceState());

    expect(state.messages.map((message) => [message.role, message.text])).toEqual([
      ["assistant", "第一轮回答。"],
      ["user", "继续"],
      ["assistant", "第二轮回答。"]
    ]);
  });

  it("uses assistant final to finish the current streaming block", () => {
    const events: VoiceUiEvent[] = [
      { type: "voice.assistant.delta", text: "临时" },
      { type: "voice.assistant.final", text: "最终回答。" }
    ];

    const state = events.reduce(voiceReducer, createInitialVoiceState());

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      role: "assistant",
      text: "最终回答。",
      isStreaming: false
    });
  });


  it("stops capture and preserves transcript when paused", () => {
    const listening = voiceReducer(createInitialVoiceState(), {
      type: "voice.user.partial",
      text: "正在说话"
    });
    const paused = voiceReducer(listening, {
      type: "voice.state",
      state: "paused",
      text: "已暂停"
    });

    expect(paused.isCapturing).toBe(false);
    expect(paused.partialUserText).toBe("");
    expect(paused.statusText).toBe("已暂停");
  });

  it("finishes a streaming assistant block when the voice state returns idle", () => {
    const streaming = voiceReducer(createInitialVoiceState(), {
      type: "voice.assistant.delta",
      text: "处理完成。"
    });
    const idle = voiceReducer(streaming, {
      type: "voice.state",
      state: "idle",
      text: "准备好了"
    });

    expect(idle.messages[0]).toMatchObject({
      role: "assistant",
      text: "处理完成。",
      isStreaming: false
    });
  });
});
