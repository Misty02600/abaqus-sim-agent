import { useEffect, useState } from "react";
import { runPath } from "./api/client";

export function workspaceImagePath(source: string): string | null {
  let path: string;
  try {
    path = decodeURIComponent(source).replaceAll("\\", "/");
  } catch {
    return null;
  }
  const parts = path.split("/");
  if (
    path.startsWith("/") ||
    path.includes(":") ||
    parts.includes("..") ||
    !/\.(png|jpe?g|webp)$/i.test(path)
  ) {
    return null;
  }
  return parts.filter((part) => part && part !== ".").join("/") || null;
}

export function ViewportPreview({
  runId,
  path,
  version,
  alt,
}: {
  runId: string;
  path: string;
  version?: string;
  alt?: string;
}) {
  const [failed, setFailed] = useState(false);
  const name = path.split("/").at(-1);
  const src = `/api/v1${runPath(runId)}/workspace/file?path=${encodeURIComponent(path)}&v=${encodeURIComponent(version ?? "")}`;
  useEffect(() => setFailed(false), [src]);

  return (
    <span className="viewport-preview">
      <span className="model-preview-heading">
        <span title={path}>{name || "图片"}</span>
        <small className={failed ? "warning" : "muted"}>
          {failed ? "图片无法显示" : "工作区当前图片"}
        </small>
      </span>
      {failed ? (
        <span className="viewport-preview-error" role="status">
          图片无法读取，请检查文件是否仍在工作区且未超过预览上限。
        </span>
      ) : (
        <img
          className="viewport-preview-image"
          src={src}
          alt={alt || `工作区图片：${name || path}`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      )}
    </span>
  );
}
