import { useEffect, useState } from "react";
import { errorText, runPath } from "./api/client";

export type WorkspaceOpenFile = {
  runId: string;
  path: string;
  sizeBytes: number | null;
  nonce: number;
};

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function WorkspaceFilePreview({ file, onMention }: {
  file: WorkspaceOpenFile;
  onMention: (path: string) => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const isImage = /\.(png|jpe?g|webp)$/i.test(file.path);
  const url = `/api/v1${runPath(file.runId)}/workspace/file?path=${encodeURIComponent(file.path)}&v=${file.nonce}`;

  useEffect(() => {
    setText("");
    setError("");
    setLoading(false);
    if (isImage) return;
    const abort = new AbortController();
    setLoading(true);
    void fetch(url, { credentials: "same-origin", signal: abort.signal })
      .then(async response => {
        if (!response.ok) {
          let message = `无法预览（${response.status}）`;
          try {
            const body = await response.json() as { message?: string };
            if (body.message) message = body.message;
          } catch { /* Keep the HTTP status for non-JSON responses. */ }
          throw new Error(message);
        }
        return response.text();
      })
      .then(value => { if (!abort.signal.aborted) setText(value); })
      .catch(cause => { if (!abort.signal.aborted) setError(errorText(cause)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [url, isImage]);

  return <div className="preview workspace-preview">
    {file.sizeBytes !== null && <p className="preview-meta">{formatSize(file.sizeBytes)}</p>}
    <button className="workspace-preview-mention" onClick={() => onMention(file.path)}>@{file.path}</button>
    {error ? <p role="alert" className="error">{error}</p>
      : isImage ? <img alt={file.path} src={url} onError={() => setError("图片无法显示，文件可能正在变化或格式不受支持")} />
      : <pre>{loading ? "正在读取…" : text || "（空文件）"}</pre>}
  </div>;
}
