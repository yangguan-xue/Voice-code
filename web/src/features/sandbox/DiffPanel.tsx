import { Download, FileCode2 } from "lucide-react";
import type { ChangedFile, SandboxDiffPayload } from "../../lib/bridge/protocol";

type DiffPanelProps = {
  diff: SandboxDiffPayload;
  onDownloadSource: () => void;
};

export function DiffPanel({ diff, onDownloadSource }: DiffPanelProps) {
  const changedCount = diff.changedFiles.length;
  const canDownloadSource = changedCount === 1;
  return (
    <aside className="diff-panel" aria-label="沙盒变更">
      <div className="panel-heading">
        <div>
          <p>Diff</p>
          <h2>{changedCount ? `${changedCount} 个文件` : "暂无变更"}</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="下载源文件"
          disabled={!canDownloadSource}
          onClick={onDownloadSource}
        >
          <Download size={17} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>

      <div className="changed-file-list">
        {changedCount ? (
          diff.changedFiles.map((file) => <ChangedFileRow key={file.path} file={file} />)
        ) : (
          <p className="panel-empty">完成一轮编辑后，这里会出现改动摘要。</p>
        )}
      </div>

      {diff.patch ? (
        <pre className="patch-preview" aria-label="patch 预览">
          {diff.patch}
        </pre>
      ) : null}
    </aside>
  );
}

function ChangedFileRow({ file }: { file: ChangedFile }) {
  return (
    <div className="changed-file-row">
      <FileCode2 size={16} strokeWidth={1.8} aria-hidden="true" />
      <span>{file.path}</span>
      <strong>{file.status}</strong>
      <em>
        +{file.additions} / -{file.deletions}
      </em>
    </div>
  );
}
