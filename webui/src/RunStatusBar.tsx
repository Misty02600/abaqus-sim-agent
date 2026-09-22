import type { Snapshot } from "./api/client";
import { currentAction } from "./runPresentation";
import { ThinkingIcon } from "./ThinkingIcon";

function tokenCount(value: number) {
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k` : `${value}`;
}

function countLimit(value: Snapshot["limits"]["model_requests"]) {
  return value?.mode === "limited" ? value.value : "不限";
}

function runtimeLimit(value: Snapshot["runtime_policy"]["abaqus"]) {
  if (!value || value.mode === "deny") return "禁用";
  return value.mode === "allow_with_limit" ? value.limit : "不限";
}

export function RunStatusBar({
  snapshot,
  connected,
  onDetails,
  onCancelSolver,
  cancellingSolver = false,
}: {
  snapshot: Snapshot;
  connected: boolean;
  onDetails: () => void;
  onCancelSolver?: () => void;
  cancellingSolver?: boolean;
}) {
  const usage = snapshot.view.usage;
  const context = snapshot.context_usage;
  const action = currentAction(snapshot);
  const execution = snapshot.tool_execution;
  const solverRunning =
    execution?.status === "starting" || execution?.status === "running";
  const available = [
    snapshot.can_message ? "可发送消息" : null,
    snapshot.can_answer ? "可回答" : null,
    snapshot.can_start ? "可启动" : null,
    snapshot.can_continue ? "可继续" : null,
    snapshot.can_stop ? "可停止回复" : null,
  ].filter(Boolean);
  return (
    <div className="composer-statusbar" aria-label="当前配置与累计用量">
      <div className="status-primary">
        <ThinkingIcon active={snapshot.active || solverRunning} />
        <strong>{action.title}</strong>
        <span className={`status-connection ${connected ? "" : "disconnected"}`}>
          {connected ? "已连接" : "连接断开"}
        </span>
        {available.length > 0 && (
          <span className="available-actions">{available.join(" · ")}</span>
        )}
        {execution && solverRunning && (
          <span className="solver-progress">
            {execution.job_name} · {execution.elapsed_seconds.toFixed(1)} 秒
          </span>
        )}
        {snapshot.can_cancel_tool_execution && onCancelSolver && (
          <button
            className="status-tool-action"
            disabled={cancellingSolver || execution?.cancel_requested}
            onClick={onCancelSolver}
          >
            {execution?.cancel_requested ? "正在取消" : "取消求解"}
          </button>
        )}
      </div>
      <div className="status-secondary">
        <button
          className="model-info"
          onClick={onDetails}
          title="查看当前运行配置"
        >
          {snapshot.model}
        </button>
        <span>
          {snapshot.reasoning_policy === "enabled_high"
            ? "推理 high"
            : "默认推理"}
        </span>
        <span
          className="usage-summary"
          title="框架已记录的累计用量，不代表全部HTTP尝试次数。"
        >
          请求 {usage?.model_requests ?? 0}/{countLimit(snapshot.limits.model_requests)} ·
          Abaqus {usage?.abaqus_calls ?? 0}/{runtimeLimit(snapshot.runtime_policy.abaqus)} ·
          Solver {usage?.solver_calls ?? 0}/{runtimeLimit(snapshot.runtime_policy.solver)}
        </span>
        <span
          className={`context-usage ${context.warning ? "warning" : ""}`}
          title="最近一次有真实计数的模型请求输入 token；提醒阈值是宿主界面策略，不代表模型提供方的硬上下文窗口。"
        >
          上下文 {context.latest_input_tokens == null ? "未记录" : tokenCount(context.latest_input_tokens)}
          {context.warning_threshold_tokens == null
            ? ""
            : ` / ${tokenCount(context.warning_threshold_tokens)} 提醒`}
        </span>
        <span title="已保存的累计活动时间；等待用户或外部 Abaqus 进程不计入。">
          活动 {(usage?.wall_time_seconds ?? 0).toFixed(1)}s
        </span>
      </div>
    </div>
  );
}
