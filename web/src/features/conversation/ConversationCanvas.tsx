import { AlertCircle, CheckCircle2, ChevronDown, Loader2, TerminalSquare } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { WebDemoClientLike } from "../../lib/bridge/protocol";
import { GeneratedImageCard } from "./GeneratedImageCard";
import { MarkdownMessage } from "./MarkdownMessage";
import type { ConversationState, ConversationTurn, ToolEvent } from "./types";

type ConversationCanvasProps = {
  conversation: ConversationState;
  workspaceLabel: string;
  client?: WebDemoClientLike;
  sessionToken?: string;
};

export function ConversationCanvas({ conversation, workspaceLabel, client, sessionToken }: ConversationCanvasProps) {
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
            <TurnView key={turn.id} turn={turn} client={client} sessionToken={sessionToken} />
          ))}
        </div>
      ) : (
        <div className="empty-state" role="status">
          <p>{workspaceLabel}</p>
          <h1>让 agent 在你的专属沙盒里动手</h1>
        </div>
      )}
    </main>
  );
}

function TurnView({
  turn,
  client,
  sessionToken
}: {
  turn: ConversationTurn;
  client?: WebDemoClientLike;
  sessionToken?: string;
}) {
  return (
    <article className="turn-view" aria-label={`任务 ${turn.id}`}>
      <div className="message-row message-row-user">
        <p className="message-bubble user-turn">{turn.userText}</p>
      </div>
      <div className="message-row message-row-assistant">
        <div className="assistant-message">
          {turn.reasoningText ? <p className="reasoning-turn">{turn.reasoningText}</p> : null}
          {turn.tools.map((tool) => (
            <ToolRow key={tool.id} tool={tool} client={client} sessionToken={sessionToken} />
          ))}
          {turn.assistantText ? <MarkdownMessage text={turn.assistantText} /> : null}
          {turn.errorText ? <p className="error-turn">{turn.errorText}</p> : null}
        </div>
      </div>
    </article>
  );
}

const IMAGE_TOOL_NAMES = new Set(["image_gen", "image_edit"]);

const IMAGE_PATH_PATTERN = /\.\/[^\s"']+?\.(?:png|jpe?g|gif|webp)/gi;

function extractGeneratedImagePaths(result: string): string[] {
  return Array.from(new Set(result.match(IMAGE_PATH_PATTERN) ?? []));
}

function ToolRow({
  tool,
  client,
  sessionToken
}: {
  tool: ToolEvent;
  client?: WebDemoClientLike;
  sessionToken?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const isCompleted = tool.status === "completed";
  const isError = tool.status === "error";
  const hasResult = Boolean(tool.result?.trim());
  const showExpandedResult = hasResult && expanded;
  const imagePaths =
    IMAGE_TOOL_NAMES.has(tool.name) && isCompleted && client && sessionToken
      ? extractGeneratedImagePaths(tool.result)
      : [];
  return (
    <div className={`tool-card tool-card-${tool.status}`}>
      <div
        className={`tool-row tool-row-${tool.status}${hasResult ? " tool-row-expandable" : ""}`}
        role={hasResult ? "button" : undefined}
        tabIndex={hasResult ? 0 : undefined}
        aria-expanded={hasResult ? expanded : undefined}
        aria-label={hasResult ? `${tool.name} 工具结果` : undefined}
        onClick={hasResult ? () => setExpanded((value) => !value) : undefined}
        onKeyDown={
          hasResult
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setExpanded((value) => !value);
                }
              }
            : undefined
        }
      >
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
        {hasResult ? (
          <ChevronDown
            size={14}
            strokeWidth={1.8}
            className={`tool-chevron${expanded ? " tool-chevron-open" : ""}`}
            aria-hidden="true"
          />
        ) : null}
      </div>
      {showExpandedResult ? (
        <pre className="tool-result-expanded" aria-label={`${tool.name} 输出`}>
          {tool.result}
        </pre>
      ) : null}
      {imagePaths.map((path) => (
        <GeneratedImageCard
          key={path}
          path={path}
          client={client as WebDemoClientLike}
          sessionToken={sessionToken as string}
        />
      ))}
    </div>
  );
}

function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}
