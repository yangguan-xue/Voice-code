import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell, type PendingPermissionView } from "./AppShell";
import { demoSessions, demoWorkspace } from "./fixtures";
import {
  conversationReducer,
  createInitialConversationState
} from "../features/conversation/conversation-reducer";
import type { ConversationState } from "../features/conversation/types";
import {
  broadcastVoicePermissionResolution,
  subscribeToVoicePermissionRequests
} from "../features/voice/voice-broadcast";
import {
  buildVoiceContextMessages,
  writeVoiceLaunchContext,
  type VoiceLaunchContext,
  type VoiceLaunchMode
} from "../features/voice/voice-launch-context";
import {
  broadcastVoiceContextSnapshot,
  broadcastVoiceInterruptRequest,
  subscribeToVoiceExecutionState
} from "../features/voice/voice-context-sync";
import { createDesktopBridgeClient } from "../lib/bridge/client-factory";
import {
  readString,
  type BridgeEvent,
  type ModelConfigInput,
  type ModelProfileSummary,
  type PermissionRememberScope,
  type PermissionResolveBehavior,
  type VoiceSettings,
  type VoiceSettingsInput
} from "../lib/bridge/protocol";
import { selectWorkspaceFolder } from "../lib/desktop/workspace-picker";
import { openVoiceWindow } from "../lib/desktop/voice-window";
import {
  openDiagnosticsPath,
  readDesktopDiagnostics,
  type DesktopDiagnostics
} from "../lib/desktop/diagnostics";

