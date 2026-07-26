import { CheckCircle2, Clipboard, Loader2, TerminalSquare } from "lucide-react";
import { useState } from "react";
import { MarkdownMessage } from "./MarkdownMessage";
import type { ConversationTurn, ToolEvent } from "./types";

type TurnViewProps = {
  turn: ConversationTurn;
};

export function TurnView({ turn }: TurnViewProps) {
  return (
    <article className="turn-view" aria-label={`任务 ${turn.id}`}>
      <div className="message-row message-row-user" aria-label="用户消息">
        <p className="user-turn message-bubble">{turn.userText}</p>
      </div>

      <div className="message-row message-row-assistant" aria-label="模型消息">
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
  const isDone = tool.status === "completed";
  const writtenPath = getWrittenFilePath(tool);
  return (
    <div className={writtenPath ? "tool-row tool-row-with-detail" : "tool-row"}>
      <div className="tool-row-main">
        <TerminalSquare size={16} strokeWidth={1.8} />
        <span className="tool-name">{tool.name}</span>
        {writtenPath ? (
          <span className="tool-preview">已写入文件</span>
        ) : (
          <span className="tool-preview">{tool.resultPreview || formatArgs(tool.args)}</span>
        )}
        {isDone ? (
          <CheckCircle2 size={15} strokeWidth={1.8} className="tool-state" />
        ) : (
          <Loader2 size={15} strokeWidth={1.8} className="tool-state tool-state-running" />
        )}
      </div>
      {writtenPath ? <WrittenFilePath path={writtenPath} /> : null}
    </div>
  );
}

function WrittenFilePath({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);

  async function copyPath() {
    await navigator.clipboard?.writeText(path);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="tool-path-detail">
      <code className="tool-path" title={path}>
        {path}
      </code>
      <button
        type="button"
        className="tool-path-copy"
        aria-label={copied ? "已复制文件路径" : "复制文件路径"}
        onClick={copyPath}
      >
        <Clipboard size={14} strokeWidth={1.8} />
      </button>
    </div>
  );
}

function getWrittenFilePath(tool: ToolEvent): string {
  if (tool.name !== "write") {
    return "";
  }

  const pathFromArgs = readArgString(tool.args, "file_path") || readArgString(tool.args, "filePath");
  if (pathFromArgs) {
    return pathFromArgs;
  }

  const match = tool.result.match(/^The file (.+?) has been\b/i);
  return match?.[1]?.trim() ?? "";
}

function readArgString(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  return typeof value === "string" ? value : "";
}

function formatArgs(args: Record<string, unknown>): string {
  const pairs = Object.entries(args).map(([key, value]) => `${key}=${String(value)}`);
  return pairs.join(" · ");
}
