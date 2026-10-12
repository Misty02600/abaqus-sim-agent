import { useEffect, useRef, useState } from "react";
import { errorText, request, runPath } from "./api/client";
import { FolderIcon, getFileIcon } from "./FileIcons";

type WorkspaceEntry = {
  relative_path: string;
  name: string;
  kind: "file" | "directory";
  size_bytes: number | null;
};

type WorkspaceDirectory = {
  run_id: string;
  path: string;
  items: WorkspaceEntry[];
  truncated: boolean;
};

type WorkspaceSearch = {
  run_id: string;
  query: string;
  items: WorkspaceEntry[];
  truncated: boolean;
};

type DirectoryState = {
  loading: boolean;
  error: string;
  items: WorkspaceEntry[];
  truncated: boolean;
};

const EMPTY_DIRECTORY: DirectoryState = {
  loading: false,
  error: "",
  items: [],
  truncated: false,
};

export function WorkspaceExplorer({
  runId,
  filesVersion,
  onMention,
  selectedPath,
  onOpenFile,
}: {
  runId: string;
  filesVersion: string;
  onMention: (path: string) => void;
  selectedPath?: string;
  onOpenFile: (path: string, sizeBytes: number | null) => void;
}) {
  const [directories, setDirectories] = useState<Record<string, DirectoryState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<WorkspaceSearch | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const searchSequence = useRef(0);
  const directorySequence = useRef(new Map<string, number>());

  function loadDirectory(path: string, force = false) {
    if (!force && directories[path] && !directories[path].error) return;
    const sequence = (directorySequence.current.get(path) ?? 0) + 1;
    directorySequence.current.set(path, sequence);
    setDirectories((current) => ({
      ...current,
      [path]: { ...(current[path] ?? EMPTY_DIRECTORY), loading: true, error: "" },
    }));
    const suffix = path ? `?path=${encodeURIComponent(path)}` : "";
    void request<WorkspaceDirectory>(`${runPath(runId)}/workspace${suffix}`)
      .then((result) => {
        if (directorySequence.current.get(path) !== sequence) return;
        setDirectories((current) => ({
          ...current,
          [path]: {
            loading: false,
            error: "",
            items: result.items,
            truncated: result.truncated,
          },
        }));
      })
      .catch((loadError) => {
        if (directorySequence.current.get(path) !== sequence) return;
        setDirectories((current) => ({
          ...current,
          [path]: {
            ...(current[path] ?? EMPTY_DIRECTORY),
            loading: false,
            error: errorText(loadError),
          },
        }));
      });
  }

  useEffect(() => {
    setDirectories({});
    setExpanded(new Set([""]));
    setQuery("");
    setSearch(null);
    setSearchError("");
    loadDirectory("", true);
    // loadDirectory intentionally uses the fresh runId after a session switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    for (const path of expanded) loadDirectory(path, true);
    // The host file manifest changes at checkpoints, not on every SSE poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filesVersion]);

  useEffect(() => {
    const trimmed = query.trim();
    const sequence = ++searchSequence.current;
    if (!trimmed) {
      setSearch(null);
      setSearchError("");
      setSearching(false);
      return;
    }
    setSearching(true);
    setSearchError("");
    const timer = window.setTimeout(() => {
      void request<WorkspaceSearch>(
        `${runPath(runId)}/workspace/search?q=${encodeURIComponent(trimmed)}&limit=50`,
      )
        .then((result) => {
          if (sequence === searchSequence.current) setSearch(result);
        })
        .catch((searchFailure) => {
          if (sequence === searchSequence.current)
            setSearchError(errorText(searchFailure));
        })
        .finally(() => {
          if (sequence === searchSequence.current) setSearching(false);
        });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [query, runId]);

  function toggleDirectory(path: string) {
    const next = new Set(expanded);
    if (next.has(path)) next.delete(path);
    else {
      next.add(path);
      loadDirectory(path);
    }
    setExpanded(next);
  }

  function renderDirectory(path: string, depth: number) {
    const state = directories[path];
    if (!state) return null;
    if (state.loading && state.items.length === 0)
      return <p className="workspace-tree-note">正在读取…</p>;
    if (state.error)
      return (
        <button className="workspace-tree-retry" onClick={() => loadDirectory(path, true)}>
          {state.error} · 重试
        </button>
      );
    return (
      <>
        {state.items.map((entry) => {
          const folder = entry.kind === "directory";
          const open = folder && expanded.has(entry.relative_path);
          return (
            <div key={entry.relative_path}>
              <div
                className={`workspace-tree-row ${selectedPath === entry.relative_path ? "selected" : ""}`}
                style={{ paddingLeft: 6 + depth * 14 }}
                title={entry.relative_path}
              >
                <button
                  type="button"
                  className="workspace-entry"
                  onClick={() =>
                    folder ? toggleDirectory(entry.relative_path) : onOpenFile(entry.relative_path, entry.size_bytes)
                  }
                >
                  {folder ? (
                    <span className={`workspace-chevron ${open ? "open" : ""}`}>›</span>
                  ) : (
                    <span className="workspace-chevron-spacer" />
                  )}
                  {folder ? (
                    <FolderIcon size={14} open={open} />
                  ) : (
                    getFileIcon(entry.name, 14)
                  )}
                  <span className="workspace-entry-name">{entry.name}</span>
                </button>
                {!folder && (
                  <button
                    type="button"
                    className="workspace-mention"
                    aria-label={`引用 ${entry.relative_path}`}
                    title="插入到消息输入框"
                    onClick={() => onMention(entry.relative_path)}
                  >
                    @
                  </button>
                )}
              </div>
              {folder && open && renderDirectory(entry.relative_path, depth + 1)}
            </div>
          );
        })}
        {state.truncated && <p className="workspace-tree-note">目录内容过多，仅显示前 500 项</p>}
      </>
    );
  }

  const searchItems = search?.items ?? [];
  return (
    <div className="workspace-explorer">
      <div className="workspace-search">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索工作区文件…"
          aria-label="搜索工作区文件"
          maxLength={200}
        />
        <button
          type="button"
          className="icon-button"
          aria-label="刷新工作区文件"
          title="刷新工作区文件"
          onClick={() => {
            setDirectories({});
            setExpanded(new Set([""]));
            loadDirectory("", true);
          }}
        >
          ↻
        </button>
      </div>
      <div className="workspace-tree" role="tree" aria-label="工作区文件">
        {query.trim() ? (
          searchError ? (
            <p className="workspace-tree-note error">{searchError}</p>
          ) : searching && !search ? (
            <p className="workspace-tree-note">正在搜索…</p>
          ) : searchItems.length ? (
            searchItems.map((entry) => (
              <div className="workspace-search-result" key={entry.relative_path}>
                <button className="workspace-entry" onClick={() => onOpenFile(entry.relative_path, entry.size_bytes)}>
                  {getFileIcon(entry.name, 14)}
                  <span className="workspace-entry-name">{entry.relative_path}</span>
                </button>
                <button
                  type="button"
                  className="workspace-mention"
                  aria-label={`引用 ${entry.relative_path}`}
                  title="插入到消息输入框"
                  onClick={() => onMention(entry.relative_path)}
                >
                  @
                </button>
              </div>
            ))
          ) : (
            <p className="workspace-tree-note">未找到匹配文件</p>
          )
        ) : (
          renderDirectory("", 0)
        )}
        {search?.truncated && (
          <p className="workspace-tree-note">匹配结果较多，仅显示前 50 项</p>
        )}
      </div>
    </div>
  );
}
