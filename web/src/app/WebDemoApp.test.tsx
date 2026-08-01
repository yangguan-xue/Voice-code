import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WebDemoApp } from "./WebDemoApp";
import type { WebDemoClientLike, WebDemoEvent } from "../lib/bridge/protocol";

function makeClient(overrides: Partial<WebDemoClientLike> = {}) {
  const client: WebDemoClientLike = {
    connect: vi.fn(async () => undefined),
    createSession: vi.fn(async () => ({
      sessionId: "demo_1",
      sessionToken: "token-1",
      expiresAt: new Date(Date.now() + 30 * 60_000).toISOString(),
      workspaceLabel: "demo-python-app",
      permissionMode: "default",
      model: "test-model"
    })),
    startTurn: vi.fn(async () => ({
      sessionId: "demo_1",
      turnId: 1,
      finishReason: "completed"
    })),
    resolvePermission: vi.fn(async () => true),
    getSandboxFile: vi.fn(async () => ({
      path: "demo-python-app/app.py",
      content: "print('hello')\n"
    })),
    resetSandbox: vi.fn(async () => true),
    close: vi.fn(),
    ...overrides
  };
  return client;
}

describe("WebDemoApp", () => {
  it("shows the public GitHub repository link on the access screen", () => {
    const client = makeClient();
    render(<WebDemoApp clientFactory={() => client} />);

    const link = screen.getByRole("link", { name: /GitHub 仓库/ });

    expect(link).toHaveAttribute("href", "https://github.com/yangguan-xue/Voice-code");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("validates an empty access code", () => {
    const client = makeClient();
    render(<WebDemoApp clientFactory={() => client} />);

    fireEvent.click(screen.getByRole("button", { name: /Start demo/ }));

    expect(screen.getByText("请输入访问码。")).toBeInTheDocument();
    expect(client.createSession).not.toHaveBeenCalled();
  });

  it("creates a demo session from an access code", async () => {
    const client = makeClient();
    const clientFactory = vi.fn((_emit: (event: WebDemoEvent) => void) => client);
    render(<WebDemoApp clientFactory={clientFactory} />);

    fireEvent.change(screen.getByLabelText("访问码"), {
      target: { value: "dev-demo" }
    });
    fireEvent.click(screen.getByRole("button", { name: /Start demo/ }));

    expect(
      await screen.findByRole("heading", { name: "demo-python-app" })
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /GitHub/ })).toHaveAttribute(
      "href",
      "https://github.com/yangguan-xue/Voice-code"
    );
    expect(client.connect).toHaveBeenCalled();
    expect(client.createSession).toHaveBeenCalledWith("dev-demo");
  });

  it("downloads a patch when multiple changed files are reported", async () => {
    let emitEvent: ((event: WebDemoEvent) => void) | undefined;
    const client = makeClient();
    const clientFactory = vi.fn((emit: (event: WebDemoEvent) => void) => {
      emitEvent = emit;
      return client;
    });
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:voice-code-demo");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    let downloadedFileName = "";
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        downloadedFileName = this.download;
      });

    render(<WebDemoApp clientFactory={clientFactory} />);
    fireEvent.change(screen.getByLabelText("访问码"), {
      target: { value: "dev-demo" }
    });
    fireEvent.click(screen.getByRole("button", { name: /Start demo/ }));
    expect(await screen.findByRole("heading", { name: "demo-python-app" })).toBeInTheDocument();

    await act(async () => {
      emitEvent?.({
        type: "event",
        event: "sandbox.diff.updated",
        sessionId: "demo_1",
        payload: {
          changedFiles: [
            { path: "src/app.py", status: "modified", additions: 2, deletions: 1 },
            { path: "tests/test_app.py", status: "modified", additions: 4, deletions: 0 }
          ],
          patchAvailable: true,
          patch: "diff --git a/src/app.py b/src/app.py\n",
          diskUsageBytes: 256
        }
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "下载 patch" }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(downloadedFileName).toBe("voice-code-demo.patch");
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).not.toHaveBeenCalled();

    createObjectURL.mockRestore();
    revokeObjectURL.mockRestore();
    click.mockRestore();
  });
});
