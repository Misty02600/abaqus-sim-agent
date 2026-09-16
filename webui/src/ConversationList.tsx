import {
  ThreadListPrimitive,
  ThreadListItemPrimitive,
} from "@assistant-ui/react";
import { useEffect, useState } from "react";
import { Icon } from "./Icon";

type ThreadMeta = {
  label?: string;
  runId?: string;
  title?: string;
  titleVersion?: number;
  active?: boolean;
  createdAt?: string;
  messageCount?: number;
};

const relative = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });

function relativeTime(value: string | undefined, now: number) {
  if (!value) return "";
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return "";
  const delta = time - now;
  const abs = Math.abs(delta);
  if (abs < 60_000) return "刚刚";
  if (abs < 3_600_000) return relative.format(Math.round(delta / 60_000), "minute");
  if (abs < 86_400_000) return relative.format(Math.round(delta / 3_600_000), "hour");
  return relative.format(Math.round(delta / 86_400_000), "day");
}

function compactWorkspace(workspace: string) {
  const separator = workspace.includes("\\") ? "\\" : "/";
  const parts = workspace.split(/[\\/]+/).filter(Boolean);
  return parts.length <= 2 ? workspace : `…${separator}${parts.slice(-2).join(separator)}`;
}

export function ConversationList({
  busy,
  query,
  onQueryChange,
  empty,
  searching,
  onSearchChange,
  workspace,
  onRename,
}: {
  busy: boolean;
  query: string;
  onQueryChange: (query: string) => void;
  empty: boolean;
  searching: boolean;
  onSearchChange: (open: boolean) => void;
  workspace: string | null;
  onRename: (
    runId: string,
    title: string,
    titleVersion: number,
  ) => Promise<boolean>;
}) {
  const [now, setNow] = useState(Date.now());
  const [editing, setEditing] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [renamePending, setRenamePending] = useState(false);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <ThreadListPrimitive.Root className="conversation-list">
      <div className="sidebar-heading">
        <strong className="app-name">Abaqus Agent</strong>
        <ThreadListPrimitive.New
          className="icon-button"
          disabled={busy}
          aria-label="新建会话"
          title="新建会话"
        >
          <Icon name="plus" />
        </ThreadListPrimitive.New>
        <button
          className="icon-button"
          aria-label="搜索会话"
          aria-expanded={searching}
          onClick={() => {
            onSearchChange(!searching);
            if (searching) onQueryChange("");
          }}
        >
          <Icon name="search" />
        </button>
      </div>
      {workspace && (
        <div className="workspace-context" title={workspace}>
          <Icon name="folder" />
          <span>{compactWorkspace(workspace)}</span>
        </div>
      )}
      {searching && (
        <div className="session-search">
          <input
            aria-label="搜索会话名称"
            placeholder="搜索会话名称…"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            maxLength={200}
            autoFocus
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                onSearchChange(false);
                onQueryChange("");
              }
            }}
          />
        </div>
      )}
      <nav aria-label="会话列表">
        <ThreadListPrimitive.Items>
          {({ threadListItem }) => {
            const meta = (threadListItem.custom ?? {}) as ThreadMeta;
            const runId = meta.runId ?? "";
            const title = meta.title ?? "未命名会话";
            const titleVersion = meta.titleVersion ?? 0;
            const metadata = [
              meta.label,
              relativeTime(meta.createdAt, now),
              `${meta.messageCount ?? 0} 条消息`,
            ].filter(Boolean);

            if (editing === runId) {
              return (
                <ThreadListItemPrimitive.Root className="thread-list-item">
                  <form
                    className="thread-rename-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      const next = editTitle.trim();
                      if (!next || renamePending) return;
                      setRenamePending(true);
                      void onRename(runId, next, titleVersion).then((saved) => {
                        setRenamePending(false);
                        if (saved) setEditing(null);
                      });
                    }}
                  >
                    <input
                      aria-label="会话名称"
                      value={editTitle}
                      maxLength={120}
                      autoFocus
                      disabled={renamePending}
                      onChange={(event) => setEditTitle(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape") setEditing(null);
                      }}
                      onBlur={(event) => {
                        if (!event.currentTarget.form?.contains(event.relatedTarget))
                          setEditing(null);
                      }}
                    />
                    <button type="submit" disabled={!editTitle.trim() || renamePending}>
                      保存
                    </button>
                  </form>
                </ThreadListItemPrimitive.Root>
              );
            }

            return (
              <ThreadListItemPrimitive.Root className="thread-list-item">
                <div className="thread-row">
                  <ThreadListItemPrimitive.Trigger className="task" disabled={busy}>
                    <span className="thread-title-line">
                      {meta.active && (
                        <span
                          className="running-dot"
                          role="status"
                          aria-label="正在运行"
                          title="正在运行"
                        />
                      )}
                      <strong>
                        <ThreadListItemPrimitive.Title />
                      </strong>
                    </span>
                    <small
                      title={
                        meta.createdAt
                          ? `创建于 ${new Date(meta.createdAt).toLocaleString()}`
                          : undefined
                      }
                    >
                      {metadata.join(" · ")}
                    </small>
                  </ThreadListItemPrimitive.Trigger>
                  <button
                    className="thread-rename-button"
                    type="button"
                    disabled={busy}
                    aria-label={`重命名 ${title}`}
                    title="重命名会话"
                    onClick={() => {
                      setEditTitle(title.slice(0, 120));
                      setEditing(runId);
                    }}
                  >
                    ✎
                  </button>
                </div>
              </ThreadListItemPrimitive.Root>
            );
          }}
        </ThreadListPrimitive.Items>
        {empty && (
          <p className="empty">{query ? "未找到匹配会话" : "暂无会话"}</p>
        )}
      </nav>
    </ThreadListPrimitive.Root>
  );
}
