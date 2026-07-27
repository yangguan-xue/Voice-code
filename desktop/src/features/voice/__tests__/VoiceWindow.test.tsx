import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VoiceWindow } from "../VoiceWindow";
import type { VoiceState } from "../voice-types";

describe("VoiceWindow", () => {
  it("renders user messages on the right and assistant markdown as separate content", () => {
    render(
      <VoiceWindow
        state={voiceStateWithMessages()}
        workspaceLabel="reasoning"
        branch="main"
        contextMode="sharedSession"
        sourceSessionTitle="查看项目结构"
        contextMessageCount={4}
        contextVersion={3}
        contextSyncStatus="synced"
        draft=""
        isBusy={false}
        bridgeError=""
        onDraftChange={vi.fn()}
        onSubmitDraft={vi.fn()}
        onStartListening={vi.fn()}
        onStopListening={vi.fn()}
        onToggleMute={vi.fn()}
        onClearTranscript={vi.fn()}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByRole("main", { name: "语音助手" })).toBeInTheDocument();
    expect(screen.getByText("reasoning · main")).toBeInTheDocument();
    expect(screen.getByText("引用 查看项目结构 · 4 条上下文 · v3 已同步")).toBeInTheDocument();
    expect(screen.getByLabelText("用户语音消息")).toHaveClass("voice-message-user");
    expect(screen.getByLabelText("AI 语音回复")).toHaveClass("voice-message-assistant");
    expect(screen.getByText("可以。")).toBeInTheDocument();
    expect(screen.getByText('const ok = true;')).toBeInTheDocument();
  });

  it("uses icon controls for voice actions", () => {
    const startListening = vi.fn();
    const clearTranscript = vi.fn();

    render(
      <VoiceWindow
        state={voiceStateWithMessages()}
        workspaceLabel="reasoning"
        branch="main"
        contextMode="independentSession"
        sourceSessionTitle=""
        contextMessageCount={0}
        contextVersion={0}
        contextSyncStatus="synced"
        draft=""
        isBusy={false}
        bridgeError=""
        onDraftChange={vi.fn()}
        onSubmitDraft={vi.fn()}
        onStartListening={startListening}
        onStopListening={vi.fn()}
        onToggleMute={vi.fn()}
        onClearTranscript={clearTranscript}
        onClose={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "开始聆听" }));
    fireEvent.click(screen.getByRole("button", { name: "清空语音记录" }));

    expect(startListening).toHaveBeenCalledOnce();
    expect(clearTranscript).toHaveBeenCalledOnce();
  });

  it("submits the typed voice fallback", () => {
    const submit = vi.fn();
    const change = vi.fn();

    render(
      <VoiceWindow
        state={voiceStateWithMessages()}
        workspaceLabel="reasoning"
        branch="main"
        contextMode="independentSession"
        sourceSessionTitle=""
        contextMessageCount={0}
        contextVersion={0}
        contextSyncStatus="synced"
        draft="查看项目"
        isBusy={false}
        bridgeError=""
        onDraftChange={change}
        onSubmitDraft={submit}
        onStartListening={vi.fn()}
        onStopListening={vi.fn()}
        onToggleMute={vi.fn()}
        onClearTranscript={vi.fn()}
        onClose={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "发送语音指令" }));

    expect(submit).toHaveBeenCalledOnce();
  });

  it("disables interrupt while idle", () => {
    render(
      <VoiceWindow
        state={voiceStateWithMessages()}
        workspaceLabel="reasoning"
        branch="main"
        contextMode="independentSession"
        sourceSessionTitle=""
        contextMessageCount={0}
        contextVersion={0}
        contextSyncStatus="synced"
        draft=""
        isBusy={false}
        bridgeError=""
        onDraftChange={vi.fn()}
        onSubmitDraft={vi.fn()}
        onStartListening={vi.fn()}
        onStopListening={vi.fn()}
        onToggleMute={vi.fn()}
        onClearTranscript={vi.fn()}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: "中断当前语音" })).toBeDisabled();
  });
});

function voiceStateWithMessages(): VoiceState {
  return {
    status: "idle",
    statusText: "准备好了",
    partialUserText: "",
    ttsMuted: false,
    isCapturing: false,
    messages: [
      {
        id: "u1",
        role: "user",
        text: "给我一个代码例子"
      },
      {
        id: "a1",
        role: "assistant",
        text: "可以。\n\n```ts\nconst ok = true;\n```"
      }
    ]
  };
}
