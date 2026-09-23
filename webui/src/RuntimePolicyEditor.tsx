import { useState, type FormEvent } from "react";
import type { Snapshot } from "./api/client";

export type RuntimePermission = NonNullable<
  Snapshot["runtime_policy"]["abaqus"]
>;
type Permission = RuntimePermission;
type PermissionMode = Permission["mode"];

function initialLimit(permission: Permission, fallback: number) {
  return permission.mode === "allow_with_limit" ? permission.limit : fallback;
}

function permission(mode: PermissionMode, limit: number): Permission {
  return mode === "allow_with_limit" ? { mode, limit } : { mode };
}

export function RuntimePolicyEditor({
  snapshot,
  busy,
  error,
  onSave,
  onCancel,
}: {
  snapshot: Snapshot;
  busy: boolean;
  error: string;
  onSave: (
    abaqus: Permission,
    solver: Permission,
    expectedTaskRevision: number,
    expectedAmendmentSequence: number,
  ) => Promise<boolean>;
  onCancel: () => void;
}) {
  const currentAbaqus = snapshot.runtime_policy.abaqus ?? { mode: "allow" };
  const currentSolver = snapshot.runtime_policy.solver ?? { mode: "deny" };
  const [abaqusMode, setAbaqusMode] = useState<PermissionMode>(
    currentAbaqus.mode,
  );
  const [solverMode, setSolverMode] = useState<PermissionMode>(
    currentSolver.mode,
  );
  const [abaqusLimit, setAbaqusLimit] = useState(
    initialLimit(currentAbaqus, 10),
  );
  const [solverLimit, setSolverLimit] = useState(
    initialLimit(currentSolver, 3),
  );
  const [expected] = useState({
    taskRevision: snapshot.view.task_revision ?? 0,
    amendmentSequence: snapshot.view.amendment_sequence ?? 0,
  });
  const usage = snapshot.view.usage;
  function submit(event: FormEvent) {
    event.preventDefault();
    void onSave(
      permission(abaqusMode, abaqusLimit),
      permission(solverMode, solverLimit),
      expected.taskRevision,
      expected.amendmentSequence,
    );
  }
  return (
    <form className="runtime-policy-editor" onSubmit={submit}>
      <p>
        修改只约束后续 Abaqus 提交，不会继续当前会话，也不会取消已经提交的执行。
      </p>
      <p className="muted small">
        有限次数是会话累计总上限；已使用次数不会清零。将上限设为不高于已用量时，后续调用会被拒绝。
      </p>
      <label>
        <span>
          Abaqus CAE / 提取
          <small>已使用 {usage?.abaqus_calls ?? 0} 次</small>
        </span>
        <select
          aria-label="Abaqus 运行权限"
          value={abaqusMode}
          disabled={busy}
          onChange={(event) =>
            setAbaqusMode(event.target.value as PermissionMode)
          }
        >
          <option value="deny">禁止</option>
          <option value="allow">允许，不限次数</option>
          <option value="allow_with_limit">允许有限次数</option>
        </select>
        {abaqusMode === "allow_with_limit" && (
          <input
            aria-label="Abaqus 累计总上限"
            type="number"
            min={1}
            value={abaqusLimit}
            disabled={busy}
            onChange={(event) =>
              setAbaqusLimit(Math.max(1, Number(event.target.value)))
            }
          />
        )}
      </label>
      <label>
        <span>
          Solver
          <small>已使用 {usage?.solver_calls ?? 0} 次</small>
        </span>
        <select
          aria-label="Solver 运行权限"
          value={solverMode}
          disabled={busy}
          onChange={(event) =>
            setSolverMode(event.target.value as PermissionMode)
          }
        >
          <option value="deny">禁止</option>
          <option value="allow">允许，不限次数</option>
          <option value="allow_with_limit">允许有限次数</option>
        </select>
        {solverMode === "allow_with_limit" && (
          <input
            aria-label="Solver 累计总上限"
            type="number"
            min={1}
            value={solverLimit}
            disabled={busy}
            onChange={(event) =>
              setSolverLimit(Math.max(1, Number(event.target.value)))
            }
          />
        )}
      </label>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="runtime-policy-actions">
        <button type="button" disabled={busy} onClick={onCancel}>
          取消
        </button>
        <button type="submit" disabled={busy}>
          {busy ? "正在保存…" : "保存权限"}
        </button>
      </div>
    </form>
  );
}
