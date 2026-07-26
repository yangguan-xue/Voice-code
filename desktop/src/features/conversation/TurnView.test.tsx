import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TurnView } from "./TurnView";
import type { ConversationTurn } from "./types";

describe("TurnView", () => {
  it("shows the full written file path for write tool results", () => {
    const writtenPath = String.raw`D:\workspace\voice-code\冒泡算法讲解.md`;
    const turn: ConversationTurn = {
      id: 1,
      sessionId: "s1",
      userText: "帮我生成一份 md 文档",
      assistantText: "",
      reasoningText: "",
      status: "completed",
      finishReason: "completed",
      tools: [
        {
          id: "tool-1",
          name: "write",
          args: {
            file_path: writtenPath,
            content: "# 冒泡算法讲解"
          },
          result: `The file ${writtenPath} has been written successfully.`,
          resultPreview: `The file ${writtenPath} has be...`,
          status: "completed"
        }
      ],
      errorText: ""
    };

    render(<TurnView turn={turn} />);

    expect(screen.getByText("已写入文件")).toBeInTheDocument();
    expect(screen.getByText(writtenPath)).toBeInTheDocument();
  });
});
