import { useEffect, useState } from "react";
import { errorText, runPath, type Artifact, type Snapshot } from "./api/client";
import { getFileIcon } from "./FileIcons";
import { SidePanel } from "./SidePanel";

const DOWNLOAD_LIMIT = 200 * 1024 * 1024;

export function Artifacts({
  snapshot,
  selectedPath,
  onSelectedPathChange,
}: {
  snapshot: Snapshot;
  selectedPath: string | null;
  onSelectedPathChange: (path: string | null) => void;
}) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const files = snapshot.view.files ?? [];
  const selected = files.find((file) => file.relative_path === selectedPath) ?? null;

  useEffect(() => {
    if (
      snapshot.active ||
      (selected && !files.some((file) => file.id === selected.id))
    ) {
      onSelectedPathChange(null);
    }
  }, [files, selected, snapshot.active]);

  useEffect(() => {
    onSelectedPathChange(null);
    setText("");
    setError("");
  }, [snapshot.view.run_id, snapshot.view.task_revision]);

  useEffect(() => {
    setText("");
    setError("");
    setLoading(false);
    if (!selected || selected.relative_path.toLowerCase().endsWith(".png")) {
      return;
    }
    const abort = new AbortController();
    const path = `/api/v1${runPath(snapshot.view.run_id)}/artifacts/${selected.id}?preview=true`;
    setLoading(true);
    void fetch(path, { signal: abort.signal })
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json();
          throw new Error(body.message ?? "无法预览");
        }
        return response.text();
      })
      .then((value) => {
        if (!abort.signal.aborted) setText(value);
      })
      .catch((fetchError) => {
        if (!abort.signal.aborted) setError(errorText(fetchError));
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [selected, snapshot.view.run_id]);

  return (
    <div className="artifact-browser">
      {files.length === 0 ? (
        <p className="empty">暂无文件</p>
      ) : (
        <ul className="file-list">
          {files.map((file) => {
            const tooLarge = file.size_bytes > DOWNLOAD_LIMIT;
            return (
              <li key={file.id}>
                <button
                  className={`file ${selected?.id === file.id ? "selected" : ""}`}
                  disabled={snapshot.active || tooLarge}
                  onClick={() => onSelectedPathChange(file.relative_path)}
                  title={file.relative_path}
                >
                  <span className="file-title-row">
                    {getFileIcon(file.relative_path, 14)}
                    <span className="file-path">{file.relative_path}</span>
                  </span>
                  <small>
                    {(file.size_bytes / 1024).toFixed(1)} KB ·{" "}
                    {file.fresh ? "当前修订产物" : "已有文件"}
                  </small>
                </button>
                {tooLarge ? (
                  <small title={`${snapshot.workspace}/${file.relative_path}`}>
                    超过网页上限，请在本地读取
                  </small>
                ) : (
                  !snapshot.active && (
                    <a
                      className="download"
                      href={`/api/v1${runPath(snapshot.view.run_id)}/artifacts/${file.id}`}
                      download
                    >
                      下载
                    </a>
                  )
                )}
              </li>
            );
          })}
        </ul>
      )}
      {selected && (
        <SidePanel
          title={selected.relative_path}
          onClose={() => onSelectedPathChange(null)}
        >
          <div className="preview">
            <p className="preview-meta">
              {selected.fresh ? "当前修订产物" : "已有文件"} · SHA-256 已绑定
            </p>
            {error ? (
              <p role="alert" className="error">
                {error}
              </p>
            ) : selected.relative_path.toLowerCase().endsWith(".png") ? (
              <img
                alt={selected.relative_path}
                src={`/api/v1${runPath(snapshot.view.run_id)}/artifacts/${selected.id}?preview=true`}
                onError={() =>
                  setError("图片无法显示，可能已变化或格式不受支持")
                }
              />
            ) : (
              <pre>{loading ? "正在读取…" : text || "（空文件）"}</pre>
            )}
          </div>
        </SidePanel>
      )}
    </div>
  );
}
