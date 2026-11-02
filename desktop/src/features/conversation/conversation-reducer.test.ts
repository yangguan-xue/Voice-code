import { describe, expect, it } from "vitest";
import { conversationReducer, createInitialConversationState } from "./conversation-reducer";
import type { BridgeEvent } from "../../lib/bridge/protocol";

describe("conversationReducer", () => {
  it("builds a live turn from streamed agent events", () => {
    const events: BridgeEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "s1",
        turnId: 1,
        payload: { userText: "查看项目结构" }
      },
      {
        type: "event",
        event: "agent.reasoning.delta",
        sessionId: "s1",
        turnId: 1,
        payload: { content: "先读目录。" }
      },
      {
        type: "event",
        event: "agent.text.delta",
        sessionId: "s1",
        turnId: 1,
        payload: { content: "我会先看项目结构。" }
      },
      {
        type: "event",
        event: "agent.tool.call",
        sessionId: "s1",
        turnId: 1,
        payload: {
          toolCallId: "tool-1",
          toolName: "grep",
          toolArgs: { pattern: "TUI" }
        }
      },
      {
        type: "event",
        event: "agent.tool.result",
        sessionId: "s1",
        turnId: 1,
        payload: {
          toolCallId: "tool-1",
          toolResult: "docs/specs/tui-goal-session-experience.md"
        }
      },
      {
        type: "event",
        event: "agent.turn.finish",
        sessionId: "s1",
        turnId: 1,
        payload: { finishReason: "completed" }
      }
    ];

    const state = events.reduce(conversationReducer, createInitialConversationState());

    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]?.userText).toBe("查看项目结构");
    expect(state.turns[0]?.assistantText).toBe("我会先看项目结构。");
    expect(state.turns[0]?.reasoningText).toBe("先读目录。");
    expect(state.turns[0]?.status).toBe("completed");
    expect(state.turns[0]?.tools[0]).toMatchObject({
      id: "tool-1",
      name: "grep",
      status: "completed",
      resultPreview: "docs/specs/tui-goal-session-experience.md"
    });
  });

  it("marks a tool row as failed when an agent error belongs to that tool call", () => {
    const events: BridgeEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "s1",
        turnId: 1,
        payload: { userText: "看目录" }
      },
      {
        type: "event",
        event: "agent.tool.call",
        sessionId: "s1",
        turnId: 1,
        payload: {
          toolCallId: "tool-1",
          toolName: "bash",
          toolArgs: { command: "find . -type f" }
        }
      },
      {
        type: "event",
        event: "agent.error",
        sessionId: "s1",
        turnId: 1,
        payload: {
          toolCallId: "tool-1",
          content: "<tool_use_error>Error: use glob instead</tool_use_error>"
        }
      }
    ];

    const state = events.reduce(conversationReducer, createInitialConversationState());

    expect(state.turns[0]?.status).toBe("streaming");
    expect(state.turns[0]?.errorText).toBe("");
    expect(state.turns[0]?.tools[0]).toMatchObject({
      id: "tool-1",
      status: "error",
      resultPreview: "Error: use glob instead"
    });
  });

  it("closes any stale running tool rows when a turn finishes", () => {
    const events: BridgeEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "s1",
        turnId: 1,
        payload: { userText: "看目录" }
      },
      {
        type: "event",
        event: "agent.tool.call",
        sessionId: "s1",
        turnId: 1,
        payload: {
          toolCallId: "tool-1",
          toolName: "bash",
          toolArgs: { command: "pwd" }
        }
      },
      {
        type: "event",
        event: "agent.turn.finish",
        sessionId: "s1",
        turnId: 1,
        payload: { finishReason: "completed" }
      }
    ];

    const state = events.reduce(conversationReducer, createInitialConversationState());

    expect(state.turns[0]?.status).toBe("completed");
    expect(state.turns[0]?.tools[0]?.status).toBe("completed");
  });

  it("records compact notices as status lines instead of errors", () => {
    const events: BridgeEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "s1",
        turnId: 1,
        payload: { userText: "写个文件" }
      },
      {
        type: "event",
        event: "agent.error",
        sessionId: "s1",
        turnId: 1,
        payload: { content: "compact: micro ~1250 tok", status: "compact" }
      },
      {
        type: "event",
        event: "agent.text.delta",
        sessionId: "s1",
        turnId: 1,
        payload: { content: "已创建 hello.txt" }
      },
      {
        type: "event",
        event: "agent.turn.finish",
        sessionId: "s1",
        turnId: 1,
        payload: { finishReason: "completed" }
      }
    ];

    const state = events.reduce(conversationReducer, createInitialConversationState());

    expect(state.turns[0]?.status).toBe("completed");
    expect(state.turns[0]?.errorText).toBe("");
    expect(state.turns[0]?.statusNotices).toEqual(["compact: micro ~1250 tok"]);
  });

  it("keeps legacy compact text without a status tag out of the error state", () => {
    const events: BridgeEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: "s1",
        turnId: 1,
        payload: { userText: "继续" }
      },
      {
        type: "event",
        event: "agent.error",
        sessionId: "s1",
        turnId: 1,
        payload: { content: "Conversation compacted (reactive) — retrying" }
      }
    ];

    const state = events.reduce(conversationReducer, createInitialConversationState());

    expect(state.turns[0]?.status).toBe("streaming");
    expect(state.turns[0]?.errorText).toBe("");
    expect(state.turns[0]?.statusNotices).toEqual([
      "Conversation compacted (reactive) — retrying"
    ]);
  });
});
