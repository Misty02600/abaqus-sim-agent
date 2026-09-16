import type { Snapshot } from "./api/client";
import { currentAction } from "./runPresentation";

export function RunSummary({
  snapshot,
  onDetails,
}: {
  snapshot: Snapshot;
  onDetails: () => void;
}) {
  const view = snapshot.view;
  const action = currentAction(snapshot);
  const response = view.outcome?.completion_kind === "response";
  const verification = view.verification;
  return (
    <div
      className={`run-summary ${action.tone}`}
      role="status"
      aria-label="当前状态"
    >
      <div className="row">
        <strong>{action.title}</strong>
        <button className="subtle" onClick={onDetails}>
          详情
        </button>
      </div>
      {action.hint && !(view.status === "failed" && verification) && (
        <p>{action.hint}</p>
      )}
      {!response && verification && (
        <div
          className={
            verification.passed
              ? "verification-summary pass"
              : "verification-summary danger"
          }
          role={verification.passed ? undefined : "alert"}
        >
          {!verification.passed && (
            <strong>
              执行证据未通过{snapshot.active ? " · 仍在处理" : ""}
            </strong>
          )}
          <p>
            {verification.passed
              ? `验收范围：${verification.scope}。${verification.scope === "execution_evidence" ? "未进行物理正确性验收。" : ""}`
              : verification.feedback}
          </p>
        </div>
      )}
      {!snapshot.active &&
        snapshot.message_block_reason &&
        view.status !== "registered" &&
        !view.question?.pending && (
          <p className="muted">{snapshot.message_block_reason}</p>
        )}
    </div>
  );
}
