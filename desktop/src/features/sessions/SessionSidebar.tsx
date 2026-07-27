import {
  Archive,
  ChevronDown,
  CircleHelp,
  Info,
  PenLine,
  Search,
  Settings
} from "lucide-react";
import type { SessionGroup } from "../../app/fixtures";

type SessionSidebarProps = {
  groups: SessionGroup[];
  activeSessionId: string;
  onNewTask: () => void;
  onSessionSelect: (sessionId: string) => void;
  onSessionArchive: (sessionId: string) => void;
  onSettingsOpen: () => void;
  onDiagnosticsOpen: () => void;
};

export function SessionSidebar({
  groups,
  activeSessionId,
  onNewTask,
  onSessionSelect,
  onSessionArchive,
  onSettingsOpen,
  onDiagnosticsOpen
}: SessionSidebarProps) {
  return (
    <aside className="session-sidebar" role="navigation" aria-label="项目和任务">
      <div className="brand-row">
        <button className="brand-button" type="button" aria-label="切换工作区">
          <span>语码</span>
          <ChevronDown size={15} strokeWidth={1.8} />
        </button>
        <button className="sidebar-search" type="button" aria-label="搜索任务">
          <Search size={18} strokeWidth={1.8} />
        </button>
      </div>

      <div className="primary-actions" aria-label="任务入口">
        <button className="new-task-button" type="button" aria-label="新建对话" onClick={onNewTask}>
          <PenLine size={17} strokeWidth={1.8} />
          <span>新建对话</span>
        </button>
      </div>

      <div className="session-groups">
        {groups.map((group) => (
          <section className="session-group" key={group.id} aria-label={`${group.folderName} 任务`}>
            <div className="section-label">
              <span>{group.folderName}</span>
            </div>
            <div className="session-list">
              {group.sessions.length > 0 ? (
                group.sessions.map((session) => (
                  <div className="session-row-shell" key={session.id}>
                    <button
                      className={`session-row ${
                        session.id === activeSessionId ? "session-row-active" : ""
                      } ${session.isEmpty ? "session-row-empty" : ""}`}
                      type="button"
                      disabled={session.isEmpty}
                      onClick={() => onSessionSelect(session.id)}
                    >
                      {session.title}
                    </button>
                    {!session.isEmpty ? (
                      <button
                        className="session-archive-button"
                        type="button"
                        aria-label={`归档 ${session.title}`}
                        title="归档"
                        onClick={() => onSessionArchive(session.id)}
                      >
                        <Archive size={14} strokeWidth={1.8} />
                      </button>
                    ) : null}
                  </div>
                ))
              ) : (
                <p className="session-empty-copy">提交任务后会出现在这里</p>
              )}
            </div>
          </section>
        ))}
      </div>

      <div className="sidebar-bottom">
        <button className="help-button" type="button" aria-label="设置" title="设置" onClick={onSettingsOpen}>
          <Settings size={18} strokeWidth={1.8} />
        </button>
        <button
          className="help-button"
          type="button"
          aria-label="诊断信息"
          title="诊断信息"
          onClick={onDiagnosticsOpen}
        >
          <Info size={18} strokeWidth={1.8} />
        </button>
        <button className="help-button" type="button" aria-label="帮助" title="帮助">
          <CircleHelp size={18} strokeWidth={1.8} />
        </button>
      </div>
    </aside>
  );
}
