import { ExternalLink, GitBranch, LockKeyhole, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Composer } from "../features/composer/Composer";
import { ConversationCanvas } from "../features/conversation/ConversationCanvas";
import {
  conversationReducer,
  createInitialConversationState
} from "../features/conversation/conversation-reducer";
import { PermissionSheet } from "../features/permissions/PermissionSheet";
import { DiffPanel } from "../features/sandbox/DiffPanel";
import { SandboxHeader } from "../features/sandbox/SandboxHeader";
import {
  readChangedFiles,
  readNumber,
  readString,
  type DemoSessionPayload,
  type PermissionRememberScope,
  type PermissionResolveBehavior,
  type SandboxDiffPayload,
  type WebDemoClientLike,
  type WebDemoEvent
} from "../lib/bridge/protocol";
import { createWebDemoClient } from "../lib/bridge/web-demo-client";

type DemoSessionStatus =
  | "access"
  | "creating"
  | "ready"
  | "running"
  | "waiting_permission"
  | "expired"
  | "failed";

type PendingPermissionView = {
  requestId: string;
  toolName: string;
  reason: string;
  filePreview: string;
  commandPreview: string;
};

type WebDemoAppProps = {
  clientFactory?: (emit: (event: WebDemoEvent) => void) => WebDemoClientLike;
};

const EMPTY_DIFF: SandboxDiffPayload = {
  changedFiles: [],
  patchAvailable: false,
  patch: "",
  diskUsageBytes: 0
};

const REPOSITORY_URL = "https://github.com/yangguan-xue/Voice-code";

