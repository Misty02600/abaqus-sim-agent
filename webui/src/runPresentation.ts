import type { View, Snapshot } from "./api/client";

const labels: Record<string, string> = {
  registered: "待启动",
  running: "执行中",
  waiting_for_user: "等待答复",
  waiting_for_tool_execution: "等待求解完成",
  interrupted: "已中断",
  needs_reconcile: "需要核对执行",
  completed: "已完成",
  failed: "未完成",
  unavailable: "记录不可用",
};
export function runLabel(view: View) {
  if (view.status === "interrupted" && view.phase === "stopped")
    return "已停止";
  if (
    view.status === "completed" &&
    view.outcome?.completion_kind === "response"
  )
    return "已回答";
  if (view.status === "completed" && view.verification?.passed)
    return view.verification.scope === "execution_evidence"
      ? "执行证据通过"
      : "宿主验收通过";
  return labels[view.status ?? "registered"] ?? "状态待确认";
}
export function currentAction(snapshot: Snapshot): {
  title: string;
  hint: string;
  tone: string;
} {
  const view = snapshot.view;
  const execution = snapshot.tool_execution;
  if (view.status === "waiting_for_tool_execution") {
    if (snapshot.active)
      return {
        title: "正在接回求解结果",
        hint: "宿主正在把原 Solver 调用的结果交回同一次 Agent 执行。",
        tone: "neutral",
      };
    if (execution?.cancel_requested)
      return {
        title: "正在取消 Standard 求解",
        hint: "宿主正在终止当前进程树；完成前不会接受新的 Agent 消息。",
        tone: "warning",
      };
    return {
      title:
        execution?.status === "starting"
          ? "Standard 求解准备中"
          : "Standard 求解中",
      hint: "模型工作段已释放；可查看已用时间，当前不接受新的 Agent 消息。",
      tone: "neutral",
    };
  }
  if (snapshot.active) {
    if (snapshot.stop_requested)
      return {
        title: "正在停止回复",
        hint: "保存已收到的内容后，可继续或发送新消息。",
        tone: "neutral",
      };
    const phase: Record<string, string> = {
      model: "正在生成回复",
      cae: "CAE 建模中",
      solver: "Standard 求解中",
      extract: "正在提取结果",
      verification: "宿主正在验收",
    };
    return {
      title: phase[view.phase ?? ""] ?? "正在处理",
      hint: snapshot.can_stop
        ? "可以编辑草稿；停止回复后可发送新消息。"
        : "可以编辑草稿，当前阶段不提供模型停止操作。",
      tone: "neutral",
    };
  }
  if (view.status === "needs_reconcile")
    return {
      title: "需要核对外部执行",
      hint: "存在尚未确认的外部执行。本页不提供强制继续或作业重附着。",
      tone: "warning",
    };
  if (view.status === "unavailable")
    return {
      title: "记录不可用",
      hint: "可刷新状态或查看本地记录，当前不能发送执行请求。",
      tone: "danger",
    };
  if (view.question?.pending)
    return {
      title: "等待你的答复",
      hint: snapshot.can_answer
        ? "回复下方当前问题，保存后继续。"
        : "问题仍待答，当前暂不能提交；草稿会保留。",
      tone: "warning",
    };
  if (view.phase === "revision_pending")
    return {
      title: "输入已保存，尚未继续",
      hint: "可点击继续处理已保存的要求；不会重新发送这条消息。",
      tone: "warning",
    };
  if (snapshot.command_uncertain)
    return {
      title: "执行状态待确认",
      hint: "上次命令回执未完成，请先检查当前状态。",
      tone: "warning",
    };
  if (view.status === "registered")
    return {
      title: "首条消息已保存",
      hint: "尚未开始处理，可重试发送。",
      tone: "neutral",
    };
  if (view.status === "interrupted")
    return {
      title: runLabel(view),
      hint:
        view.phase === "stopped"
          ? "已收到的回复保留。可发送新消息，或继续未完成的请求。"
          : "已有上下文与用量保留；按可用操作继续或补充要求。",
      tone: "neutral",
    };
  if (view.status === "failed")
    return {
      title: "本次请求未完成",
      hint: view.outcome?.summary ?? "查看详情了解已记录的失败原因。",
      tone: "danger",
    };
  return { title: runLabel(view), hint: "", tone: "neutral" };
}
