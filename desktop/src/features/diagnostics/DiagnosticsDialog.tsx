import { useState } from "react";
import { AlertCircle, CheckCircle2, ClipboardList, Copy, FolderOpen, X } from "lucide-react";
import type { DesktopDiagnostics } from "../../lib/desktop/diagnostics";

type DiagnosticsDialogProps = {
  diagnostics: DesktopDiagnostics | null;
  isLoading: boolean;
  error: string;
  onClose: () => void;
  onOpenPath: (path: string) => Promise<boolean>;
};

export function DiagnosticsDialog({
  diagnostics,
  isLoading,
  error,
  onClose,
  onOpenPath
}: DiagnosticsDialogProps) {
  const rows = diagnostics ? diagnosticsRows(diagnostics) : [];
  const [actionMessage, setActionMessage] = useState("");

  return (
    <div className="diagnostics-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="diagnostics-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="diagnostics-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="diagnostics-header">
          <div className="diagnostics-title">
            <CheckCircle2 size={18} strokeWidth={1.8} />
            <h2 id="diagnostics-title">诊断信息</h2>
          </div>
          <button
            className="settings-icon-button"
            type="button"
            aria-label="关闭诊断信息"
            onClick={onClose}
          >
            <X size={17} strokeWidth={1.8} />
          </button>
        </header>

        <div className="diagnostics-content">
          {isLoading ? (
            <div className="diagnostics-status" role="status">
              正在读取桌面诊断信息...
            </div>
          ) : null}

          {!isLoading && error ? (
            <div className="diagnostics-error" role="alert">
              <AlertCircle size={18} strokeWidth={1.8} />
              <span>{error}</span>
            </div>
          ) : null}

          {!isLoading && !error && diagnostics ? (
            <>
              <div className="diagnostics-actions">
                <button
                  type="button"
                  disabled={!diagnostics.appLogDir}
                  onClick={() =>
                    void openPath(diagnostics.appLogDir, onOpenPath, setActionMessage)
                  }
                >
                  <FolderOpen size={16} strokeWidth={1.8} />
                  <span>打开日志目录</span>
                </button>
                <button
                  type="button"
                  onClick={() =>
                    void copyText(formatDiagnostics(diagnostics), setActionMessage, "已复制全部诊断")
                  }
                >
                  <ClipboardList size={16} strokeWidth={1.8} />
                  <span>复制全部诊断</span>
                </button>
              </div>
              {actionMessage ? (
                <p className="diagnostics-action-status" role="status">
                  {actionMessage}
                </p>
              ) : null}
              <dl className="diagnostics-list">
                {rows.map((row) => (
                  <div className="diagnostics-row" key={row.label}>
                    <dt>{row.label}</dt>
                    <dd>
                      <span>{row.value}</span>
                      {row.copyable ? (
                        <button
                          type="button"
                          aria-label={`复制${row.label}`}
                          title="复制"
                          onClick={() =>
                            void copyText(row.value, setActionMessage, `已复制${row.label}`)
                          }
                        >
                          <Copy size={14} strokeWidth={1.8} />
                        </button>
                      ) : null}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          ) : null}

          {!isLoading && !error && !diagnostics ? (
            <div className="diagnostics-status" role="status">
              当前不在 Tauri 桌面环境，暂无本地运行时诊断。
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

async function openPath(
  path: string | null,
  onOpenPath: (path: string) => Promise<boolean>,
  setActionMessage: (message: string) => void
) {
  if (!path) {
    return;
  }
  try {
    const opened = await onOpenPath(path);
    setActionMessage(opened ? "已打开日志目录" : "当前环境无法打开本地路径");
  } catch (error) {
    setActionMessage(error instanceof Error ? error.message : "打开日志目录失败");
  }
}

async function copyText(
  text: string,
  setActionMessage: (message: string) => void,
  successMessage: string
) {
  if (!navigator.clipboard) {
    setActionMessage("当前环境无法访问剪贴板");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    setActionMessage(successMessage);
  } catch {
    setActionMessage("复制失败");
  }
}

function formatDiagnostics(diagnostics: DesktopDiagnostics): string {
  return diagnosticsRows(diagnostics)
    .map((row) => `${row.label}: ${row.value}`)
    .join("\n");
}

function diagnosticsRows(diagnostics: DesktopDiagnostics) {
  return [
    { label: "运行模式", value: diagnostics.runtimeMode || "unknown", copyable: false },
    { label: "运行入口", value: diagnostics.runtimeExecutable || "未找到", copyable: true },
    { label: "uv 路径", value: diagnostics.uvPath || "未使用", copyable: true },
    { label: "工作区", value: diagnostics.workspaceRoot || "未设置", copyable: true },
    { label: "日志目录", value: diagnostics.appLogDir || "未设置", copyable: true },
    { label: "文字日志", value: diagnostics.textBridgeLog || "未设置", copyable: true },
    { label: "语音日志", value: diagnostics.voiceBridgeLog || "未设置", copyable: true }
  ];
}
