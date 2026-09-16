import { useEffect, useRef, useState } from "react";
import { errorText, request, runPath } from "./api/client";
import { FolderIcon, getFileIcon } from "./FileIcons";
import { SidePanel } from "./SidePanel";

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

function formatSize(bytes: number | null) {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function isImage(path: string) {
  return /\.(png|jpe?g|webp)$/i.test(path);
}

export function WorkspaceExplorer({
  runId,
  onMention,
}: {
  runId: string;
  onMention: (path: string) => void;
}) {
  const [directories, setDirectories] = useState<Record<string, DirectoryState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<WorkspaceSearch | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [selected, setSelected] = useState<WorkspaceEntry | null>(null);
  const [previewText, setPreviewText] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const searchSequence = useRef(0);

  function loadDirectory(path: string, force = false) {
    if (!force && directories[path] && !directories[path].error) return;
    setDirectories((current) => ({
      ...current,
      [path]: { ...(current[path] ?? EMPTY_DIRECTORY), loading: true, error: "" },
    }));
    const suffix = path ? `?path=${encodeURIComponent(path)}` : "";
    void request<WorkspaceDirectory>(`${runPath(runId)}/workspace${suffix}`)
      .then((result) => {
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
    setSelected(null);
    loadDirectory("", true);
    // loadDirectory intentionally uses the fresh runId after a session switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

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

  useEffect(() => {
    setPreviewText("");
    setPreviewError("");
    setPreviewLoading(false);
    if (!selected || isImage(selected.relative_path)) return;
    const abort = new AbortController();
    setPreviewLoading(true);
    void fetch(
      `/api/v1${runPath(runId)}/workspace/file?path=${encodeURIComponent(selected.relative_path)}`,
      { credentials: "same-origin", signal: abort.signal },
    )
      .then(async (response) => {
        if (!response.ok) {
          let message = `无法预览（${response.status}）`;
          try {
            const body = (await response.json()) as { message?: string };
            if (body.message) message = body.message;
          } catch {
            // Non-JSON failures still have a useful HTTP status fallback.
          }
          throw new Error(message);
        }
        return response.text();
      })
      .then((text) => {
        if (!abort.signal.aborted) setPreviewText(text);
      })
      .catch((previewFailure) => {
        if (!abort.signal.aborted) setPreviewError(errorText(previewFailure));
      })
      .finally(() => {
        if (!abort.signal.aborted) setPreviewLoading(false);
      });
    return () => abort.abort();
  }, [runId, selected]);

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
                className={`workspace-tree-row ${selected?.relative_path === entry.relative_path ? "selected" : ""}`}
                style={{ paddingLeft: 6 + depth * 14 }}
                title={entry.relative_path}
              >
                <button
                  type="button"
                  className="workspace-entry"
                  onClick={() =>
                    folder ? toggleDirectory(entry.relative_path) : setSelected(entry)
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
                <button className="workspace-entry" onClick={() => setSelected(entry)}>
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
      {selected && (
        <SidePanel title={selected.relative_path} onClose={() => setSelected(null)}>
          <div className="preview workspace-preview">
            <p className="preview-meta">
              工作区实时文件{selected.size_bytes === null ? "" : ` · ${formatSize(selected.size_bytes)}`} · 非验收产物
            </p>
            <button
              type="button"
              className="workspace-preview-mention"
              onClick={() => onMention(selected.relative_path)}
            >
              @{selected.relative_path}
            </button>
            {previewError ? (
              <p role="alert" className="error">
                {previewError}
              </p>
            ) : isImage(selected.relative_path) ? (
              <img
                alt={selected.relative_path}
                src={`/api/v1${runPath(runId)}/workspace/file?path=${encodeURIComponent(selected.relative_path)}`}
                onError={() => setPreviewError("图片无法显示，文件可能正在变化或格式不受支持")}
              />
            ) : (
              <pre>{previewLoading ? "正在读取…" : previewText || "（空文件）"}</pre>
            )}
          </div>
        </SidePanel>
      )}
    </div>
  );
}
