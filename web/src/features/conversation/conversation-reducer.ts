import {
  readRecord,
  readString,
  type WebDemoEvent
} from "../../lib/bridge/protocol";
import type { ConversationState, ConversationTurn, ToolEvent } from "./types";

export function createInitialConversationState(): ConversationState {
  return {
    turns: [],
    activeTurnId: null
  };
}

export function conversationReducer(
  state: ConversationState,
  event: WebDemoEvent
): ConversationState {
  if (typeof event.turnId !== "number") {
    return state;
  }

  switch (event.event) {
    case "agent.turn.started":
      return upsertTurn(state, createTurn(event));
    case "agent.text.delta":
      return updateTurn(state, event.turnId, (turn) => ({
        ...turn,
        assistantText: turn.assistantText + readString(event.payload, "content"),
        status: "streaming"
      }));
    case "agent.reasoning.delta":
      return updateTurn(state, event.turnId, (turn) => {
        const nextReasoning = sanitizeReasoningChunk(readString(event.payload, "content"));
        if (!nextReasoning) {
          return turn;
        }
        return {
          ...turn,
          reasoningText: turn.reasoningText + nextReasoning,
          status: "streaming"
        };
      });
    case "agent.tool.call":
      return updateTurn(state, event.turnId, (turn) => ({
        ...turn,
        tools: upsertTool(turn.tools, {
          id: readString(event.payload, "toolCallId"),
          name: readString(event.payload, "toolName"),
          args: readRecord(event.payload, "toolArgs"),
          result: "",
          resultPreview: "",
          status: "running"
        }),
        status: "streaming"
      }));
    case "agent.tool.result":
      return updateTurn(state, event.turnId, (turn) => {
        const toolCallId = readString(event.payload, "toolCallId");
        const result = readString(event.payload, "toolResult");
        const status = readString(event.payload, "status");
        return {
          ...turn,
          tools: turn.tools.map((tool) =>
            tool.id === toolCallId
              ? {
                  ...tool,
                  result,
                  resultPreview: previewText(result),
                  status: status === "error" ? "error" : "completed"
                }
              : tool
          ),
          status: "streaming"
        };
      });
    case "agent.error":
      return updateTurn(state, event.turnId, (turn) => ({
        ...turn,
        errorText: readString(event.payload, "content"),
        status: "error"
      }));
    case "agent.turn.finish":
      return updateTurn(state, event.turnId, (turn) => ({
        ...turn,
        finishReason: readString(event.payload, "finishReason"),
        tools: closeRunningTools(turn.tools),
        status: turn.status === "error" ? "error" : "completed"
      }));
    default:
      return state;
  }
}

function createTurn(event: WebDemoEvent): ConversationTurn {
  return {
    id: event.turnId ?? 0,
    sessionId: event.sessionId,
    userText: readString(event.payload, "userText"),
    assistantText: "",
    reasoningText: "",
    status: "streaming",
    finishReason: "",
    tools: [],
    errorText: ""
  };
}

function upsertTurn(state: ConversationState, nextTurn: ConversationTurn): ConversationState {
  const existingIndex = state.turns.findIndex((turn) => turn.id === nextTurn.id);
  if (existingIndex === -1) {
    return {
      ...state,
      activeTurnId: nextTurn.id,
      turns: [...state.turns, nextTurn]
    };
  }
  const turns = [...state.turns];
  turns[existingIndex] = nextTurn;
  return {
    ...state,
    activeTurnId: nextTurn.id,
    turns
  };
}

function updateTurn(
  state: ConversationState,
  turnId: number,
  updater: (turn: ConversationTurn) => ConversationTurn
): ConversationState {
  let didUpdate = false;
  const turns = state.turns.map((turn) => {
    if (turn.id !== turnId) {
      return turn;
    }
    didUpdate = true;
    return updater(turn);
  });
  if (!didUpdate) {
    return state;
  }
  return {
    ...state,
    activeTurnId: turnId,
    turns
  };
}

function upsertTool(tools: ToolEvent[], nextTool: ToolEvent): ToolEvent[] {
  if (!nextTool.id) {
    return tools;
  }
  const existingIndex = tools.findIndex((tool) => tool.id === nextTool.id);
  if (existingIndex === -1) {
    return [...tools, nextTool];
  }
  const nextTools = [...tools];
  nextTools[existingIndex] = {
    ...nextTools[existingIndex],
    ...nextTool
  };
  return nextTools;
}

function closeRunningTools(tools: ToolEvent[]): ToolEvent[] {
  return tools.map((tool) =>
    tool.status === "running"
      ? {
          ...tool,
          status: "completed"
        }
      : tool
  );
}

function previewText(text: string): string {
  const normalized = text.replace(/<\/?tool_use_error>/g, "").replace(/\s+/g, " ").trim();
  if (normalized.length <= 160) {
    return normalized;
  }
  return `${normalized.slice(0, 159)}...`;
}

function sanitizeReasoningChunk(text: string): string {
  if (!text) {
    return "";
  }
  const filtered = text
    .split("\n")
    .filter((line) => !/^\s*compact:\s*\w+/i.test(line.trim()))
    .join("\n")
    .trim();
  if (!filtered) {
    return "";
  }
  return text.endsWith("\n") ? `${filtered}\n` : filtered;
}
