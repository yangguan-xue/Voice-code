import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WebDemoApp } from "./WebDemoApp";
import type {
  ConversationListPayload,
  TenantSessionPayload,
  WebDemoClientLike,
  WebDemoEvent
} from "../lib/bridge/protocol";

const SESSION: TenantSessionPayload = {
  sessionId: "conv-1",
  conversationId: "conv-1",
  tenantId: "alice",
  tenantLabel: "Alice",
  title: "",
  workspaceLabel: "demo-python-app",
  permissionMode: "default",
  model: "test-model"
};

const EMPTY_LIST: ConversationListPayload = {
  conversations: [],
  currentConversationId: "conv-1"
};

function makeClient(overrides: Partial<WebDemoClientLike> = {}) {
  const client: WebDemoClientLike = {
    connect: vi.fn(async () => undefined),
    login: vi.fn(async () => ({
      tenantToken: "token-1",
      tenantId: "alice",
      tenantLabel: "Alice",
      expiresAt: new Date(Date.now() + 30 * 24 * 3600_000).toISOString()
    })),
    fetchMe: vi.fn(async () => ({ tenantId: "alice", tenantLabel: "Alice" })),
    logout: vi.fn(async () => undefined),
    attachSession: vi.fn(async () => SESSION),
    listConversations: vi.fn(async () => EMPTY_LIST),
    newConversation: vi.fn(async () => ({ ...SESSION, conversationId: "conv-2", sessionId: "conv-2" })),
    activateConversation: vi.fn(async () => SESSION),
    getConversationMessages: vi.fn(async () => []),
    startTurn: vi.fn(async () => ({
      sessionId: "conv-1",
      turnId: 1,
      finishReason: "completed"
    })),
    resolvePermission: vi.fn(async () => true),
    uploadFile: vi.fn(async (_token: string, path: string, file: File) => ({
      path,
      size: file.size
    })),
    getSandboxFile: vi.fn(async () => ({
      path: "demo-python-app/app.py",
      content: "print('hello')\n",
      encoding: "utf-8",
      mimeType: "text/plain;charset=utf-8"
    })),
    resetSandbox: vi.fn(async () => true),
    close: vi.fn(),
    ...overrides
  };
  return client;
}