export function WebDemoApp({ clientFactory = createWebDemoClient }: WebDemoAppProps) {
  const [status, setStatus] = useState<DemoSessionStatus>("access");
  const [accessCode, setAccessCode] = useState("");
  const [session, setSession] = useState<DemoSessionPayload | null>(null);
  const [conversation, setConversation] = useState(createInitialConversationState);
  const [prompt, setPrompt] = useState("");
  const [permissionMode, setPermissionMode] = useState("default");
  const [pendingPermission, setPendingPermission] = useState<PendingPermissionView | null>(null);
  const [diff, setDiff] = useState<SandboxDiffPayload>(EMPTY_DIFF);
  const [errorText, setErrorText] = useState("");
  const [now, setNow] = useState(() => Date.now());

  const handleEvent = useCallback((event: WebDemoEvent) => {
    if (event.event === "permission.request") {
      setPendingPermission({
        requestId: readString(event.payload, "requestId"),
        toolName: readString(event.payload, "toolName"),
        reason: readString(event.payload, "reason"),
        filePreview: readString(event.payload, "filePreview"),
        commandPreview: readString(event.payload, "commandPreview")
      });
      setStatus("waiting_permission");
      return;
    }
    if (event.event === "sandbox.diff.updated") {
      setDiff({
        changedFiles: readChangedFiles(event.payload),
        patchAvailable: Boolean(event.payload.patchAvailable),
        patch: readString(event.payload, "patch"),
        diskUsageBytes: readNumber(event.payload, "diskUsageBytes")
      });
      return;
    }
    if (event.event === "session.expired") {
      setStatus("expired");
      return;
    }
    setConversation((current) => conversationReducer(current, event));
  }, []);

  const client = useMemo(() => clientFactory(handleEvent), [clientFactory, handleEvent]);

  useEffect(() => {
    return () => client.close();
  }, [client]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!session || status === "expired") {
      return;
    }
    if (new Date(session.expiresAt).getTime() <= now) {
      setStatus("expired");
      client.close();
    }
  }, [client, now, session, status]);

  async function startDemo() {
    const code = accessCode.trim();
    if (!code) {
      setErrorText("请输入访问码。");
      return;
    }
    setStatus("creating");
    setErrorText("");
    try {
      await client.connect();
      const created = await client.createSession(code);
      setSession(created);
      setNow(Date.now());
      setPermissionMode(created.permissionMode || "default");
      setConversation(createInitialConversationState());
      setDiff(EMPTY_DIFF);
      setStatus("ready");
    } catch (error: unknown) {
      setStatus("access");
      setErrorText(errorMessage(error));
    }
  }

  async function submitPrompt() {
    const text = prompt.trim();
    if (!text || !session || status === "running" || status === "waiting_permission") {
      return;
    }
    setPrompt("");
    setErrorText("");
    setStatus("running");
    try {
      await client.startTurn(text, {
        sessionToken: session.sessionToken,
        permissionMode
      });
      setStatus("ready");
    } catch (error: unknown) {
      setPrompt(text);
      setStatus("failed");
      setErrorText(errorMessage(error));
    }
  }

  async function resolvePermission(
    behavior: PermissionResolveBehavior,
    rememberScope: PermissionRememberScope
  ) {
    if (!session || !pendingPermission) {
      return;
    }
    const requestId = pendingPermission.requestId;
    setPendingPermission(null);
    setStatus("running");
    try {
      const resolved = await client.resolvePermission(session.sessionToken, {
        requestId,
        behavior,
        rememberScope
      });
      if (!resolved) {
        setErrorText("权限请求已经失效，请重新发送任务。");
      }
    } catch (error: unknown) {
      setStatus("failed");
      setErrorText(errorMessage(error));
    }
  }

  async function resetSandbox() {
    if (!session || status === "running" || status === "waiting_permission") {
      return;
    }
    if (!window.confirm("重置会清空当前沙盒改动。")) {
      return;
    }
    setErrorText("");
    try {
      await client.resetSandbox(session.sessionToken);
      setDiff(EMPTY_DIFF);
    } catch (error: unknown) {
      setErrorText(errorMessage(error));
    }
  }

  async function downloadSourceFile() {
    if (!session || diff.changedFiles.length !== 1) {
      return;
    }
    const changedFile = diff.changedFiles[0];
    const sourceFile = await client.getSandboxFile(session.sessionToken, changedFile.path);
    if (!sourceFile.path) {
      return;
    }
    const blob = new Blob([sourceFile.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileNameFromPath(sourceFile.path);
    link.style.display = "none";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  if (!session || status === "access" || status === "creating") {
    return (
      <div className="access-screen">
        <section className="access-panel" aria-labelledby="access-title">
          <div className="access-mark" aria-hidden="true">
            <LockKeyhole size={26} strokeWidth={1.8} />
          </div>
          <div className="access-copy">
            <p>语码 Web Demo</p>
            <h1 id="access-title">进入临时沙盒</h1>
            <span>会话自动过期，改动只保存在本次 demo workspace。</span>
          </div>
          <label className="access-code">
            <span>访问码</span>
            <input
              value={accessCode}
              disabled={status === "creating"}
              autoComplete="off"
              onChange={(event) => setAccessCode(event.target.value)}
            />
          </label>
          {errorText ? <p className="form-error">{errorText}</p> : null}
          <button type="button" className="primary-button" onClick={startDemo}>
            <ShieldCheck size={18} strokeWidth={1.8} aria-hidden="true" />
            {status === "creating" ? "创建中" : "Start demo"}
          </button>
          <a
            className="repository-link repository-link-access"
            href={REPOSITORY_URL}
            target="_blank"
            rel="noreferrer"
          >
            <GitBranch size={17} strokeWidth={1.8} aria-hidden="true" />
            GitHub 仓库
            <ExternalLink size={14} strokeWidth={1.8} aria-hidden="true" />
          </a>
        </section>
      </div>
    );
  }

  const isBusy = status === "running" || status === "waiting_permission";
  const isDisabled = isBusy || status === "expired";

  return (
    <div className="web-demo-shell">
      <SandboxHeader
        workspaceLabel={session.workspaceLabel}
        expiresIn={formatExpiresIn(session.expiresAt, now)}
        permissionMode={permissionMode}
        changedCount={diff.changedFiles.length}
        isBusy={isBusy}
        repositoryUrl={REPOSITORY_URL}
        onPermissionModeChange={setPermissionMode}
        onReset={resetSandbox}
      />
      {errorText ? (
        <div className="bridge-error" role="status">
          {errorText}
        </div>
      ) : null}
      {status === "expired" ? (
        <div className="expired-banner" role="status">
          会话已过期，请刷新页面重新开始。
        </div>
      ) : null}
      <section className="workspace-grid">
        <div className="conversation-workbench">
          <ConversationCanvas conversation={conversation} workspaceLabel={session.workspaceLabel} />
          <Composer
            value={prompt}
            isBusy={isBusy}
            disabled={isDisabled}
            onChange={setPrompt}
            onSubmit={submitPrompt}
          />
        </div>
        <DiffPanel diff={diff} onDownloadSource={downloadSourceFile} />
      </section>

      {pendingPermission ? (
        <PermissionSheet
          toolName={pendingPermission.toolName}
          reason={pendingPermission.reason}
          filePreview={pendingPermission.filePreview}
          commandPreview={pendingPermission.commandPreview}
          onResolve={resolvePermission}
        />
      ) : null}
    </div>
  );
}

function formatExpiresIn(expiresAt: string, now: number): string {
  const remainingMs = Math.max(0, new Date(expiresAt).getTime() - now);
  const remainingMinutes = Math.ceil(remainingMs / 60_000);
  if (remainingMinutes <= 0) {
    return "已过期";
  }
  return `${remainingMinutes} 分钟`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  if (typeof error === "string" && error) {
    return error;
  }
  return "请求失败。";
}

function fileNameFromPath(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.at(-1) || "source.txt";
}
