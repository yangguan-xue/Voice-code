import type { HistoryTurn } from "../../lib/bridge/protocol";
import type { ConversationState, ConversationTurn } from "./types";

/**
 * 后端历史回放（HistoryTurn[]）→ reducer 的 ConversationState。
 * 后端已完成 transcript → turn 的映射与路径脱敏；历史回合使用负数 ID，
 * 与实时事件从 1 开始的回合编号隔离，避免恢复后新回合覆盖历史。
 */
export function conversationFromHistory(
  turns: HistoryTurn[],
  sessionId: string
): ConversationState {
  const mapped: ConversationTurn[] = turns.map((turn) => ({
    id: -turn.id,
    sessionId,
    userText: turn.userText,
    assistantText: turn.assistantText,
    reasoningText: turn.reasoningText,
    status: turn.status,
    finishReason: turn.finishReason,
    tools: turn.tools.map((tool) => ({
      id: tool.id,
      name: tool.name,
      args: tool.args,
      result: tool.result,
      resultPreview: tool.resultPreview,
      status: tool.status
    })),
    errorText: ""
  }));
  return { turns: mapped, activeTurnId: null };
}
