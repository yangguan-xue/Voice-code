import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { openDiagnosticsPath, readDesktopDiagnostics } from "../lib/desktop/diagnostics";
import { selectWorkspaceFolder } from "../lib/desktop/workspace-picker";

vi.mock("../lib/desktop/diagnostics", () => ({
  openDiagnosticsPath: vi.fn(async () => false),
  readDesktopDiagnostics: vi.fn(async () => null)
}));

vi.mock("../lib/desktop/workspace-picker", () => ({
  selectWorkspaceFolder: vi.fn(async () => null)
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(openDiagnosticsPath).mockResolvedValue(false);
  vi.mocked(readDesktopDiagnostics).mockResolvedValue(null);
  vi.mocked(selectWorkspaceFolder).mockResolvedValue(null);
  window.localStorage.clear();
});

describe("App", () => {
  it("renders the desktop workspace shell", () => {
    render(<App />);

    expect(screen.getByRole("navigation", { name: "项目和任务" })).toBeInTheDocument();
    expect(screen.getByRole("main", { name: "会话画布" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "新任务输入" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "voice-code" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "选择文件夹，当前项目 voice-code" })).not.toBeInTheDocument();
    expect(screen.getByText("准备开始一个本地任务")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "voice-code 任务" })).toBeInTheDocument();
    expect(screen.getByText("提交任务后会出现在这里")).toBeInTheDocument();
    expect(screen.queryByText("本地任务")).not.toBeInTheDocument();
    expect(screen.queryByText("Mac 桌面应用壳")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("当前本地用户")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("权限模式：完全访问")).not.toBeInTheDocument();
  });

  it("changes the composer permission mode", () => {
    render(<App />);

    const selector = screen.getByRole("combobox", { name: "权限模式" });
    fireEvent.change(selector, { target: { value: "只读模式" } });

    expect(selector).toHaveValue("只读模式");
  });

  it("asks how voice should use the current conversation before opening the companion", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "语音输入" }));

    expect(screen.getByRole("dialog", { name: "打开语音助手" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /并行语音任务/ })).toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /独立语音会话/ }));

    expect(open).toHaveBeenCalledWith("#/voice", "voice", "popup,width=520,height=680");
    expect(window.localStorage.getItem("voiceLaunchContext")).toContain(
      '"mode":"independentSession"'
    );
  });

  it("opens voice with a user-assistant-only snapshot of the active conversation", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<App />);

    fireEvent.change(screen.getByRole("textbox", { name: "输入任务" }), {
      target: { value: "查看项目结构" }
    });
    fireEvent.click(screen.getByRole("button", { name: "发送任务" }));
    expect(await screen.findByText("我先看项目结构，然后确认桌面入口。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "语音输入" }));
    fireEvent.click(screen.getByRole("button", { name: /引用当前会话/ }));

    expect(open).toHaveBeenCalledWith("#/voice", "voice", "popup,width=520,height=680");
    const context = JSON.parse(window.localStorage.getItem("voiceLaunchContext") ?? "{}");
    expect(context.mode).toBe("sharedSession");
    expect(context.contextVersion).toEqual(expect.any(Number));
    expect(context.workspacePath).toContain("voice-code");
    expect(context.contextMessages).toEqual(
      expect.arrayContaining([
        { role: "user", content: "查看项目结构" },
        {
          role: "assistant",
          content: expect.stringContaining("我先看项目结构")
        }
      ])
    );
    expect(JSON.stringify(context.contextMessages)).not.toContain("grep");
  });

  it("opens voice in parallel worktree mode with an initial context snapshot", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<App />);

    fireEvent.change(screen.getByRole("textbox", { name: "输入任务" }), {
      target: { value: "先建立上下文" }
    });
    fireEvent.click(screen.getByRole("button", { name: "发送任务" }));
    expect(await screen.findByText("我先看项目结构，然后确认桌面入口。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "语音输入" }));
    fireEvent.click(screen.getByRole("button", { name: /并行语音任务/ }));

    expect(open).toHaveBeenCalledWith("#/voice", "voice", "popup,width=520,height=680");
    const context = JSON.parse(window.localStorage.getItem("voiceLaunchContext") ?? "{}");
    expect(context.mode).toBe("parallelWorktree");
    expect(context.contextMessages).toEqual(
      expect.arrayContaining([{ role: "user", content: "先建立上下文" }])
    );
  });

  it("loads saved sessions grouped by workspace folder on bootstrap", async () => {
    render(<App />);

    expect(await screen.findByRole("button", { name: "冒泡算法文档" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "voice-code 任务" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "notes 任务" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Windows 桌面构建" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "整理笔记" })).toBeInTheDocument();
  });

  it("keeps workspace groups when switching folders from the composer", async () => {
    vi.mocked(selectWorkspaceFolder)
      .mockResolvedValueOnce({ label: "alpha", path: "/Users/example/alpha" })
      .mockResolvedValueOnce({ label: "beta", path: "/Users/example/beta" })
      .mockResolvedValueOnce({ label: "alpha", path: "/Users/example/alpha" });

    render(<App />);
    expect(await screen.findByRole("button", { name: "冒泡算法文档" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "voice-code 任务" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "voice-code" }));
    expect(await screen.findByRole("region", { name: "alpha 任务" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "alpha" }));
    expect(await screen.findByRole("region", { name: "beta 任务" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "alpha 任务" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "voice-code 任务" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "beta" }));
    expect(await screen.findByRole("button", { name: "alpha" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "beta 任务" })).toBeInTheDocument();
  });

  it("restores a saved session into the conversation canvas", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "冒泡算法文档" }));

    expect(await screen.findByText("恢复的历史任务")).toBeInTheDocument();
    expect(await screen.findByText("这是一段已恢复的历史回复。")).toBeInTheDocument();
  });

  it("selects a real git branch and model profile", async () => {
    render(<App />);

    const branch = await screen.findByRole("combobox", { name: "Git 分支" });
    fireEvent.change(branch, { target: { value: "main" } });
    expect(await screen.findByRole("combobox", { name: "Git 分支" })).toHaveValue("main");

    const model = screen.getByRole("combobox", { name: "模型配置" });
    fireEvent.change(model, { target: { value: "deepseek" } });
    expect(await screen.findByRole("combobox", { name: "模型配置" })).toHaveValue("deepseek");
  });

  it("creates and deletes a saved model configuration from settings", async () => {
    render(<App />);
    await screen.findByRole("region", { name: "voice-code 任务" });

    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByRole("dialog", { name: "设置" })).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "显示名称" }), {
      target: { value: "公司 GPT" }
    });
    fireEvent.change(screen.getByRole("textbox", { name: "API 地址" }), {
      target: { value: "https://llm.example.com/v1" }
    });
    fireEvent.change(screen.getByLabelText("API Key"), {
      target: { value: "test-secret" }
    });
    fireEvent.change(screen.getByRole("textbox", { name: "模型名称" }), {
      target: { value: "gpt-company" }
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "模型配置" })).toHaveDisplayValue(
        "公司 GPT (gpt-company)"
      );
    });
    expect(screen.getByRole("combobox", { name: "模型配置" })).toHaveDisplayValue(
      "公司 GPT (gpt-company)"
    );
    expect(screen.getByRole("status", { name: "模型配置状态" })).toHaveTextContent(
      "已启用：公司 GPT (gpt-company)"
    );
    expect(screen.getByRole("button", { name: "删除配置" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除配置" }));
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "模型配置" })).not.toHaveDisplayValue(
        "公司 GPT (gpt-company)"
      );
    });
  });

  it("saves desktop voice settings from the settings sheet", async () => {
    render(<App />);
    await screen.findByRole("region", { name: "voice-code 任务" });

    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    fireEvent.change(screen.getByRole("combobox", { name: "语音后端" }), {
      target: { value: "stepfun" }
    });
    fireEvent.change(screen.getByLabelText("StepFun Key"), {
      target: { value: "stepfun-secret" }
    });
    fireEvent.change(screen.getByRole("textbox", { name: "StepFun 音色" }), {
      target: { value: "longxiaochun" }
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "启用语音播报" }));
    fireEvent.click(screen.getByRole("button", { name: "保存语音配置" }));

    expect(await screen.findByRole("status", { name: "语音配置状态" })).toHaveTextContent(
      "StepFun 语音已保存并启用。"
    );
  });

  it("opens desktop diagnostics from the sidebar", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, {
      clipboard: { writeText }
    });
    vi.mocked(openDiagnosticsPath).mockResolvedValue(true);
    vi.mocked(readDesktopDiagnostics).mockResolvedValue({
      runtimeMode: "app-owned",
      runtimeExecutable: "/Applications/语码.app/Contents/Resources/runtime/voice-code-agent",
      uvPath: null,
      workspaceRoot: "/Users/example/project",
      appLogDir: "/Users/example/Library/Logs/com.voicecode.desktop",
      textBridgeLog: "/Users/example/Library/Logs/com.voicecode.desktop/desktop-bridge.log",
      voiceBridgeLog:
        "/Users/example/Library/Logs/com.voicecode.desktop/desktop-voice-bridge.log"
    });

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "诊断信息" }));

    expect(await screen.findByRole("dialog", { name: "诊断信息" })).toBeInTheDocument();
    expect(await screen.findByText("app-owned")).toBeInTheDocument();
    expect(screen.getByText(/voice-code-agent/)).toBeInTheDocument();
    expect(screen.getByText(/desktop-bridge.log/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "复制全部诊断" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(expect.stringContaining("运行模式: app-owned"));
    });

    fireEvent.click(screen.getByRole("button", { name: "打开日志目录" }));
    await waitFor(() => {
      expect(openDiagnosticsPath).toHaveBeenCalledWith(
        "/Users/example/Library/Logs/com.voicecode.desktop"
      );
    });
  });

  it("submits a prompt through the mock bridge event stream", async () => {
    render(<App />);

    fireEvent.change(screen.getByRole("textbox", { name: "输入任务" }), {
      target: { value: "查看项目结构" }
    });
    fireEvent.click(screen.getByRole("button", { name: "发送任务" }));

    expect(await screen.findByRole("button", { name: "查看项目结构" })).toBeInTheDocument();
    await Promise.resolve();
    expect(await screen.findByText("我先看项目结构，然后确认桌面入口。")).toBeInTheDocument();
    expect(await screen.findByText('const desktop = "ready";')).toBeInTheDocument();
    expect(await screen.findByRole("cell", { name: "Markdown" })).toBeInTheDocument();
    expect(await screen.findByText("grep")).toBeInTheDocument();
    expect(screen.queryByText("准备开始一个本地任务")).not.toBeInTheDocument();
  });

  it("submits the composer with Enter", async () => {
    render(<App />);

    const input = screen.getByRole("textbox", { name: "输入任务" });
    fireEvent.change(input, {
      target: { value: "按 Enter 直接发送" }
    });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", charCode: 13 });

    expect(await screen.findByRole("button", { name: "按 Enter 直接发送" })).toBeInTheDocument();
    expect(screen.getByLabelText("用户消息")).toHaveClass("message-row-user");
    expect(screen.getByLabelText("模型消息")).toHaveClass("message-row-assistant");
  });

  it("starts a blank task from the sidebar after a submitted task", async () => {
    vi.mocked(selectWorkspaceFolder).mockResolvedValue({
      label: "voice-code",
      path: "/Users/example/workspace/voice-code"
    });
    render(<App />);

    fireEvent.change(screen.getByRole("textbox", { name: "输入任务" }), {
      target: { value: "先做真实任务列表" }
    });
    fireEvent.click(screen.getByRole("button", { name: "发送任务" }));
    expect(await screen.findByRole("button", { name: "先做真实任务列表" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "新建对话" }));

    expect(await screen.findByText("准备开始一个本地任务")).toBeInTheDocument();
    expect(screen.queryByText("我先看项目结构，然后确认桌面入口。")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "先做真实任务列表" }));
    expect(await screen.findByText("这是一段已恢复的历史回复。")).toBeInTheDocument();
  });
});
