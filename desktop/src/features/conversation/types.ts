export type TurnStatus = "streaming" | "waiting_permission" | "completed" | "error";

export type ToolEventStatus = "running" | "completed" | "error";

export type ToolEvent = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result: string;
  resultPreview: string;
  status: ToolEventStatus;
};

export type ConversationTurn = {
  id: number;
  sessionId: string;
  userText: string;
  assistantText: string;
  reasoningText: string;
  status: TurnStatus;
  finishReason: string;
  tools: ToolEvent[];
  errorText: string;
};

export type ConversationState = {
  turns: ConversationTurn[];
  activeTurnId: number | null;
};
