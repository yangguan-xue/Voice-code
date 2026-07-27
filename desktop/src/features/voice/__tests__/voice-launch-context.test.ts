import { describe, expect, it } from "vitest";
import type { ConversationState } from "../../conversation/types";
import {
  buildVoiceContextMessages,
  readVoiceLaunchContext,
  writeVoiceLaunchContext
} from "../voice-launch-context";

describe("voice launch context", () => {
  it("serializes only user and assistant text from a conversation", () => {
    const conversation: ConversationState = {
      activeTurnId: 1,
      turns: [
        {
          id: 1,
          sessionId: "task-1",
          userText: "用户问题",
          assistantText: "模型回答",
          reasoningText: "内部思考",
          status: "completed",
          finishReason: "completed",
          tools: [
            {
              id: "tool-1",
              name: "bash",
              args: { command: "pwd" },
              result: "/tmp",
              resultPreview: "/tmp",
              status: "completed"
            }
          ],
          errorText: ""
        }
      ]
    };

    expect(buildVoiceContextMessages(conversation)).toEqual([
      { role: "user", content: "用户问题" },
      { role: "assistant", content: "模型回答" }
    ]);
  });

  it("round-trips launch context through localStorage", () => {
    writeVoiceLaunchContext({
      mode: "sharedSession",
      workspaceLabel: "new",
      workspacePath: "/workspace/new",
      branch: "main",
      sourceSessionId: "task-1",
      sourceSessionTitle: "查看项目",
      contextVersion: 2,
      contextMessages: [{ role: "user", content: "之前说过什么？" }]
    });

    expect(readVoiceLaunchContext()).toEqual({
      mode: "sharedSession",
      workspaceLabel: "new",
      workspacePath: "/workspace/new",
      branch: "main",
      sourceSessionId: "task-1",
      sourceSessionTitle: "查看项目",
      contextVersion: 2,
      contextMessages: [{ role: "user", content: "之前说过什么？" }]
    });
  });

  it("normalizes legacy launch mode names", () => {
    window.localStorage.setItem(
      "voiceLaunchContext",
      JSON.stringify({
        mode: "sharedContext",
        workspaceLabel: "new",
        branch: "main",
        sourceSessionId: "task-1",
        sourceSessionTitle: "旧会话",
        contextMessages: []
      })
    );

    expect(readVoiceLaunchContext().mode).toBe("sharedSession");
  });
});
