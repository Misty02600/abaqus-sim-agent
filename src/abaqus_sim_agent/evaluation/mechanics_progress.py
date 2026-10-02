"""由Evals生命周期记录错误和更新进度；不执行任务、不重算评分或保存恢复状态。"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic_evals.lifecycle import CaseLifecycle
from pydantic_evals.reporting import ReportCase, ReportCaseFailure

from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.evaluation import public_mechanics_control as control
from abaqus_sim_agent.evaluation.case_metadata import CaseMetadata
from abaqus_sim_agent.evaluation.mechanics_grading import (
    MechanicsObservation,
    native_task_verdict,
)

_logger = logging.getLogger(__name__)


def progress_lifecycle(
    root: Path,
    case_ids: tuple[str, ...],
    selected_case_ids: tuple[str, ...],
    *,
    invocation_id: str,
    action: str,
) -> type[CaseLifecycle[str, MechanicsObservation, CaseMetadata]]:
    """创建本次调用独有的原生Case生命周期，覆盖最新进度视图。

    Note:
        进度包括实际执行及只读投影，不等于成功题数。派生进度写入失败只警告。
        原生任务/评分异常立即追加到已有批次日志；该证据写入失败会传播，不伪称已留存。
        各hook在同一事件循环内同步发布，不持有业务资源；强杀后视图可能停留在evaluating，
        恢复仍以Service证据为准，不能从进度文件推断是否应重跑工具。
    """
    rows = {
        name: {
            "case_id": name,
            "phase": "pending",
            "selected_for_execution": name in selected_case_ids,
            "duration_scope": None,
            "run_status": None,
            "task_verdict": None,
        }
        for name in case_ids
    }

    def publish() -> None:
        try:
            handled = sum(
                row["phase"] in {"evaluated", "task_error"} for row in rows.values()
            )
            snapshot = {
                "schema_version": "mechanics-progress-v1",
                "invocation_id": invocation_id,
                "action": action,
                "updated_at": datetime.now(UTC).isoformat(),
                "handled_cases": handled,
                "planned_cases": len(case_ids),
                "cases": list(rows.values()),
                "scope": "case processing progress, not final grades or recovery state",
            }
            write_text_atomic(
                root / "progress.json",
                json.dumps(snapshot, ensure_ascii=False, indent=2),
            )
            phases = {
                "pending": "待处理",
                "evaluating": "处理中",
                "evaluated": "已处理",
                "task_error": "任务适配异常",
                "interrupted": "已中断",
            }
            scopes = {
                "case_execution_and_observation": "实际执行",
                "saved_result_projection_only": "只读结果",
            }
            lines = [
                "# 本次测评进度",
                "",
                f"操作：{action}；Case已处理：{handled}/{len(case_ids)}。",
                "",
                "已处理包含只读结果和评分，不代表实际执行或通过题数。最终成绩见report.md；恢复以Service记录为准。",
                "",
                "| 案例 | 处理阶段 | 本次选择执行 | 动作记录 | 已知运行状态 | 任务判定 |",
                "|---|---|---|---|---|---|",
            ]
            for row in rows.values():
                lines.append(
                    f"| {row['case_id']} | {phases[row['phase']]} | {'是' if row['selected_for_execution'] else '否'} | "
                    f"{scopes.get(row['duration_scope'], '—')} | {row['run_status'] or '—'} | {row['task_verdict'] or '—'} |"
                )
            write_text_atomic(root / "progress.md", "\n".join(lines) + "\n")
        except Exception:
            _logger.warning(
                "Could not update mechanics progress view; task state and grades are unchanged"
            )

    class Progress(CaseLifecycle[str, MechanicsObservation, CaseMetadata]):
        async def setup(self) -> None:
            rows[self.case.inputs]["phase"] = "evaluating"
            publish()

        async def teardown(
            self,
            result: ReportCase[str, MechanicsObservation, CaseMetadata]
            | ReportCaseFailure[str, MechanicsObservation, CaseMetadata]
            | None,
        ) -> None:
            row = rows[self.case.inputs]
            if result is None:
                row["phase"] = "interrupted"
            elif isinstance(result, ReportCase):
                row.update(
                    phase="evaluated",
                    duration_scope=result.attributes.get("duration_scope"),
                    run_status=result.output.status,
                    task_verdict=native_task_verdict(result),
                )
            else:
                row.update(phase="task_error", task_verdict="grading_error")
            if isinstance(result, ReportCaseFailure):
                control.append_event(
                    root / "control/events.jsonl",
                    "case_evaluation_error",
                    evaluation_invocation_id=invocation_id,
                    case_id=self.case.inputs,
                    stage="task",
                    errors=[{"message": result.error_message}],
                )
            elif isinstance(result, ReportCase) and result.evaluator_failures:
                control.append_event(
                    root / "control/events.jsonl",
                    "case_evaluation_error",
                    evaluation_invocation_id=invocation_id,
                    case_id=self.case.inputs,
                    stage="grading",
                    errors=[
                        {
                            "evaluator": failure.name,
                            "error_type": failure.error_type,
                            "message": failure.error_message,
                        }
                        for failure in result.evaluator_failures
                    ],
                )
            publish()

    publish()
    return Progress
