import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { Mic, MicOff, Pause, Send, Trash2, Volume2, VolumeX, X } from "lucide-react";
import { IconButton } from "../../components/IconButton";
import { MarkdownMessage } from "../conversation/MarkdownMessage";
import type { VoiceLaunchMode } from "./voice-launch-context";
import type { VoiceMessage, VoiceState } from "./voice-types";

type VoiceWindowProps = {
  state: VoiceState;
  workspaceLabel: string;
  branch: string;
  contextMode: VoiceLaunchMode;
  sourceSessionTitle: string;
  contextMessageCount: number;
  contextVersion: number;
  contextSyncStatus: "synced" | "pending";
  draft: string;
  isBusy: boolean;
  bridgeError: string;
  onDraftChange: (value: string) => void;
  onSubmitDraft: () => void;
  onStartListening: () => void;
  onStopListening: () => void;
  onToggleMute: () => void;
  onClearTranscript: () => void;
  onClose: () => void;
};

export function VoiceWindow({
  state,
  workspaceLabel,
  branch,
  contextMode,
  sourceSessionTitle,
  contextMessageCount,
  contextVersion,
  contextSyncStatus,
  draft,
  isBusy,
  bridgeError,
  onDraftChange,
  onSubmitDraft,
  onStartListening,
  onStopListening,
  onToggleMute,
  onClearTranscript,
  onClose
}: VoiceWindowProps) {
  const isListening = state.status === "listening";
  const canPause =
    isListening ||
    isBusy ||
    state.status === "thinking" ||
    state.status === "transcribing" ||
    state.status === "speaking";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmitDraft();
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    const nativeEvent = event.nativeEvent as KeyboardEvent & { isComposing?: boolean };
    if (event.key !== "Enter" || nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    onSubmitDraft();
  }

  return (
    <main className="voice-window" aria-label="语音助手">
      <header className="voice-header">
        <div>
          <h1>语音助手</h1>
          <p>
            {workspaceLabel} · {branch}
          </p>
          <p className="voice-context-copy">
            {voiceContextCopy(
              contextMode,
              sourceSessionTitle,
              contextMessageCount,
              contextVersion,
              contextSyncStatus
            )}
          </p>
        </div>
        <IconButton label="关闭语音窗口" onClick={onClose}>
          <X size={18} strokeWidth={1.9} />
        </IconButton>
      </header>

      <section className="voice-stage" aria-label="语音状态">
        <div className={`voice-orb voice-orb-${state.status}`} aria-hidden="true">
          {isListening ? <Mic size={30} strokeWidth={1.8} /> : <Volume2 size={30} strokeWidth={1.8} />}
        </div>
        <h2>{state.statusText}</h2>
        {state.partialUserText ? <p className="voice-partial">{state.partialUserText}</p> : null}
      </section>

      <VoiceTranscript messages={state.messages} />

      {bridgeError ? (
        <div className="voice-bridge-error" role="status">
          {bridgeError}
        </div>
      ) : null}

      <footer className="voice-controls" aria-label="语音控制">
        <form className="voice-draft-form" aria-label="语音文字备用输入" onSubmit={handleSubmit}>
          <input
            aria-label="输入语音指令"
            value={draft}
            disabled={isBusy}
            placeholder="输入一句语音指令的文字版本..."
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <IconButton label="发送语音指令" tone="send" type="submit" disabled={isBusy}>
            <Send size={18} strokeWidth={1.9} />
          </IconButton>
        </form>
        <div className="voice-control-row">
          <IconButton
            label={isListening ? "停止聆听" : "开始聆听"}
            tone={isListening ? "accent" : "send"}
            onClick={isListening ? onStopListening : onStartListening}
          >
            {isListening ? (
              <MicOff size={19} strokeWidth={1.9} />
            ) : (
              <Mic size={19} strokeWidth={1.9} />
            )}
          </IconButton>
          <IconButton
            label={isListening ? "停止聆听" : "中断当前语音"}
            onClick={onStopListening}
            disabled={!canPause}
          >
            <Pause size={18} strokeWidth={1.9} />
          </IconButton>
          <IconButton label={state.ttsMuted ? "开启播报" : "静音播报"} onClick={onToggleMute}>
            {state.ttsMuted ? (
              <VolumeX size={18} strokeWidth={1.9} />
            ) : (
              <Volume2 size={18} strokeWidth={1.9} />
            )}
          </IconButton>
          <div className="voice-controls-spacer" />
          <IconButton label="清空语音记录" onClick={onClearTranscript}>
            <Trash2 size={18} strokeWidth={1.9} />
          </IconButton>
        </div>
      </footer>
    </main>
  );
}

function voiceContextCopy(
  mode: VoiceLaunchMode,
  sourceSessionTitle: string,
  contextMessageCount: number,
  contextVersion: number,
  contextSyncStatus: "synced" | "pending"
): string {
  if (mode === "parallelWorktree") {
    const suffix = contextMessageCount > 0 ? ` · ${contextMessageCount} 条初始上下文` : "";
    return `并行任务 · worktree${suffix}`;
  }
  if (mode !== "sharedSession") {
    return "独立语音会话";
  }
  const title = sourceSessionTitle || "当前会话";
  const syncCopy = contextSyncStatus === "pending" ? "本轮结束后同步" : `v${contextVersion} 已同步`;
  if (contextMessageCount === 0) {
    return `引用 ${title}，暂无可读上下文 · ${syncCopy}`;
  }
  return `引用 ${title} · ${contextMessageCount} 条上下文 · ${syncCopy}`;
}

type VoiceTranscriptProps = {
  messages: VoiceMessage[];
};

export function VoiceTranscript({ messages }: VoiceTranscriptProps) {
  if (messages.length === 0) {
    return (
      <section className="voice-transcript voice-transcript-empty" aria-label="语音对话">
        <p>语音窗口已准备好。</p>
      </section>
    );
  }

  return (
    <section className="voice-transcript" role="log" aria-label="语音对话" aria-live="polite">
      {messages.map((message) => (
        <VoiceMessageBlock key={message.id} message={message} />
      ))}
    </section>
  );
}

function VoiceMessageBlock({ message }: { message: VoiceMessage }) {
  if (message.role === "status" || message.role === "error") {
    return (
      <div className={`voice-message voice-message-${message.role}`} role="status">
        {message.text}
      </div>
    );
  }

  return (
    <article
      className={`voice-message voice-message-${message.role}`}
      aria-label={message.role === "user" ? "用户语音消息" : "AI 语音回复"}
    >
      {message.role === "assistant" ? (
        <MarkdownMessage text={message.text} />
      ) : (
        <p>{message.text}</p>
      )}
    </article>
  );
}
