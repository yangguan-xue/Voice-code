import { X } from "lucide-react";
import { Composer } from "../features/composer/Composer";
import { ConversationCanvas } from "../features/conversation/ConversationCanvas";
import type { ConversationState } from "../features/conversation/types";
import { DiagnosticsDialog } from "../features/diagnostics/DiagnosticsDialog";
import { PermissionSheet } from "../features/permissions/PermissionSheet";
import { SessionSidebar } from "../features/sessions/SessionSidebar";
import { ModelSettings } from "../features/settings/ModelSettings";
import type {
  ModelProfileSummary,
  ModelConfigInput,
  PermissionRememberScope,
  PermissionResolveBehavior,
  VoiceSettings,
  VoiceSettingsInput
} from "../lib/bridge/protocol";
import type { VoiceLaunchMode } from "../features/voice/voice-launch-context";
import type { SessionGroup, WorkspaceSummary } from "./fixtures";
import type { DesktopDiagnostics } from "../lib/desktop/diagnostics";

export type PendingPermissionView = {
  requestId: string;
  toolName: string;
  reason: string;
  source: "main" | "voice";
};

type AppShellProps = {
  workspace: WorkspaceSummary;
  sessions: SessionGroup[];
  conversation: ConversationState;
  prompt: string;
  isBusy: boolean;
  isVoiceSharedBusy: boolean;
  bridgeError: string;
  activeSessionId: string;
  pendingPermission: PendingPermissionView | null;
  voicePickerOpen: boolean;
  branches: string[];
  isGitDirty: boolean;
  modelProfiles: ModelProfileSummary[];
  voiceSettings: VoiceSettings;
  isSettingsOpen: boolean;
  isDiagnosticsOpen: boolean;
  isDiagnosticsLoading: boolean;
  diagnostics: DesktopDiagnostics | null;
  diagnosticsError: string;
  onNewTask: () => void;
  onBridgeErrorDismiss: () => void;
  onWorkspaceSelect: () => void;
  onBranchChange: (branch: string) => void;
  onModelProfileChange: (profile: string) => void;
  onSettingsOpen: () => void;
  onSettingsClose: () => void;
  onDiagnosticsOpen: () => void;
  onDiagnosticsClose: () => void;
  onDiagnosticsPathOpen: (path: string) => Promise<boolean>;
  onModelConfigSave: (input: ModelConfigInput) => Promise<ModelProfileSummary>;
  onModelConfigDelete: (id: string) => Promise<boolean>;
  onVoiceSettingsSave: (input: VoiceSettingsInput) => Promise<VoiceSettings>;
  onSessionSelect: (sessionId: string) => void;
  onSessionArchive: (sessionId: string) => void;
  onPromptChange: (value: string) => void;
  onPermissionModeChange: (value: string) => void;
  onPromptSubmit: () => void;
  onVoiceOpen: () => void;
  onVoiceModeSelect: (mode: VoiceLaunchMode) => void;
  onVoiceModeCancel: () => void;
  onPromptInterrupt: () => void;
  onPermissionResolve: (
    behavior: PermissionResolveBehavior,
    rememberScope: PermissionRememberScope
  ) => void;
};

