import { useState } from "react";
import { runPath, type Artifact } from "./api/client";

export function ViewportPreview({
  runId,
  artifact,
}: {
  runId: string;
  artifact: Artifact;
}) {
  const [failed, setFailed] = useState(false);
  const name = artifact.relative_path.replaceAll("\\", "/").split("/").at(-1);
  const src = `/api/v1${runPath(runId)}/artifacts/${encodeURIComponent(artifact.id)}?preview=true`;

  return (
    <div className="viewport-preview">
      <div className="model-preview-heading">
        <span title={artifact.relative_path}>{name || "Abaqus 视图"}</span>
        <small className={failed ? "warning" : "muted"}>
          {failed ? "图片无法显示" : "Abaqus CAE 视图"}
        </small>
      </div>
      {failed ? (
        <div className="viewport-preview-error">PNG 预览读取失败</div>
      ) : (
        <img
          className="viewport-preview-image"
          src={src}
          alt={`Abaqus CAE 视图：${name || artifact.relative_path}`}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}
