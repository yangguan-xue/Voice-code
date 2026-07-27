import { describe, expect, it } from "vitest";
import {
  conversationReducer,
  createInitialConversationState
} from "./conversation-reducer";
import type { WebDemoEvent } from "../../lib/bridge/protocol";

describe("conversationReducer", () => {
  it("renders streamed assistant and tool events into one turn", () => {
    const events: WebDemoEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "demo_1",
        turnId: 1,
        payload: { userText: "改 app.py" }
      },
      {
        type: "event",
        event: "agent.tool.call",
        sessionId: "demo_1",
        turnId: 1,
        payload: { toolCallId: "tool-1", toolName: "read", toolArgs: { file_path: "app.py" } }
      },
      {
        type: "event",
        event: "agent.tool.result",
        sessionId: "demo_1",
        turnId: 1,
        payload: { toolCallId: "tool-1", toolName: "read", toolResult: "VALUE = 1" }
      },
      {
        type: "event",
        event: "agent.text.delta",
        sessionId: "demo_1",
        turnId: 1,
        payload: { content: "完成。" }
      }
    ];

    const state = events.reduce(conversationReducer, createInitialConversationState());

    expect(state.turns[0].userText).toBe("改 app.py");
    expect(state.turns[0].assistantText).toBe("完成。");
    expect(state.turns[0].tools[0]).toMatchObject({
      name: "read",
      status: "completed",
      resultPreview: "VALUE = 1"
    });
  });

  it("filters internal compaction reasoning markers from visible conversation text", () => {
    const events: WebDemoEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "demo_1",
        turnId: 1,
        payload: { userText: "继续" }
      },
      {
        type: "event",
        event: "agent.reasoning.delta",
        sessionId: "demo_1",
        turnId: 1,
        payload: { content: "compact: micro ~586 tok\n" }
      },
      {
        type: "event",
        event: "agent.reasoning.delta",
        sessionId: "demo_1",
        turnId: 1,
        payload: { content: "继续分析上下文\n" }
      }
    ];

    const state = events.reduce(conversationReducer, createInitialConversationState());

    expect(state.turns[0].reasoningText).toBe("继续分析上下文\n");
  });
});
