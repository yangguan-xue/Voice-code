import { ArrowUp, GitBranch, HardDrive, Mic, Paperclip, ShieldCheck, Square } from "lucide-react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import type { WorkspaceSummary } from "../../app/fixtures";
import type { ModelProfileSummary } from "../../lib/bridge/protocol";
import { IconButton } from "../../components/IconButton";

type ComposerProps = {
  workspace: WorkspaceSummary;
  permissionModes: string[];
  branches: string[];
  isGitDirty: boolean;
  modelProfiles: ModelProfileSummary[];
  value: string;
  isBusy: boolean;
  onChange: (value: string) => void;
  onWorkspaceSelect: () => void;
  onBranchChange: (branch: string) => void;
  onModelProfileChange: (profile: string) => void;
  onPermissionModeChange: (value: string) => void;
  onSubmit: () => void;
  onVoiceOpen: () => void;
  onInterrupt: () => void;
};

export function Composer({
  workspace,
  permissionModes,
  branches,
  isGitDirty,
  modelProfiles,
  value,
  isBusy,
  onChange,
  onWorkspaceSelect,
  onBranchChange,
  onModelProfileChange,
  onPermissionModeChange,
  onSubmit,
  onVoiceOpen,
  onInterrupt
}: ComposerProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isBusy) {
      onInterrupt();
      return;
    }
    onSubmit();
  }

  function handlePromptKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    const nativeEvent = event.nativeEvent as KeyboardEvent & { isComposing?: boolean };
    if (event.key !== "Enter" || event.shiftKey || nativeEvent.isComposing) {
      return;
    }

    event.preventDefault();
    if (isBusy) {
      onInterrupt();
      return;
    }
    onSubmit();
  }

  return (
    <form className="composer" aria-label="新任务输入" onSubmit={handleSubmit}>
      <div className="composer-context">
        <button
          className="composer-context-button"
          type="button"
          disabled={isBusy}
          onClick={onWorkspaceSelect}
        >
          <HardDrive size={16} strokeWidth={1.8} />
          {workspace.label}
        </button>
        <span>{workspace.locality}</span>
        <label
          className="composer-branch"
          title={isGitDirty ? "当前工作区有未提交更改，提交或暂存后可切换分支" : "切换 Git 分支"}
        >
          <GitBranch size={16} strokeWidth={1.8} />
          <select
            aria-label="Git 分支"
            value={workspace.branch}
            disabled={isBusy || isGitDirty || branches.length === 0}
            onChange={(event) => onBranchChange(event.target.value)}
          >
            {branches.length === 0 ? <option value="">无 Git 分支</option> : null}
            {branches.map((branch) => (
              <option value={branch} key={branch}>{branch}</option>
            ))}
          </select>
        </label>
      </div>

      <textarea
        className="prompt-input"
        aria-label="输入任务"
        value={value}
        disabled={isBusy}
        placeholder="输入要交给本地 agent 的任务..."
        rows={3}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handlePromptKeyDown}
      />

      <div className="composer-actions">
        <div className="composer-actions-left">
          <IconButton label="添加上下文">
            <Paperclip size={18} strokeWidth={1.8} />
          </IconButton>
          <label className="permission-mode">
            <ShieldCheck size={16} strokeWidth={1.8} />
            <select
              className="permission-mode-select"
              aria-label="权限模式"
              value={workspace.permissionMode}
              disabled={isBusy}
              onChange={(event) => onPermissionModeChange(event.target.value)}
            >
              {permissionModes.map((mode) => (
                <option value={mode} key={mode}>
                  {mode}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="composer-actions-right">
          <select
            className="model-button"
            aria-label="模型配置"
            value={workspace.model}
            disabled={isBusy || modelProfiles.length === 0}
            onChange={(event) => onModelProfileChange(event.target.value)}
          >
            {modelProfiles.length === 0 ? <option value="">未发现模型配置</option> : null}
            {modelProfiles.map((profile) => (
              <option value={profile.id} key={profile.id}>
                {profile.label} ({profile.modelName})
              </option>
            ))}
          </select>
          <IconButton label="语音输入" onClick={onVoiceOpen} disabled={isBusy}>
            <Mic size={18} strokeWidth={1.8} />
          </IconButton>
          <IconButton
            label={isBusy ? "中断任务" : "发送任务"}
            tone="send"
            type="submit"
          >
            {isBusy ? (
              <Square size={14} fill="currentColor" strokeWidth={2} />
            ) : (
              <ArrowUp size={20} strokeWidth={2.1} />
            )}
          </IconButton>
        </div>
      </div>
    </form>
  );
}
