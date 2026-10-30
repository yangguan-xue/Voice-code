import { ExternalLink, GitBranch, LogOut, Menu, RotateCcw, ShieldCheck, UserRound, X } from "lucide-react";
import { useState } from "react";

type SandboxHeaderProps = {
  workspaceLabel: string;
  tenantLabel: string;
  permissionMode: string;
  changedCount: number;
  isBusy: boolean;
  repositoryUrl: string;
  onPermissionModeChange: (value: string) => void;
  onReset: () => void;
  onLogout: () => void;
};

export function SandboxHeader({
  workspaceLabel,
  tenantLabel,
  permissionMode,
  changedCount,
  isBusy,
  repositoryUrl,
  onPermissionModeChange,
  onReset,
  onLogout
}: SandboxHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  function handleReset() {
    setMenuOpen(false);
    onReset();
  }

  function handleLogout() {
    setMenuOpen(false);
    onLogout();
  }

  return (
    <>
      {menuOpen ? (
        <button
          type="button"
          className="header-menu-backdrop"
          aria-hidden="true"
          tabIndex={-1}
          onClick={() => setMenuOpen(false)}
        />
      ) : null}
      <header className="sandbox-header">
      <div className="sandbox-title">
        <p>语码 Web Demo</p>
        <h1>{workspaceLabel}</h1>
      </div>
      <button
        type="button"
        className="header-menu-button"
        aria-label={menuOpen ? "关闭菜单" : "打开菜单"}
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
      >
        {menuOpen ? (
          <X size={18} strokeWidth={1.8} aria-hidden="true" />
        ) : (
          <Menu size={18} strokeWidth={1.8} aria-hidden="true" />
        )}
      </button>
      <div className={menuOpen ? "sandbox-meta sandbox-meta-open" : "sandbox-meta"}>
        <span title="当前租户">
          <UserRound size={16} strokeWidth={1.8} aria-hidden="true" />
          {tenantLabel}
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
            <option value="fullAuto">完全执行</option>
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
        <button type="button" className="reset-button" disabled={isBusy} onClick={handleReset}>
          <RotateCcw size={16} strokeWidth={1.8} aria-hidden="true" />
          重置
        </button>
        <button type="button" className="reset-button" onClick={handleLogout}>
          <LogOut size={16} strokeWidth={1.8} aria-hidden="true" />
          退出
        </button>
      </div>
      </header>
    </>
  );
}
