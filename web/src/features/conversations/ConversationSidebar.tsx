import { MessageSquarePlus, MessagesSquare } from "lucide-react";
import type { ConversationSummary } from "../../lib/bridge/protocol";

type ConversationSidebarProps = {
  conversations: ConversationSummary[];
  tenantLabel: string;
  isBusy: boolean;
  onSelect: (conversationId: string) => void;
  onNew: () => void;
};

export function ConversationSidebar({
  conversations,
  tenantLabel,
  isBusy,
  onSelect,
  onNew
}: ConversationSidebarProps) {
  return (
    <aside className="conversation-sidebar">
      <div className="sidebar-head">
        <span className="sidebar-tenant" title={tenantLabel}>
          {tenantLabel}
        </span>
        <button
          type="button"
          className="sidebar-new-button"
          disabled={isBusy}
          onClick={onNew}
          aria-label="新会话"
        >
          <MessageSquarePlus size={16} strokeWidth={1.8} aria-hidden="true" />
          新会话
        </button>
      </div>
      <ul className="sidebar-list">
        {conversations.length === 0 ? (
          <li className="sidebar-empty">还没有历史会话</li>
        ) : (
          conversations.map((item) => (
            <li key={item.conversationId}>
              <button
                type="button"
                className={
                  item.isCurrent ? "sidebar-item sidebar-item-active" : "sidebar-item"
                }
                disabled={isBusy}
                onClick={() => onSelect(item.conversationId)}
              >
                <MessagesSquare size={15} strokeWidth={1.8} aria-hidden="true" />
                <span className="sidebar-item-title">{item.title || "新会话"}</span>
                <span className="sidebar-item-time">{formatSidebarTime(item.updatedAt)}</span>
              </button>
            </li>
          ))
        )}
      </ul>
    </aside>
  );
}

function formatSidebarTime(iso: string): string {
  const time = new Date(iso).getTime();
  if (!Number.isFinite(time)) {
    return "";
  }
  const diffMs = Date.now() - time;
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) {
    return "刚刚";
  }
  if (minutes < 60) {
    return `${minutes} 分钟前`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时前`;
  }
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}
