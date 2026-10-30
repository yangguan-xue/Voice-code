import { ExternalLink, GitBranch, LockKeyhole, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Composer, type ComposerAttachment } from "../features/composer/Composer";
import { ConversationCanvas } from "../features/conversation/ConversationCanvas";
import {
  conversationReducer,
  createInitialConversationState
} from "../features/conversation/conversation-reducer";
import { conversationFromHistory } from "../features/conversation/history-mapper";
import { ConversationSidebar } from "../features/conversations/ConversationSidebar";
import { PermissionSheet } from "../features/permissions/PermissionSheet";
import { DiffPanel } from "../features/sandbox/DiffPanel";
import { SandboxHeader } from "../features/sandbox/SandboxHeader";
import {
  clearTenantToken,
  loadTenantToken,
  saveTenantToken
} from "../lib/auth-storage";
import {
  readChangedFiles,
  readNumber,
  readString,
  type ConversationSummary,
  type PermissionRememberScope,
  type PermissionResolveBehavior,
  type SandboxDiffPayload,
  type TenantSessionPayload,
  type WebDemoClientLike,
  type WebDemoEvent
} from "../lib/bridge/protocol";
import { createWebDemoClient } from "../lib/bridge/web-demo-client";

type DemoSessionStatus =
  | "access"
  | "restoring"
  | "switching"
  | "ready"
  | "running"
  | "waiting_permission"
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
  const [token, setToken] = useState("");
  const [session, setSession] = useState<TenantSessionPayload | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState(createInitialConversationState);
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [permissionMode, setPermissionMode] = useState("default");
  const [pendingPermission, setPendingPermission] = useState<PendingPermissionView | null>(null);
  const [diff, setDiff] = useState<SandboxDiffPayload>(EMPTY_DIFF);
  const [errorText, setErrorText] = useState("");
  const [noticeText, setNoticeText] = useState("");

  // 事件回调里读最新 token/session，避免 client 因闭包过期而重建连接
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const noticeTimerRef = useRef<number | null>(null);

  const showNotice = useCallback((text: string) => {
    setNoticeText(text);
    if (noticeTimerRef.current !== null) {
      window.clearTimeout(noticeTimerRef.current);
    }
    noticeTimerRef.current = window.setTimeout(() => setNoticeText(""), 4000);
  }, []);

  const refreshConversations = useCallback(
    async (tk: string) => {
      try {
        const list = await client.listConversations(tk);
        setConversations(list.conversations);
      } catch {
        // 列表刷新失败不打断主流程
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const handleEvent = useCallback(
    (event: WebDemoEvent) => {
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
      if (event.event === "session.updated") {
        // 首条消息落标题后服务端推送：更新头部与侧边栏
        setSession((current) =>
          current
            ? { ...current, title: readString(event.payload, "title") || current.title }
            : current
        );
        void refreshConversations(tokenRef.current);
        return;
      }
      if (event.event === "runtime.recycled") {
        showNotice("已从上次会话恢复上下文，可以继续聊。");
        return;
      }
      setConversation((current) => conversationReducer(current, event));
    },
    [refreshConversations, showNotice]
  );

  const client = useMemo(() => clientFactory(handleEvent), [clientFactory, handleEvent]);

  useEffect(() => {
    return () => client.close();
  }, [client]);

  async function enterSession(tk: string) {
    const attached = await client.attachSession(tk);
    setSession(attached);
    setPermissionMode(attached.permissionMode || "default");
    setDiff(EMPTY_DIFF);
    setConversation(createInitialConversationState());
    await refreshConversations(tk);
    try {
      const turns = await client.getConversationMessages(tk, attached.conversationId);
      setConversation(conversationFromHistory(turns, attached.conversationId));
    } catch {
      // 历史拉取失败不阻塞使用，画布从空开始
    }
    setStatus("ready");
  }

  // 启动时恢复登录态：有本地 token 就直接 attach，免重新登录
  useEffect(() => {
    const saved = loadTenantToken();
    if (!saved) {
      return;
    }
    setToken(saved);
    setStatus("restoring");
    setErrorText("");
    enterSession(saved).catch((error: unknown) => {
      clearTenantToken();
      setToken("");
      setSession(null);
      setStatus("access");
      setErrorText(errorMessage(error));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startDemo() {
    const code = accessCode.trim();
    if (!code) {
      setErrorText("请输入访问码。");
      return;
    }
    setStatus("restoring");
    setErrorText("");
    try {
      const result = await client.login(code);
      saveTenantToken(result.tenantToken);
      setToken(result.tenantToken);
      await client.connect();
      await enterSession(result.tenantToken);
    } catch (error: unknown) {
      setStatus("access");
      setErrorText(errorMessage(error));
    }
  }

  async function selectConversation(conversationId: string) {
    if (!token || status === "running" || status === "switching" || status === "waiting_permission") {
      return;
    }
    if (session?.conversationId === conversationId) {
      return;
    }
    setStatus("switching");
    setErrorText("");
    try {
      await client.activateConversation(token, conversationId);
      // 切换后重新 attach：把事件流绑定到新会话对象上
      const attached = await client.attachSession(token);
      const [turns] = await Promise.all([
        client.getConversationMessages(token, conversationId),
        refreshConversations(token)
      ]);
      setSession(attached);
      setConversation(conversationFromHistory(turns, conversationId));
      setDiff(EMPTY_DIFF);
      setStatus("ready");
    } catch (error: unknown) {
      setStatus("ready");
      setErrorText(errorMessage(error));
    }
  }

  async function startNewConversation() {
    if (!token || status === "running" || status === "switching" || status === "waiting_permission") {
      return;
    }
    setStatus("switching");
    setErrorText("");
    try {
      await client.newConversation(token);
      const attached = await client.attachSession(token);
      setSession(attached);
      setConversation(createInitialConversationState());
      setDiff(EMPTY_DIFF);
      await refreshConversations(token);
      setStatus("ready");
    } catch (error: unknown) {
      setStatus("ready");
      setErrorText(errorMessage(error));
    }
  }

  async function logout() {
    const tk = token;
    clearTenantToken();
    setToken("");
    setSession(null);
    setConversations([]);
    setConversation(createInitialConversationState());
    setDiff(EMPTY_DIFF);
    setErrorText("");
    setAccessCode("");
    setStatus("access");
    if (tk) {
      try {
        await client.logout(tk);
      } catch {
        // 本地已清除，服务端撤销失败不影响退出
      }
    }
  }

  function addAttachments(files: File[]) {
    const images = files.filter((file) => file.type.startsWith("image/"));
    if (images.length !== files.length) {
      setErrorText("目前只支持图片附件。");
    }
    setAttachments((current) => [
      ...current,
      ...images.map((file) => ({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        file
      }))
    ]);
  }

  function removeAttachment(id: string) {
    setAttachments((current) => current.filter((item) => item.id !== id));
  }

  async function submitPrompt() {
    const hasAttachments = attachments.length > 0;
    const text = prompt.trim();
    if ((!text && !hasAttachments) || !session || !token || status === "running" || status === "switching" || status === "waiting_permission") {
      return;
    }
    setPrompt("");
    setErrorText("");
    setStatus("running");
    try {
      let finalText = text;
      const uploadedPaths: string[] = [];
      if (hasAttachments) {
        for (const attachment of attachments) {
          const safeName = attachment.file.name.replace(/[^\w.-]+/g, "_");
          const uploaded = await client.uploadFile(
            token,
            `uploads/${attachment.id}-${safeName}`,
            attachment.file
          );
          uploadedPaths.push(`./${uploaded.path}`);
        }
        setAttachments([]);
        const note = `我上传了这些图片（沙盒相对路径）：${uploadedPaths.join("、")}`;
        finalText = text ? `${text}\n\n${note}` : `${note}，请查看。`;
      }
      await client.startTurn(finalText, {
        sessionToken: token,
        permissionMode,
        images: hasAttachments
          ? uploadedPaths.map((path) => path.replace(/^\.\//, ""))
          : undefined
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
    if (!token || !pendingPermission) {
      return;
    }
    const requestId = pendingPermission.requestId;
    setPendingPermission(null);
    setStatus("running");
    try {
      const resolved = await client.resolvePermission(token, {
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
    if (!token || status === "running" || status === "switching" || status === "waiting_permission") {
      return;
    }
    if (!window.confirm("重置会清空当前沙盒改动。")) {
      return;
    }
    setErrorText("");
    try {
      await client.resetSandbox(token);
      setDiff(EMPTY_DIFF);
    } catch (error: unknown) {
      setErrorText(errorMessage(error));
    }
  }

  async function downloadSourceFile() {
    if (!token || diff.changedFiles.length !== 1) {
      return;
    }
    const changedFile = diff.changedFiles[0];
    const sourceFile = await client.getSandboxFile(token, changedFile.path);
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

  function downloadPatch() {
    if (!diff.patchAvailable || !diff.patch?.trim()) {
      return;
    }
    const blob = new Blob([diff.patch], { type: "text/x-patch;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "voice-code-demo.patch";
    link.style.display = "none";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function downloadDiffArtifact() {
    if (diff.changedFiles.length === 1) {
      void downloadSourceFile();
      return;
    }
    downloadPatch();
  }

  if (!session || status === "access" || status === "restoring") {
    return (
      <div className="access-screen">
        <section className="access-panel" aria-labelledby="access-title">
          <div className="access-mark" aria-hidden="true">
            <LockKeyhole size={26} strokeWidth={1.8} />
          </div>
          <div className="access-copy">
            <p>语码 Web Demo</p>
            <h1 id="access-title">访问码登录</h1>
            <span>登录后历史会话会自动保留，下次继续。</span>
          </div>
          <label className="access-code">
            <span>访问码</span>
            <input
              value={accessCode}
              disabled={status === "restoring"}
              autoComplete="off"
              onChange={(event) => setAccessCode(event.target.value)}
            />
          </label>
          {errorText ? <p className="form-error">{errorText}</p> : null}
          <button type="button" className="primary-button" onClick={startDemo}>
            <ShieldCheck size={18} strokeWidth={1.8} aria-hidden="true" />
            {status === "restoring" ? "登录中" : "登录"}
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

  const isBusy = status === "running" || status === "switching" || status === "waiting_permission";

  return (
    <div className="web-demo-shell">
      <SandboxHeader
        workspaceLabel={session.workspaceLabel}
        tenantLabel={session.tenantLabel}
        permissionMode={permissionMode}
        changedCount={diff.changedFiles.length}
        isBusy={isBusy}
        repositoryUrl={REPOSITORY_URL}
        onPermissionModeChange={setPermissionMode}
        onReset={resetSandbox}
        onLogout={logout}
      />
      {noticeText ? (
        <div className="bridge-notice" role="status">
          {noticeText}
        </div>
      ) : null}
      {errorText ? (
        <div className="bridge-error" role="status">
          {errorText}
        </div>
      ) : null}
      <div className="app-body">
        <ConversationSidebar
          conversations={conversations}
          tenantLabel={session.tenantLabel}
          isBusy={isBusy}
          onSelect={selectConversation}
          onNew={startNewConversation}
        />
        <section className="workspace-grid">
          <div className="conversation-workbench">
            <ConversationCanvas
              conversation={conversation}
              workspaceLabel={session.workspaceLabel}
              client={client}
              sessionToken={token}
            />
            <Composer
              value={prompt}
              isBusy={isBusy}
              disabled={isBusy}
              attachments={attachments}
              onChange={setPrompt}
              onSubmit={submitPrompt}
              onAttach={addAttachments}
              onRemoveAttachment={removeAttachment}
            />
          </div>
          <DiffPanel diff={diff} onDownload={downloadDiffArtifact} />
        </section>
      </div>

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
