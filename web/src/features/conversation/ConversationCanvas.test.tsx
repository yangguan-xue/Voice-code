import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConversationCanvas } from "./ConversationCanvas";
import type { ConversationState } from "./types";

describe("ConversationCanvas", () => {
  it("shows error tools as finished error rows instead of loading spinners", () => {
    HTMLElement.prototype.scrollTo = () => {};
    const conversation: ConversationState = {
      activeTurnId: 1,
      turns: [
        {
          id: 1,
          sessionId: "demo_1",
          userText: "看看服务器上还有啥",
          assistantText: "",
          reasoningText: "",
          status: "completed",
          finishReason: "completed",
          errorText: "",
          tools: [
            {
              id: "tool-1",
              name: "bash",
              args: {},
              result: "This command is blocked in the web demo sandbox.",
              resultPreview: "This command is blocked in the web demo sandbox.",
              status: "error"
            }
          ]
        }
      ]
    };

    const { container } = render(
      <ConversationCanvas conversation={conversation} workspaceLabel="demo-repo" />
    );

    expect(screen.getByText("This command is blocked in the web demo sandbox.")).toBeInTheDocument();
    expect(container.querySelector(".tool-row-error")).not.toBeNull();
    expect(container.querySelector(".tool-state-running")).toBeNull();
  });

  it("shows expanded read output for direct content inspection", () => {
    HTMLElement.prototype.scrollTo = () => {};
    const conversation: ConversationState = {
      activeTurnId: 1,
      turns: [
        {
          id: 1,
          sessionId: "demo_1",
          userText: "直接把内容输出给我看",
          assistantText: "",
          reasoningText: "",
          status: "completed",
          finishReason: "completed",
          errorText: "",
          tools: [
            {
              id: "tool-1",
              name: "read",
              args: { file_path: "linux-sandboxing.md" },
              result: "1: # Linux 沙箱机制\n2: ## 概述",
              resultPreview: "1: # Linux 沙箱机制 2: ## 概述",
              status: "completed"
            }
          ]
        }
      ]
    };

    render(<ConversationCanvas conversation={conversation} workspaceLabel="demo-repo" />);

    expect(screen.getByLabelText("read 输出")).toHaveTextContent("1: # Linux 沙箱机制");
  });
});
