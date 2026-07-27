import { Clock3, ExternalLink, GitBranch, RotateCcw, ShieldCheck } from "lucide-react";

type SandboxHeaderProps = {
  workspaceLabel: string;
  expiresIn: string;
  permissionMode: string;
  changedCount: number;
  isBusy: boolean;
  repositoryUrl: string;
  onPermissionModeChange: (value: string) => void;
  onReset: () => void;
};

export function SandboxHeader({
  workspaceLabel,
  expiresIn,
  permissionMode,
  changedCount,
  isBusy,
  repositoryUrl,
  onPermissionModeChange,
  onReset
}: SandboxHeaderProps) {
  return (
    <header className="sandbox-header">
      <div className="sandbox-title">
        <p>语码 Web Demo</p>
        <h1>{workspaceLabel}</h1>
      </div>
      <div className="sandbox-meta">
        <span>
          <Clock3 size={16} strokeWidth={1.8} aria-hidden="true" />
          {expiresIn}
        </span>
        <label>
          <ShieldCheck size={16} strokeWidth={1.8} aria-hidden="true" />
          <select
            aria-label="权限模式"
            value={permissionMode}
            disabled={isBusy}
            onChange={(event) => onPermissionModeChange(event.target.value)}
          >
            <option value="default">按需询问</option>
            <option value="dontAsk">只读模式</option>
          </select>
        </label>
        <span>{changedCount} 个改动文件</span>
        <a
          className="repository-link repository-link-header"
          href={repositoryUrl}
          target="_blank"
          rel="noreferrer"
        >
          <GitBranch size={16} strokeWidth={1.8} aria-hidden="true" />
          GitHub
          <ExternalLink size={13} strokeWidth={1.8} aria-hidden="true" />
        </a>
        <button type="button" className="reset-button" disabled={isBusy} onClick={onReset}>
          <RotateCcw size={16} strokeWidth={1.8} aria-hidden="true" />
          重置
        </button>
      </div>
    </header>
  );
}