export function AppShell({
  workspace,
  sessions,
  conversation,
  prompt,
  isBusy,
  isVoiceSharedBusy,
  bridgeError,
  activeSessionId,
  pendingPermission,
  voicePickerOpen,
  branches,
  isGitDirty,
  modelProfiles,
  voiceSettings,
  isSettingsOpen,
  isDiagnosticsOpen,
  isDiagnosticsLoading,
  diagnostics,
  diagnosticsError,
  onNewTask,
  onBridgeErrorDismiss,
  onWorkspaceSelect,
  onBranchChange,
  onModelProfileChange,
  onSettingsOpen,
  onSettingsClose,
  onDiagnosticsOpen,
  onDiagnosticsClose,
  onDiagnosticsPathOpen,
  onModelConfigSave,
  onModelConfigDelete,
  onVoiceSettingsSave,
  onSessionSelect,
  onSessionArchive,
  onPromptChange,
  onPermissionModeChange,
  onPromptSubmit,
  onVoiceOpen,
  onVoiceModeSelect,
  onVoiceModeCancel,
  onPromptInterrupt,
  onPermissionResolve
}: AppShellProps) {
  return (
    <div className="app-shell">
      <div className="workspace-layout">
        <SessionSidebar
          groups={sessions}
          activeSessionId={activeSessionId}
          onNewTask={onNewTask}
          onSessionSelect={onSessionSelect}
          onSessionArchive={onSessionArchive}
          onSettingsOpen={onSettingsOpen}
          onDiagnosticsOpen={onDiagnosticsOpen}
        />
        <section className="workbench">
          {bridgeError ? (
            <div className="bridge-error" role="alert">
              <span>{bridgeError}</span>
              <button type="button" aria-label="关闭错误提示" onClick={onBridgeErrorDismiss}>
                <X size={16} strokeWidth={1.8} />
              </button>
            </div>
          ) : null}
          <ConversationCanvas workspace={workspace} conversation={conversation} />
          <Composer
            workspace={workspace}
            permissionModes={["完全访问", "按需询问", "只读模式"]}
            branches={branches}
            isGitDirty={isGitDirty}
            modelProfiles={modelProfiles}
            value={prompt}
            isBusy={isBusy || isVoiceSharedBusy}
            onChange={onPromptChange}
            onWorkspaceSelect={onWorkspaceSelect}
            onBranchChange={onBranchChange}
            onModelProfileChange={onModelProfileChange}
            onPermissionModeChange={onPermissionModeChange}
            onSubmit={onPromptSubmit}
            onVoiceOpen={onVoiceOpen}
            onInterrupt={onPromptInterrupt}
          />
        </section>
      </div>

      {pendingPermission ? (
        <PermissionSheet
          toolName={pendingPermission.toolName}
          reason={pendingPermission.reason}
          onResolve={onPermissionResolve}
        />
      ) : null}
      {voicePickerOpen ? (
        <VoiceModeDialog onSelect={onVoiceModeSelect} onCancel={onVoiceModeCancel} />
      ) : null}
      {isSettingsOpen ? (
        <ModelSettings
          configs={modelProfiles.filter((profile) => profile.source === "custom")}
          voiceSettings={voiceSettings}
          onClose={onSettingsClose}
          onSave={onModelConfigSave}
          onDelete={onModelConfigDelete}
          onVoiceSave={onVoiceSettingsSave}
        />
      ) : null}
      {isDiagnosticsOpen ? (
        <DiagnosticsDialog
          diagnostics={diagnostics}
          isLoading={isDiagnosticsLoading}
          error={diagnosticsError}
          onClose={onDiagnosticsClose}
          onOpenPath={onDiagnosticsPathOpen}
        />
      ) : null}
    </div>
  );
}

function VoiceModeDialog({
  onSelect,
  onCancel
}: {
  onSelect: (mode: VoiceLaunchMode) => void;
  onCancel: () => void;
}) {
  return (
    <div className="voice-mode-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="voice-mode-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="voice-mode-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="voice-mode-copy">
          <h2 id="voice-mode-title">打开语音助手</h2>
          <p>选择语音这次如何使用当前会话。</p>
        </div>
        <div className="voice-mode-actions">
          <button type="button" onClick={() => onSelect("sharedSession")}>
            <strong>引用当前会话</strong>
            <span>读取当前对话上下文，语音内容只留在语音窗口，执行与文字串行。</span>
          </button>
          <button type="button" onClick={() => onSelect("independentSession")}>
            <strong>独立语音会话</strong>
            <span>不读取当前聊天；同一工作区内写文件会等待锁。</span>
          </button>
          <button type="button" onClick={() => onSelect("parallelWorktree")}>
            <strong>并行语音任务</strong>
            <span>创建隔离 worktree 并行运行，完成后通过 diff 决定是否应用。</span>
          </button>
        </div>
        <button className="voice-mode-cancel" type="button" onClick={onCancel}>
          取消
        </button>
      </section>
    </div>
  );
}
