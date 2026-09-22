import type { Snapshot } from "./api/client";

function tokenCount(value: number) {
  return value.toLocaleString("zh-CN");
}

function countLimit(value: Snapshot["limits"]["model_requests"]) {
  if (!value || value.mode !== "limited") return "不限";
  return value.value.toLocaleString("zh-CN");
}

function runtimeAllowance(
  value: Snapshot["runtime_policy"]["abaqus"],
  used: number,
) {
  if (!value || value.mode === "deny") return "禁止";
  if (value.mode === "allow_with_limit") return `${used} / ${value.limit}`;
  return `${used} / 不限`;
}

export function RunDetails({ snapshot }: { snapshot: Snapshot }) {
  const view = snapshot.view;
  const context = snapshot.context_usage;
  const execution = snapshot.tool_execution;
  const responseOnly = view.outcome?.completion_kind === "response";
  return (
    <section className="panel">
      <span className="eyebrow">宿主记录</span>
      <h2>结果与用量</h2>
      <p className={view.verification?.passed ? "pass" : ""}>
        {responseOnly
          ? "文字回答 · 无执行验收"
          : view.verification
            ? view.verification.passed
              ? "执行证据通过"
              : "执行证据未通过"
            : "尚未完成验证"}
      </p>
      <p className="muted small">
        {responseOnly
          ? "本次交付为文字回答，未声明文件、运行或物理验证通过。"
          : "仅按本次请求检查交付物和已使用的运行阶段，不代表物理正确性验收。"}
      </p>
      {view.outcome && !responseOnly && <p>{view.outcome.summary}</p>}
      {view.verification && !view.verification.passed && (
        <pre>{view.verification.feedback}</pre>
      )}
      {!!snapshot.knowledge_packs.length && (
        <>
          <h3>私有知识包</h3>
          <dl>
            {snapshot.knowledge_packs.map((pack) => (
              <div key={pack.pack_id}>
                <dt>{pack.pack_id}</dt>
                <dd>
                  {pack.release} · {pack.pack_digest.slice(0, 12)}
                </dd>
              </div>
            ))}
          </dl>
        </>
      )}
      {execution && (
        <>
          <h3>Solver 作业</h3>
          <dl className="tool-execution-details">
            <dt>Job</dt>
            <dd>{execution.job_name}</dd>
            <dt>输入文件</dt>
            <dd>{execution.input_file}</dd>
            <dt>状态</dt>
            <dd>{execution.status}</dd>
            <dt>已用时间</dt>
            <dd>{execution.elapsed_seconds.toFixed(1)} 秒</dd>
            <dt>进程 ID</dt>
            <dd>{execution.process_id ?? "尚未记录"}</dd>
            {execution.exit_code != null && (
              <>
                <dt>退出码</dt>
                <dd>{execution.exit_code}</dd>
              </>
            )}
            {execution.error && (
              <>
                <dt>执行说明</dt>
                <dd>{execution.error}</dd>
              </>
            )}
          </dl>
        </>
      )}
      <dl>
        <dt>模型请求</dt>
        <dd>
          {view.usage?.model_requests ?? 0} / {countLimit(snapshot.limits.model_requests)}
        </dd>
        <dt>Abaqus 调用</dt>
        <dd>{runtimeAllowance(snapshot.runtime_policy.abaqus, view.usage?.abaqus_calls ?? 0)}</dd>
        <dt>Solver 调用</dt>
        <dd>{runtimeAllowance(snapshot.runtime_policy.solver, view.usage?.solver_calls ?? 0)}</dd>
        <dt>累计活动时间</dt>
        <dd>{(view.usage?.wall_time_seconds ?? 0).toFixed(1)} 秒</dd>
        <dt>最近请求上下文</dt>
        <dd className={context.warning ? "context-usage warning" : "context-usage"}>
          {context.latest_input_tokens == null
            ? "未记录"
            : `${tokenCount(context.latest_input_tokens)} input tokens`}
        </dd>
        <dt>上下文提醒阈值</dt>
        <dd>
          {context.warning_threshold_tokens == null
            ? "未启用"
            : `${tokenCount(context.warning_threshold_tokens)} tokens（宿主策略）`}
        </dd>
      </dl>
      <details>
        <summary>运行详情</summary>
        <p>{snapshot.model}</p>
        <p>推理配置：{snapshot.reasoning_policy}</p>
        <p>请求数为框架已记录值，可能不含最后失败的逻辑请求。</p>
        <p>上下文数字取最近一次带真实用量的模型响应，不是 CodingRun 累计 input tokens。</p>
        <p>提醒阈值用于界面提示，不声明模型提供方的硬上下文上限。</p>
        <code>{view.run_id}</code>
        <p>任务修订 {view.task_revision ?? 0}</p>
        <p>
          验证范围：
          {responseOnly
            ? "不适用（文字回答）"
            : (view.outcome?.verification_scope ?? "尚无结果")}
        </p>
      </details>
    </section>
  );
}
