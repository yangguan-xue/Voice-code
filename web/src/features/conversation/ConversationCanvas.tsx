import { AlertCircle, CheckCircle2, Loader2, TerminalSquare } from "lucide-react";
import { useEffect, useRef } from "react";
import { MarkdownMessage } from "./MarkdownMessage";
import type { ConversationState, ConversationTurn, ToolEvent } from "./types";

type ConversationCanvasProps = {
  conversation: ConversationState;
  workspaceLabel: string;
};

export function ConversationCanvas({ conversation, workspaceLabel }: ConversationCanvasProps) {
  const canvasRef = useRef<HTMLElement>(null);
  const hasTurns = conversation.turns.length > 0;
  const scrollKey = conversation.turns
    .map(
      (turn) =>
        `${turn.id}:${turn.assistantText.length}:${turn.reasoningText.length}:${turn.tools.length}:${turn.status}`
    )
    .join("|");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !hasTurns) {
      return;
    }
    canvas.scrollTo({ top: canvas.scrollHeight, behavior: "auto" });
  }, [hasTurns, scrollKey]);

  return (
    <main className="conversation-canvas" aria-label="会话" ref={canvasRef} tabIndex={0}>
      {hasTurns ? (
        <div className="turn-stack">
          {conversation.turns.map((turn) => (
            <TurnView key={turn.id} turn={turn} />
          ))}
        </div>
      ) : (
        <div className="empty-state" role="status">
          <p>{workspaceLabel}</p>
          <h1>让 agent 在这个临时沙盒里动手</h1>
        </div>
      )}
    </main>
  );
}

function TurnView({ turn }: { turn: ConversationTurn }) {
  return (
    <article className="turn-view" aria-label={`任务 ${turn.id}`}>
      <div className="message-row message-row-user">
        <p className="message-bubble user-turn">{turn.userText}</p>
      </div>
      <div className="message-row message-row-assistant">
        <div className="assistant-message">
          {turn.reasoningText ? <p className="reasoning-turn">{turn.reasoningText}</p> : null}
          {turn.tools.map((tool) => (
            <ToolRow key={tool.id} tool={tool} />
          ))}
          {turn.assistantText ? <MarkdownMessage text={turn.assistantText} /> : null}
          {turn.errorText ? <p className="error-turn">{turn.errorText}</p> : null}
        </div>
      </div>
    </article>
  );
}

function ToolRow({ tool }: { tool: ToolEvent }) {
  const isCompleted = tool.status === "completed";
  const isError = tool.status === "error";
  const showExpandedResult = tool.name === "read" && Boolean(tool.result?.trim());
  return (
    <div className={`tool-card tool-card-${tool.status}`}>
      <div className={`tool-row tool-row-${tool.status}`}>
        <TerminalSquare size={16} strokeWidth={1.8} aria-hidden="true" />
        <span className="tool-name">{tool.name}</span>
        <span className="tool-preview">{tool.resultPreview || formatArgs(tool.args)}</span>
        {isCompleted ? (
          <CheckCircle2 size={15} strokeWidth={1.8} className="tool-state" aria-hidden="true" />
        ) : isError ? (
          <AlertCircle size={15} strokeWidth={1.8} className="tool-state" aria-hidden="true" />
        ) : (
          <Loader2
            size={15}
            strokeWidth={1.8}
            className="tool-state tool-state-running"
            aria-hidden="true"
          />
        )}
      </div>
      {showExpandedResult ? (
        <pre className="tool-result-expanded" aria-label={`${tool.name} 输出`}>
          {tool.result}
        </pre>
      ) : null}
    </div>
  );
}

function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}
