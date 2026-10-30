import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConversationCanvas } from "./ConversationCanvas";
import type { WebDemoClientLike } from "../../lib/bridge/protocol";
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

  it("collapses tool results by default and expands them on click", () => {
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

    expect(screen.queryByLabelText("read 输出")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "read 工具结果" }));
    expect(screen.getByLabelText("read 输出")).toHaveTextContent("1: # Linux 沙箱机制");

    fireEvent.click(screen.getByRole("button", { name: "read 工具结果" }));
    expect(screen.queryByLabelText("read 输出")).toBeNull();
  });

  it("renders generated image with a download action for image_gen results", async () => {
    HTMLElement.prototype.scrollTo = () => {};
    const conversation: ConversationState = {
      activeTurnId: 1,
      turns: [
        {
          id: 1,
          sessionId: "demo_1",
          userText: "画一只猫",
          assistantText: "",
          reasoningText: "",
          status: "completed",
          finishReason: "completed",
          errorText: "",
          tools: [
            {
              id: "tool-1",
              name: "image_gen",
              args: { prompt: "a cat" },
              result: "Saved generated image to ./output/cat.png",
              resultPreview: "Saved generated image to ./output/cat.png",
              status: "completed"
            }
          ]
        }
      ]
    };
    const client = {
      getSandboxFile: async () => ({
        path: "output/cat.png",
        content: "aGVsbG8=",
        encoding: "base64",
        mimeType: "image/png"
      })
    } as unknown as WebDemoClientLike;

    const createdUrls: string[] = [];
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = (blob: Blob) => {
      expect(blob.type).toBe("image/png");
      const url = `blob:mock-${createdUrls.length}`;
      createdUrls.push(url);
      return url;
    };
    URL.revokeObjectURL = () => {};

    try {
      render(
        <ConversationCanvas
          conversation={conversation}
          workspaceLabel="demo-repo"
          client={client}
          sessionToken="token"
        />
      );

      await waitFor(() => {
        expect(screen.getByRole("img", { name: "cat.png" })).toHaveAttribute("src", "blob:mock-0");
      });
      const download = screen.getByRole("link", { name: /下载/ });
      expect(download).toHaveAttribute("download", "cat.png");
      expect(download).toHaveAttribute("href", "blob:mock-0");
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
      URL.revokeObjectURL = originalRevokeObjectURL;
    }
  });
});