export function App() {
  const [prompt, setPrompt] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [bridgeError, setBridgeError] = useState("");
  const [workspace, setWorkspace] = useState(demoWorkspace);
  const [activeSessionId, setActiveSessionId] = useState("draft");
  const [sessionGroups, setSessionGroups] = useState(demoSessions);
  const [branches, setBranches] = useState<string[]>([demoWorkspace.branch]);
  const [isGitDirty, setIsGitDirty] = useState(false);
  const [modelProfiles, setModelProfiles] = useState<ModelProfileSummary[]>([]);
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings>({
    provider: "custom",
    sttUrl: "http://localhost:8765",
    ttsUrl: "http://localhost:8775",
    stepfunVoice: "cixingnansheng",
    stepfunKey: "",
    hasStepfunKey: false,
    ttsEnabled: false
  });
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isDiagnosticsOpen, setIsDiagnosticsOpen] = useState(false);
  const [isDiagnosticsLoading, setIsDiagnosticsLoading] = useState(false);
  const [diagnostics, setDiagnostics] = useState<DesktopDiagnostics | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState("");
  const [conversationsBySession, setConversationsBySession] = useState<
    Record<string, ConversationState>
  >({
    draft: createInitialConversationState()
  });
  const [pendingPermission, setPendingPermission] = useState<PendingPermissionView | null>(null);
  const [voicePickerOpen, setVoicePickerOpen] = useState(false);
  const [voiceSharedBusy, setVoiceSharedBusy] = useState(false);
  const activeSessionRef = useRef(activeSessionId);
  const runningSessionRef = useRef<string | null>(null);
  const voiceContextVersionRef = useRef(0);
  const voiceContextSignatureRef = useRef("");

  useEffect(() => {
    activeSessionRef.current = activeSessionId;
  }, [activeSessionId]);

  const handleBridgeEvent = useCallback((event: BridgeEvent) => {
    if (event.event === "permission.request") {
      setPendingPermission({
        requestId: readString(event.payload, "requestId"),
        toolName: readString(event.payload, "toolName"),
        reason: readString(event.payload, "reason"),
        source: "main"
      });
      return;
    }

    const sessionId = event.sessionId || runningSessionRef.current || activeSessionRef.current;
    if (event.event === "agent.turn.started" && sessionId !== activeSessionRef.current) {
      const previousSessionId = activeSessionRef.current;
      activeSessionRef.current = sessionId;
      setActiveSessionId(sessionId);
      setSessionGroups((current) =>
        current.map((group) => ({
          ...group,
          sessions: group.sessions.map((session) =>
            session.id === previousSessionId ? { ...session, id: sessionId } : session
          )
        }))
      );
    }
    setConversationsBySession((current) => ({
      ...current,
      [sessionId]: conversationReducer(
        current[sessionId] ?? createInitialConversationState(),
        event
      )
    }));
  }, []);

  const bridgeClient = useMemo(
    () => createDesktopBridgeClient(handleBridgeEvent),
    [handleBridgeEvent]
  );

  useEffect(() => {
    return subscribeToVoicePermissionRequests((request) => {
      setPendingPermission({
        requestId: request.requestId,
        toolName: request.toolName,
        reason: request.reason,
        source: "voice"
      });
    });
  }, []);

  useEffect(() => {
    return subscribeToVoiceExecutionState((state) => {
      setVoiceSharedBusy(
        state.isBusy &&
          state.mode === "sharedSession" &&
          state.sourceSessionId === activeSessionRef.current
      );
    });
  }, []);

  useEffect(() => {
    let isMounted = true;
    void bridgeClient
      .bootstrap()
      .then(async (bootstrap) => {
        const savedVoiceSettings = await bridgeClient.loadVoiceSettings();
        if (isMounted) {
          if (bootstrap.sessionGroups.length > 0) {
            setSessionGroups((current) => mergeBootstrapSessionGroups(current, bootstrap.sessionGroups));
          }
          setBranches(bootstrap.branches);
          setIsGitDirty(bootstrap.isDirty);
          setModelProfiles(bootstrap.modelProfiles);
          if (bootstrap.sessionId && activeSessionRef.current === "draft") {
            setActiveSessionId(bootstrap.sessionId);
            activeSessionRef.current = bootstrap.sessionId;
            setConversationsBySession((current) => {
              const draft = current.draft ?? createInitialConversationState();
              const remaining = { ...current };
              delete remaining.draft;
              return { ...remaining, [bootstrap.sessionId]: draft };
            });
          }
          setWorkspace((current) => ({
            ...current,
            path: bootstrap.workspacePath || current.path,
            label: bootstrap.workspacePath ? folderNameFromPath(bootstrap.workspacePath) : current.label,
            branch: bootstrap.branch || current.branch,
            model: bootstrap.activeProfile || current.model
          }));
          setVoiceSettings(savedVoiceSettings);
        }
      })
      .catch((error: unknown) => {
        if (isMounted) {
          setPendingPermission(null);
          setBridgeError(errorMessage(error));
        }
        console.error(error);
      });
    return () => {
      isMounted = false;
      bridgeClient.close();
    };
  }, [bridgeClient]);

  useEffect(() => {
    const snapshot = buildCurrentVoiceLaunchContext("sharedSession");
    const signature = JSON.stringify({
      sessionId: snapshot.sourceSessionId,
      workspacePath: snapshot.workspacePath,
      messages: snapshot.contextMessages
    });
    if (signature === voiceContextSignatureRef.current) {
      return;
    }
    voiceContextSignatureRef.current = signature;
    voiceContextVersionRef.current += 1;
    broadcastVoiceContextSnapshot({
      ...snapshot,
      contextVersion: voiceContextVersionRef.current
    });
  }, [activeSessionId, conversationsBySession, sessionGroups, workspace]);

  async function startNewTask() {
    if (isBusy) {
      return;
    }

    try {
      setBridgeError("");
      const selected = await selectWorkspaceFolder();
      if (!selected) {
        return;
      }

      bridgeClient.close();
      const nextWorkspace = workspaceFromSelection(workspace, selected);
      setWorkspace(nextWorkspace);
      setPendingPermission(null);
      setPrompt("");
      setSessionGroups((current) => ensureWorkspaceGroup(current, selected.path));

      const result = await bridgeClient.createSession();
      if (!result.sessionId) {
        throw new Error("Desktop bridge did not return a session id.");
      }

      const workspacePath = selected.path || result.workspacePath || workspace.path;
      setActiveSessionId(result.sessionId);
      activeSessionRef.current = result.sessionId;
      setSessionGroups((current) =>
        upsertSessionInWorkspaceGroup(current, workspacePath, {
          id: result.sessionId,
          title: "未命名任务"
        })
      );
      setConversationsBySession((current) => ({
        ...current,
        [result.sessionId]: createInitialConversationState()
      }));
    } catch (error: unknown) {
      setBridgeError(errorMessage(error));
      console.error(error);
    }
  }

  function selectSession(sessionId: string) {
    if (isBusy) {
      return;
    }
    setBridgeError("");
    void bridgeClient
      .resumeSession(sessionId)
      .then((result) => {
        setConversationsBySession((current) => ({ ...current, [sessionId]: result.conversation }));
        setActiveSessionId(sessionId);
        activeSessionRef.current = sessionId;
        setBranches(result.branches);
        setIsGitDirty(result.isDirty);
        setWorkspace((current) => ({
          ...current,
          path: result.workspacePath || current.path,
          label: result.workspacePath ? folderNameFromPath(result.workspacePath) : current.label,
          branch: result.branch || current.branch
        }));
      })
      .catch((error: unknown) => {
        setBridgeError(errorMessage(error));
        console.error(error);
      });
  }

  function archiveSession(sessionId: string) {
    if (isBusy) {
      return;
    }
    void bridgeClient
      .archiveSession(sessionId)
      .then((archived) => {
        if (!archived) {
          return;
        }
        setSessionGroups((current) => removeSessionFromGroups(current, sessionId));
        if (activeSessionRef.current === sessionId) {
          activateDraftForWorkspace(workspace);
        }
      })
      .catch((error: unknown) => {
        setBridgeError(errorMessage(error));
        console.error(error);
      });
  }

  function changePermissionMode(permissionMode: string) {
    setWorkspace((current) => ({
      ...current,
      permissionMode
    }));
  }

  async function selectWorkspace() {
    if (isBusy) {
      return;
    }

    try {
      setBridgeError("");
      const selected = await selectWorkspaceFolder();
      if (!selected) {
        return;
      }

      bridgeClient.close();
      setPendingPermission(null);
      setPrompt("");
      const nextWorkspace = workspaceFromSelection(workspace, selected);
      setWorkspace(nextWorkspace);
      setSessionGroups((current) => ensureWorkspaceGroup(current, selected.path));
      activateDraftForWorkspace(nextWorkspace);
    } catch (error: unknown) {
      setBridgeError(errorMessage(error));
      console.error(error);
    }
  }

  function submitPrompt() {
    const text = prompt.trim();
    if (!text || isBusy) {
      return;
    }
    if (voiceSharedBusy) {
      setBridgeError("语音正在引用当前会话执行，本轮结束后再发送文字任务。");
      return;
    }

    const sessionId = ensureTaskSession(text);
    runningSessionRef.current = sessionId;
    activeSessionRef.current = sessionId;
    setPrompt("");
    setBridgeError("");
    setIsBusy(true);
    void bridgeClient
      .startTurn(text, { permissionMode: permissionModeForBridge(workspace.permissionMode) })
      .catch((error: unknown) => {
        setPendingPermission(null);
        setPrompt(text);
        setBridgeError(errorMessage(error));
        console.error(error);
      })
      .finally(() => {
        runningSessionRef.current = null;
        setIsBusy(false);
      });
  }

  function changeBranch(branch: string) {
    if (isBusy || !branch) {
      return;
    }
    setBridgeError("");
    void bridgeClient
      .checkoutBranch(branch)
      .then((result) => {
        setBranches(result.branches);
        setIsGitDirty(result.isDirty);
        setWorkspace((current) => ({ ...current, branch: result.branch }));
      })
      .catch((error: unknown) => {
        setBridgeError(errorMessage(error));
        console.error(error);
      });
  }

  function changeModelProfile(profile: string) {
    if (isBusy || !profile) {
      return;
    }
    setBridgeError("");
    void bridgeClient
      .selectModelProfile(profile)
      .then((result) => {
        setWorkspace((current) => ({ ...current, model: result.profile }));
      })
      .catch((error: unknown) => {
        setBridgeError(errorMessage(error));
        console.error(error);
      });
  }

  async function saveModelConfig(input: ModelConfigInput): Promise<ModelProfileSummary> {
    const saved = await bridgeClient.saveModelConfig(input);
    setModelProfiles((current) => [
      ...current.filter((profile) => profile.id !== saved.id),
      saved
    ]);
    const result = await bridgeClient.selectModelProfile(saved.id);
    setWorkspace((current) => ({ ...current, model: result.profile }));
    return saved;
  }

  async function deleteModelConfig(id: string): Promise<boolean> {
    const deleted = await bridgeClient.deleteModelConfig(id);
    if (deleted) {
      setModelProfiles((current) => current.filter((profile) => profile.id !== id));
      if (workspace.model === id) {
        const fallback = modelProfiles.find((profile) => profile.source !== "custom");
        if (fallback) {
          changeModelProfile(fallback.id);
        }
      }
    }
    return deleted;
  }

  async function saveVoiceSettings(input: VoiceSettingsInput): Promise<VoiceSettings> {
    const saved = await bridgeClient.saveVoiceSettings(input);
    setVoiceSettings(saved);
    return saved;
  }

  function interruptPrompt() {
    if (voiceSharedBusy) {
      broadcastVoiceInterruptRequest(activeSessionRef.current);
      return;
    }
    void bridgeClient.interruptTurn().catch((error: unknown) => {
      setBridgeError(errorMessage(error));
      console.error(error);
    });
  }

  function resolvePermission(
    behavior: PermissionResolveBehavior,
    rememberScope: PermissionRememberScope
  ) {
    if (!pendingPermission) {
      return;
    }

    const { requestId } = pendingPermission;
    const source = pendingPermission.source;
    setPendingPermission(null);
    if (source === "voice") {
      broadcastVoicePermissionResolution({ requestId, behavior, rememberScope });
      return;
    }
    void bridgeClient
      .resolvePermission({ requestId, behavior, rememberScope })
      .then((resolved) => {
        if (!resolved) {
          setBridgeError("权限请求已经失效，请重新发送任务。");
        }
      })
      .catch((error: unknown) => {
        setPendingPermission(null);
        setBridgeError(errorMessage(error));
        console.error(error);
      });
  }

  function openVoiceInput() {
    if (isBusy) {
      return;
    }
    setBridgeError("");
    setVoicePickerOpen(true);
  }

  function cancelVoiceInput() {
    setVoicePickerOpen(false);
  }

  function confirmVoiceInput(mode: VoiceLaunchMode) {
    setBridgeError("");
    setVoicePickerOpen(false);
    voiceContextVersionRef.current += 1;
    const context = buildCurrentVoiceLaunchContext(mode, voiceContextVersionRef.current);
    writeVoiceLaunchContext(context);
    if (mode === "sharedSession") {
      broadcastVoiceContextSnapshot(context);
    }
    void openVoiceWindow().catch((error: unknown) => {
      setBridgeError(errorMessage(error));
      console.error(error);
    });
  }

  function openDiagnostics() {
    setIsDiagnosticsOpen(true);
    setIsDiagnosticsLoading(true);
    setDiagnosticsError("");
    void readDesktopDiagnostics()
      .then((result) => {
        setDiagnostics(result);
      })
      .catch((error: unknown) => {
        setDiagnostics(null);
        setDiagnosticsError(errorMessage(error));
        console.error(error);
      })
      .finally(() => {
        setIsDiagnosticsLoading(false);
      });
  }

  return (
    <AppShell
      workspace={workspace}
      sessions={sessionGroups}
      conversation={conversationsBySession[activeSessionId] ?? createInitialConversationState()}
      prompt={prompt}
      isBusy={isBusy}
      isVoiceSharedBusy={voiceSharedBusy}
      bridgeError={bridgeError}
      activeSessionId={activeSessionId}
      pendingPermission={pendingPermission}
      voicePickerOpen={voicePickerOpen}
      branches={branches}
      isGitDirty={isGitDirty}
      modelProfiles={modelProfiles}
      voiceSettings={voiceSettings}
      isSettingsOpen={isSettingsOpen}
      isDiagnosticsOpen={isDiagnosticsOpen}
      isDiagnosticsLoading={isDiagnosticsLoading}
      diagnostics={diagnostics}
      diagnosticsError={diagnosticsError}
      onNewTask={startNewTask}
      onBridgeErrorDismiss={() => setBridgeError("")}
      onWorkspaceSelect={selectWorkspace}
      onBranchChange={changeBranch}
      onModelProfileChange={changeModelProfile}
      onSettingsOpen={() => setIsSettingsOpen(true)}
      onSettingsClose={() => setIsSettingsOpen(false)}
      onDiagnosticsOpen={openDiagnostics}
      onDiagnosticsClose={() => setIsDiagnosticsOpen(false)}
      onDiagnosticsPathOpen={openDiagnosticsPath}
      onModelConfigSave={saveModelConfig}
      onModelConfigDelete={deleteModelConfig}
      onVoiceSettingsSave={saveVoiceSettings}
      onSessionSelect={selectSession}
      onSessionArchive={archiveSession}
      onPromptChange={setPrompt}
      onPermissionModeChange={changePermissionMode}
      onPromptSubmit={submitPrompt}
      onVoiceOpen={openVoiceInput}
      onVoiceModeSelect={confirmVoiceInput}
      onVoiceModeCancel={cancelVoiceInput}
      onPromptInterrupt={interruptPrompt}
      onPermissionResolve={resolvePermission}
    />
  );

  function ensureTaskSession(text: string): string {
    const existing = sessionGroups.some((group) =>
      group.sessions.some((session) => session.id === activeSessionId)
    );
    if (existing) {
      const title = titleFromPrompt(text);
      setSessionGroups((current) =>
        current.map((group) => ({
          ...group,
          sessions: group.sessions.map((session) =>
            session.id === activeSessionId && (session.title === "未命名任务" || session.isEmpty)
              ? { ...session, title, isEmpty: false }
              : session
          )
        }))
      );
      return activeSessionId;
    }

    const sessionId = activeSessionId;
    const title = titleFromPrompt(text);
    setActiveSessionId(sessionId);
    setSessionGroups((current) =>
      upsertSessionInWorkspaceGroup(current, workspace.path, {
        id: sessionId,
        title
      })
    );
    setConversationsBySession((current) => ({
      ...current,
      [sessionId]: current[activeSessionId] ?? createInitialConversationState()
    }));
    return sessionId;
  }

  function activateDraftForWorkspace(nextWorkspace: typeof demoWorkspace) {
    const draftSessionId = draftSessionIdForWorkspace(nextWorkspace.path);
    setActiveSessionId(draftSessionId);
    activeSessionRef.current = draftSessionId;
    setConversationsBySession((current) => ({
      ...current,
      [draftSessionId]: current[draftSessionId] ?? createInitialConversationState()
    }));
  }

  function buildCurrentVoiceLaunchContext(
    mode: VoiceLaunchMode,
    contextVersion = voiceContextVersionRef.current
  ): VoiceLaunchContext {
    const activeConversation =
      conversationsBySession[activeSessionId] ?? createInitialConversationState();
    const sourceTitle =
      sessionGroups
        .flatMap((group) => group.sessions)
        .find((session) => session.id === activeSessionId)?.title ||
      (activeSessionId === "draft" ? "当前草稿" : "当前会话");
    return {
      mode,
      workspaceLabel: workspace.label,
      workspacePath: workspace.path,
      branch: workspace.branch,
      sourceSessionId: activeSessionId,
      sourceSessionTitle: sourceTitle,
      contextVersion,
      contextMessages:
        mode === "sharedSession" || mode === "parallelWorktree"
          ? buildVoiceContextMessages(activeConversation)
          : []
    };
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    const code = "code" in error && typeof error.code === "string" ? error.code : "";
    const prefix = code && !["CONNECTION_ERROR", "CLOSED"].includes(code)
      ? "操作失败"
      : "本地 agent 连接失败";
    return `${prefix}：${error.message}`;
  }
  if (typeof error === "string" && error) {
    return `本地 agent 连接失败：${error}`;
  }
  return "本地 agent 连接失败。";
}

