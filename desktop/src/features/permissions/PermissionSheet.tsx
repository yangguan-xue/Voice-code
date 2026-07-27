import { Check, Clock3, ShieldAlert, ShieldCheck, X } from "lucide-react";
import type { PermissionRememberScope, PermissionResolveBehavior } from "../../lib/bridge/protocol";

type PermissionSheetProps = {
  toolName: string;
  reason: string;
  onResolve: (behavior: PermissionResolveBehavior, rememberScope: PermissionRememberScope) => void;
};

export function PermissionSheet({ toolName, reason, onResolve }: PermissionSheetProps) {
  return (
    <div className="permission-backdrop" role="presentation">
      <section
        className="permission-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="permission-title"
        aria-describedby="permission-reason"
      >
        <div className="permission-header">
          <div className="permission-icon" aria-hidden="true">
            <ShieldAlert size={21} strokeWidth={1.8} />
          </div>
          <div className="permission-heading">
            <p className="permission-kicker">权限请求</p>
            <h2 id="permission-title">允许 agent 使用工具？</h2>
          </div>
        </div>

        <div className="permission-body">
          <div className="permission-tool">
            <span>工具</span>
            <code>{toolName}</code>
          </div>
          <p id="permission-reason" className="permission-reason">
            {reason}
          </p>
        </div>

        <div className="permission-actions">
          <button
            type="button"
            className="permission-action permission-action-deny"
            onClick={() => onResolve("deny", "")}
          >
            <X size={15} strokeWidth={1.9} />
            拒绝
          </button>
          <button
            type="button"
            className="permission-action permission-action-primary"
            onClick={() => onResolve("allow", "")}
          >
            <Check size={15} strokeWidth={2} />
            允许一次
          </button>
          <button
            type="button"
            className="permission-action permission-action-secondary"
            onClick={() => onResolve("allow", "session")}
          >
            <Clock3 size={15} strokeWidth={1.8} />
            本会话始终允许
          </button>
          <button
            type="button"
            className="permission-action permission-action-secondary"
            onClick={() => onResolve("allow", "workspace")}
          >
            <ShieldCheck size={15} strokeWidth={1.8} />
            信任当前工作区
          </button>
        </div>
      </section>
    </div>
  );
}
