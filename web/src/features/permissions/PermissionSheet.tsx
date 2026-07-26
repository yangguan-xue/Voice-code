import { ShieldAlert } from "lucide-react";
import type {
  PermissionRememberScope,
  PermissionResolveBehavior
} from "../../lib/bridge/protocol";

type PermissionSheetProps = {
  toolName: string;
  reason: string;
  filePreview: string;
  commandPreview: string;
  onResolve: (behavior: PermissionResolveBehavior, rememberScope: PermissionRememberScope) => void;
};

export function PermissionSheet({
  toolName,
  reason,
  filePreview,
  commandPreview,
  onResolve
}: PermissionSheetProps) {
  return (
    <div className="permission-backdrop" role="presentation">
      <section className="permission-sheet" role="dialog" aria-modal="true" aria-label="权限请求">
        <div className="permission-title">
          <ShieldAlert size={19} strokeWidth={1.8} aria-hidden="true" />
          <span>{toolName}</span>
        </div>
        <p>{reason || "agent 请求执行一个需要确认的动作。"}</p>
        {filePreview ? <code>{filePreview}</code> : null}
        {commandPreview ? <code>{commandPreview}</code> : null}
        <div className="permission-actions">
          <button type="button" onClick={() => onResolve("deny", "")}>
            拒绝
          </button>
          <button type="button" onClick={() => onResolve("allow", "")}>
            允许一次
          </button>
          <button type="button" onClick={() => onResolve("allow", "session")}>
            本会话允许
          </button>
        </div>
      </section>
    </div>
  );
}