function titleFromPrompt(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= 22) {
    return normalized;
  }
  return `${normalized.slice(0, 21)}...`;
}

function folderNameFromPath(path: string): string {
  const normalized = path.replace(/[\\/]+$/, "");
  const driveRoot = normalized.match(/^([A-Za-z]):$/);
  if (driveRoot) {
    return driveRoot[1];
  }
  return normalized.split(/[\\/]/).pop() || normalized || "workspace";
}

function workspaceFromSelection(
  current: typeof demoWorkspace,
  selected: { label: string; path: string }
): typeof demoWorkspace {
  return {
    ...current,
    label: selected.label,
    path: selected.path
  };
}

function draftSessionIdForWorkspace(workspacePath: string): string {
  return workspacePath === demoWorkspace.path ? "draft" : `draft:${workspacePath}`;
}

function ensureWorkspaceGroup(
  groups: typeof demoSessions,
  workspacePath: string
): typeof demoSessions {
  if (groups.some((group) => group.workspacePath === workspacePath)) {
    return groups;
  }
  return [
    {
      id: workspacePath,
      folderName: folderNameFromPath(workspacePath),
      workspacePath,
      sessions: []
    },
    ...groups
  ];
}

function upsertSessionInWorkspaceGroup(
  groups: typeof demoSessions,
  workspacePath: string,
  session: { id: string; title: string }
): typeof demoSessions {
  const folderName = folderNameFromPath(workspacePath);
  const groupIndex = groups.findIndex((group) => group.workspacePath === workspacePath);
  if (groupIndex === -1) {
    return [
      {
        id: workspacePath,
        folderName,
        workspacePath,
        sessions: [session]
      },
      ...groups
    ];
  }

  return groups.map((group, index) =>
    index === groupIndex
      ? {
          ...group,
          sessions: [session, ...group.sessions.filter((item) => item.id !== session.id)]
        }
      : group
  );
}