describe("WebDemoApp（租户模型）", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("无 token 时显示登录页与 GitHub 链接", () => {
    const client = makeClient();
    render(<WebDemoApp clientFactory={() => client} />);

    expect(screen.getByRole("heading", { name: "访问码登录" })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /GitHub 仓库/ });
    expect(link).toHaveAttribute("href", "https://github.com/yangguan-xue/Voice-code");
    expect(client.login).not.toHaveBeenCalled();
  });

  it("校验空访问码", () => {
    const client = makeClient();
    render(<WebDemoApp clientFactory={() => client} />);

    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));

    expect(screen.getByText("请输入访问码。")).toBeInTheDocument();
    expect(client.login).not.toHaveBeenCalled();
  });

  it("访问码登录成功后进入会话页并持久化 token", async () => {
    const client = makeClient();
    render(<WebDemoApp clientFactory={() => client} />);

    fireEvent.change(screen.getByLabelText("访问码"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));

    expect(
      await screen.findByRole("heading", { name: "demo-python-app" })
    ).toBeInTheDocument();
    expect(client.login).toHaveBeenCalledWith("alice");
    expect(client.attachSession).toHaveBeenCalledWith("token-1");
    expect(window.localStorage.getItem("voice-code.web-demo.tenant-token")).toBe("token-1");
    // 不再有倒计时/过期文案
    expect(screen.queryByText(/分钟/)).not.toBeInTheDocument();
  });

  it("有本地 token 时免登录直接恢复上次会话与历史", async () => {
    window.localStorage.setItem("voice-code.web-demo.tenant-token", "saved-token");
    const client = makeClient({
      getConversationMessages: vi.fn(async () => [
        {
          id: 1,
          userText: "上次的问题",
          assistantText: "上次的回答",
          reasoningText: "",
          status: "completed" as const,
          finishReason: "completed",
          tools: []
        }
      ])
    });
    render(<WebDemoApp clientFactory={() => client} />);

    expect(
      await screen.findByRole("heading", { name: "demo-python-app" })
    ).toBeInTheDocument();
    expect(client.login).not.toHaveBeenCalled();
    expect(client.attachSession).toHaveBeenCalledWith("saved-token");
    expect(await screen.findByText("上次的问题")).toBeInTheDocument();
    expect(screen.getByText("上次的回答")).toBeInTheDocument();
  });

  it("恢复历史后新回合复用 turnId 时不覆盖历史消息", async () => {
    window.localStorage.setItem("voice-code.web-demo.tenant-token", "saved-token");
    let emitEvent: ((event: WebDemoEvent) => void) | null = null;
    const client = makeClient({
      getConversationMessages: vi.fn(async () => [
        {
          id: 1,
          userText: "上次的问题",
          assistantText: "上次的回答",
          reasoningText: "",
          status: "completed" as const,
          finishReason: "completed",
          tools: []
        }
      ]),
      startTurn: vi.fn(async () => {
        emitEvent?.({
          type: "event",
          event: "agent.turn.started",
          sessionId: "conv-1",
          turnId: 1,
          payload: { userText: "新的问题" }
        });
        emitEvent?.({
          type: "event",
          event: "agent.text.delta",
          sessionId: "conv-1",
          turnId: 1,
          payload: { content: "新的回答" }
        });
        return { sessionId: "conv-1", turnId: 1, finishReason: "completed" };
      })
    });
    render(
      <WebDemoApp
        clientFactory={(emit) => {
          emitEvent = emit;
          return client;
        }}
      />
    );

    expect(await screen.findByText("上次的回答")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("输入任务"), { target: { value: "新的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("新的回答")).toBeInTheDocument();
    expect(screen.getByText("上次的问题")).toBeInTheDocument();
    expect(screen.getByText("上次的回答")).toBeInTheDocument();
  });

  it("token 失效时清空本地态回到登录页", async () => {
    window.localStorage.setItem("voice-code.web-demo.tenant-token", "stale-token");
    const client = makeClient({
      attachSession: vi.fn(async () => {
        throw new Error("请求失败（HTTP 401）");
      })
    });
    render(<WebDemoApp clientFactory={() => client} />);

    expect(
      await screen.findByRole("heading", { name: "访问码登录" })
    ).toBeInTheDocument();
    expect(window.localStorage.getItem("voice-code.web-demo.tenant-token")).toBeNull();
  });

  it("侧边栏切换会话时不闪访问码登录页", async () => {
    const client = makeClient({
      listConversations: vi.fn(async () => ({
        conversations: [
          {
            conversationId: "conv-1",
            title: "当前会话",
            updatedAt: new Date().toISOString(),
            isCurrent: true
          },
          {
            conversationId: "conv-0",
            title: "昨天的会话",
            updatedAt: new Date(Date.now() - 3600_000).toISOString(),
            isCurrent: false
          }
        ],
        currentConversationId: "conv-1"
      }))
    });
    render(<WebDemoApp clientFactory={() => client} />);

    fireEvent.change(screen.getByLabelText("访问码"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));
    expect(await screen.findByText("昨天的会话")).toBeInTheDocument();

    fireEvent.click(screen.getByText("昨天的会话"));

    expect(screen.queryByRole("heading", { name: "访问码登录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "登录中" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "demo-python-app" })).toBeInTheDocument();
    await waitFor(() => {
      expect(client.activateConversation).toHaveBeenCalledWith("token-1", "conv-0");
    });
    expect(client.getConversationMessages).toHaveBeenCalledWith("token-1", "conv-0");
  });

  it("新会话按钮开新会话并清空画布", async () => {
    const client = makeClient();
    render(<WebDemoApp clientFactory={() => client} />);

    fireEvent.change(screen.getByLabelText("访问码"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));
    await screen.findByRole("heading", { name: "demo-python-app" });

    fireEvent.click(screen.getByRole("button", { name: /新会话/ }));

    await waitFor(() => {
      expect(client.newConversation).toHaveBeenCalledWith("token-1");
    });
  });

  it("退出登录清除 token 并回到登录页", async () => {
    const client = makeClient();
    render(<WebDemoApp clientFactory={() => client} />);

    fireEvent.change(screen.getByLabelText("访问码"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));
    await screen.findByRole("heading", { name: "demo-python-app" });

    fireEvent.click(screen.getByRole("button", { name: /退出/ }));

    expect(
      await screen.findByRole("heading", { name: "访问码登录" })
    ).toBeInTheDocument();
    expect(client.logout).toHaveBeenCalledWith("token-1");
    expect(window.localStorage.getItem("voice-code.web-demo.tenant-token")).toBeNull();
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
    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));
    expect(await screen.findByRole("heading", { name: "demo-python-app" })).toBeInTheDocument();

    await act(async () => {
      emitEvent?.({
        type: "event",
        event: "sandbox.diff.updated",
        sessionId: "conv-1",
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
