import { Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";
import type { WorkspaceSummary } from "../../app/fixtures";
import type { ConversationState } from "./types";
import { TurnView } from "./TurnView";

type ConversationCanvasProps = {
  workspace: WorkspaceSummary;
  conversation: ConversationState;
};

export function ConversationCanvas({ workspace, conversation }: ConversationCanvasProps) {
  const hasTurns = conversation.turns.length > 0;
  const canvasRef = useRef<HTMLElement>(null);
  const shouldStickToBottomRef = useRef(true);
  const previousActiveTurnIdRef = useRef<number | null>(conversation.activeTurnId);
  const scrollKey = conversation.turns
    .map(
      (turn) =>
        `${turn.id}:${turn.userText.length}:${turn.reasoningText.length}:${turn.assistantText.length}:${turn.tools.length}:${turn.status}`
    )
    .join("|");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !hasTurns) {
      return;
    }

    const isNewTurn = previousActiveTurnIdRef.current !== conversation.activeTurnId;
    previousActiveTurnIdRef.current = conversation.activeTurnId;
    if (!isNewTurn && !shouldStickToBottomRef.current) {
      return;
    }

    if (typeof canvas.scrollTo === "function") {
      canvas.scrollTo({ top: canvas.scrollHeight, behavior: "auto" });
      shouldStickToBottomRef.current = true;
      return;
    }

    canvas.scrollTop = canvas.scrollHeight;
    shouldStickToBottomRef.current = true;
  }, [conversation.activeTurnId, hasTurns, scrollKey]);

  function handleCanvasScroll() {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    const distanceFromBottom = canvas.scrollHeight - canvas.scrollTop - canvas.clientHeight;
    shouldStickToBottomRef.current = distanceFromBottom < 80;
  }

  return (
    <main
      className="conversation-canvas"
      aria-label="会话画布"
      ref={canvasRef}
      tabIndex={0}
      onScroll={handleCanvasScroll}
    >
      {hasTurns ? (
        <div className="turn-stack">
          {conversation.turns.map((turn) => (
            <TurnView key={turn.id} turn={turn} />
          ))}
        </div>
      ) : (
        <div className="empty-state" role="status">
          <Sparkles size={44} strokeWidth={1.6} aria-hidden="true" />
          <div className="empty-state-copy">
            <p>{workspace.label}</p>
            <h1>准备开始一个本地任务</h1>
          </div>
        </div>
      )}
    </main>
  );
}