function mergeBootstrapSessionGroups(
  currentGroups: typeof demoSessions,
  bootstrapGroups: typeof demoSessions
): typeof demoSessions {
  const bootstrapSessionIds = new Set(
    bootstrapGroups.flatMap((group) => group.sessions.map((session) => session.id))
  );
  const bootstrapWorkspacePaths = new Set(bootstrapGroups.map((group) => group.workspacePath));
  const localGroups = currentGroups.flatMap((group) => {
    const sessions = group.sessions.filter((session) => !bootstrapSessionIds.has(session.id));
    if (sessions.length > 0 || !bootstrapWorkspacePaths.has(group.workspacePath)) {
      return [{ ...group, sessions }];
    }
    return [];
  });

  return [...bootstrapGroups, ...localGroups];
}

function removeSessionFromGroups(
  groups: typeof demoSessions,
  sessionId: string
): typeof demoSessions {
  return groups
    .map((group) => ({
      ...group,
      sessions: group.sessions.filter((session) => session.id !== sessionId)
    }))
    .filter((group) => group.sessions.length > 0);
}

function permissionModeForBridge(label: string): string {
  switch (label) {
    case "完全访问":
      return "bypassPermissions";
    case "只读模式":
      return "dontAsk";
    case "按需询问":
    default:
      return "default";
  }
}
