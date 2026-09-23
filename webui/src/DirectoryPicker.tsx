// Adapted from Pi Web components/DirectoryPicker.tsx at 135517b.
// See THIRD_PARTY_REUSE.md and third-party/pi-web-LICENSE.
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { request, errorText } from "./api/client";
import type { components } from "./api/schema";
import { Icon } from "./Icon";
import { SidePanel } from "./SidePanel";

type DirectoryBrowse = components["schemas"]["DirectoryBrowse"];

export function DirectoryPicker({
  initialPath,
  onSelect,
  onClose,
}: {
  initialPath: string | null;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [listing, setListing] = useState<DirectoryBrowse | null>(null);
  const [pathInput, setPathInput] = useState(initialPath ?? "");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const navigate = useCallback(async (path?: string) => {
    const order = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const next = await request<DirectoryBrowse>(
        `/directories${path ? `?path=${encodeURIComponent(path)}` : ""}`,
      );
      if (order !== sequence.current) return;
      setListing(next);
      setPathInput(next.path ?? "");
    } catch (cause) {
      if (order === sequence.current) setError(errorText(cause));
    } finally {
      if (order === sequence.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void navigate(initialPath ?? undefined);
    return () => {
      sequence.current += 1;
    };
  }, [initialPath, navigate]);
  const unopened = pathInput.trim() !== (listing?.path ?? "");
  const canSelect =
    !!listing?.path && listing.selectable && !loading && !error && !unopened;
  function openPath(event: FormEvent) {
    event.preventDefault();
    if (pathInput.trim()) void navigate(pathInput.trim());
  }
  return (
    <SidePanel title="选择工作区文件夹" onClose={onClose}>
      <div className="directory-picker">
        <p className="muted small">
          会话将直接读写这个文件夹，文件不会复制到其他位置。
        </p>
        <form className="directory-picker-path" onSubmit={openPath}>
          <button
            type="button"
            className="icon-button"
            aria-label="上一级目录"
            disabled={loading || !listing?.path}
            onClick={() => void navigate(listing?.parent_path ?? undefined)}
          >
            ↑
          </button>
          <label htmlFor="workspace-directory-path">目录路径</label>
          <input
            id="workspace-directory-path"
            value={pathInput}
            autoComplete="off"
            spellCheck={false}
            placeholder="输入绝对目录路径…"
            onChange={(event) => setPathInput(event.target.value)}
          />
          <button type="submit" disabled={loading || !pathInput.trim()}>
            前往
          </button>
        </form>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {!error && listing?.selection_error && (
          <p className="muted small">{listing.selection_error}</p>
        )}
        <nav
          className="directory-picker-list"
          aria-label="可浏览的文件夹"
          aria-busy={loading}
        >
          {loading ? (
            <p className="muted">正在读取目录…</p>
          ) : (
            (listing?.drives ?? listing?.directories ?? []).map((entry) => (
              <button
                type="button"
                className="directory-picker-entry"
                key={entry.path}
                title={entry.path}
                onClick={() => void navigate(entry.path)}
              >
                <Icon name="folder" />
                <span>{entry.name}</span>
                <Icon name="chevron" />
              </button>
            ))
          )}
          {!loading &&
            !error &&
            !(listing?.drives ?? listing?.directories ?? []).length && (
              <p className="muted">没有可浏览的子目录。</p>
            )}
          {listing?.truncated && (
            <p className="muted small">
              目录较多，仅显示部分结果；可输入完整路径前往。
            </p>
          )}
        </nav>
        <div className="directory-picker-footer">
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            disabled={!canSelect}
            title={
              unopened
                ? "请先点击前往，再选择目录"
                : (listing?.selection_error ?? undefined)
            }
            onClick={() => {
              if (canSelect && listing?.path) onSelect(listing.path);
            }}
          >
            选择此文件夹
          </button>
        </div>
      </div>
    </SidePanel>
  );
}
