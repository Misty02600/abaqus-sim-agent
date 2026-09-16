import { useEffect, useRef, useState } from "react";
import type { EmbeddedViewer } from "online-3d-viewer";
import { runPath, type Artifact } from "./api/client";

const hasObjGeometry = (bytes: ArrayBuffer) => {
  const text = new TextDecoder().decode(bytes);
  const hasVertex = /^\s*v\s+/m.test(text);
  const hasRenderablePrimitive = /^\s*(?:f|l)\s+/m.test(text);
  return hasVertex && hasRenderablePrimitive;
};

export function ModelPreview({
  runId,
  artifact,
}: {
  runId: string;
  artifact: Artifact;
}) {
  const container = useRef<HTMLDivElement>(null);
  const viewer = useRef<EmbeddedViewer | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    const rootElement = container.current;
    if (!rootElement) return;
    const hostElement = document.createElement("div");
    hostElement.className = "model-preview-viewer";
    rootElement.appendChild(hostElement);
    const abort = new AbortController();
    let disposed = false;
    let resize: ResizeObserver | null = null;
    let embedded: EmbeddedViewer | null = null;
    let finished = false;

    function destroyViewer() {
      resize?.disconnect();
      resize = null;
      if (!embedded) return;
      embedded.Destroy();
      if (viewer.current === embedded) viewer.current = null;
      embedded = null;
    }

    async function load() {
      setState("loading");
      setError("");
      try {
        const response = await fetch(
          `/api/v1${runPath(runId)}/artifacts/${encodeURIComponent(artifact.id)}`,
          {
            credentials: "same-origin",
            signal: abort.signal,
          },
        );
        if (!response.ok) throw new Error(`模型读取失败（${response.status}）`);
        const bytes = await response.arrayBuffer();
        if (disposed) return;
        if (!hasObjGeometry(bytes)) {
          setState("error");
          setError("OBJ 不含可显示几何");
          return;
        }

        const OV = await import("online-3d-viewer");
        if (disposed) return;
        embedded = new OV.EmbeddedViewer(hostElement, {
          onModelLoaded: () => {
            finished = true;
            if (disposed) {
              destroyViewer();
              return;
            }
            setState("ready");
          },
          onModelLoadFailed: () => {
            finished = true;
            if (disposed) {
              destroyViewer();
              return;
            }
            setState("error");
            setError("OBJ 模型解析失败");
          },
        });
        const activeViewer = embedded;
        viewer.current = activeViewer;
        resize = new ResizeObserver(() => activeViewer.Resize());
        resize.observe(hostElement);

        const name = artifact.relative_path.replaceAll("\\", "/").split("/").at(-1);
        activeViewer.LoadModelFromFileList([
          new File([bytes], name || "model.obj", { type: "text/plain" }),
        ]);
      } catch (cause) {
        if (disposed || abort.signal.aborted) return;
        setState("error");
        setError(cause instanceof Error ? cause.message : "模型读取失败");
      }
    }

    void load();
    return () => {
      disposed = true;
      abort.abort();
      resize?.disconnect();
      resize = null;
      hostElement.remove();
      // online-3d-viewer does not cancel an in-flight import in Destroy().
      // Keep its detached host intact until the callback finishes so the
      // library can remove its own progress node safely.
      if (finished) destroyViewer();
    };
  }, [runId, artifact.id, artifact.relative_path, artifact.sha256]);

  function fit() {
    const embedded = viewer.current;
    if (!embedded) return;
    const inner = embedded.GetViewer();
    inner.FitSphereToWindow(inner.GetBoundingSphere(() => true), false);
  }

  const name = artifact.relative_path.replaceAll("\\", "/").split("/").at(-1);
  return (
    <div className="model-preview">
      <div className="model-preview-heading">
        <span title={artifact.relative_path}>{name || "OBJ 模型"}</span>
        <div className="model-preview-actions">
          {state === "ready" && (
            <button type="button" className="subtle" onClick={fit}>
              适配视图
            </button>
          )}
          <small className={state === "error" ? "warning" : "muted"}>
            {state === "loading" ? "正在加载 3D 模型…" : state === "ready" ? "可旋转、缩放" : error}
          </small>
        </div>
      </div>
      <div ref={container} className="model-preview-canvas" />
    </div>
  );
}
