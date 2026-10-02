"""关联真实力学运行与 Phoenix 评分副本；不参与任务判定。"""

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field
from pydantic_evals.reporting import EvaluationReport

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.evaluation.case_metadata import CaseMetadata
from abaqus_sim_agent.evaluation.mechanics_grading import MechanicsObservation

_logger = logging.getLogger(__name__)


class AttemptTrace(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal["mechanics-attempt-trace-v1"] = "mechanics-attempt-trace-v1"
    batch_id: str
    case_id: str
    attempt_id: str
    attempt_sha256: str
    state_sha256: dict[str, str | None]
    endpoint: str
    trace_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    span_id: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _state_hashes(state: Path) -> dict[str, str | None]:
    return {
        name: _digest(state / name)
        for name in ("outcome.json", "resume_state.json", "events.jsonl")
    }


@contextmanager
def trace_attempt(
    root: Path,
    case_id: str,
    batch_id: str,
    attempt_path: Path,
    attempt: dict,
    goal: str,
) -> Iterator[None]:
    """覆盖一次真实执行；收尾后保存追踪关联，副本写入失败不改变执行结果。

    Note:
        每次恢复新建 span；关联记录绑定最后一次 attempt 及原始状态文件。
        未结束或无记录的运行不由报告生成器补造追踪。
    """
    with (
        telemetry.execution_context(
            batch_id=batch_id, case_id=case_id, invocation_id=attempt_path.stem
        ),
        telemetry.span(
            "evaluation.case",
            batch_id=batch_id,
            case_id=case_id,
            attempt_id=attempt_path.stem,
            **{
                "openinference.span.kind": "CHAIN",
                "input.value": goal,
                "input.mime_type": "text/plain",
                "evaluation.mode": attempt["mode"],
                "duration_scope": "actual_case_attempt",
            },
        ) as current,
    ):
        try:
            yield
        finally:
            if current.is_recording():
                try:
                    outcome = attempt.get("outcome_snapshot") or {}
                    resume = attempt.get("resume_state_snapshot") or {}
                    status = outcome.get("status") or resume.get("status") or "unknown"
                    current.set_attribute("evaluation.status", status)
                    current.set_attribute(
                        "output.value",
                        json.dumps(
                            {
                                "status": status,
                                "error_type": attempt.get("error_type"),
                                "summary": outcome.get("summary"),
                            },
                            ensure_ascii=False,
                        ),
                    )
                    current.set_attribute("output.mime_type", "application/json")
                    if attempt.get("error_type"):
                        current.set_status(
                            trace.StatusCode.ERROR, attempt["error_type"]
                        )
                    attempt_hash = _digest(attempt_path)
                    endpoint = telemetry.collector_endpoint()
                    if attempt_hash and endpoint and attempt.get("finished_at"):
                        ctx = current.get_span_context()
                        link = AttemptTrace(
                            batch_id=batch_id,
                            case_id=case_id,
                            attempt_id=attempt_path.stem,
                            attempt_sha256=attempt_hash,
                            state_sha256=_state_hashes(attempt_path.parent.parent),
                            endpoint=endpoint,
                            trace_id=f"{ctx.trace_id:032x}",
                            span_id=f"{ctx.span_id:016x}",
                        )
                        write_text_atomic(
                            root / "telemetry" / case_id / attempt_path.name,
                            link.model_dump_json(indent=2),
                        )
                except Exception:
                    _logger.warning("Could not save mechanics trace association")


def associate_report_traces(
    root: Path,
    batch_id: str,
    report: EvaluationReport[str, MechanicsObservation, CaseMetadata],
) -> None:
    """只关联最新且证据未变的实际 attempt；关联缺失或损坏不影响本地评分。

    Note:
        显式启用另一个 collector 时不向它发送原 collector 的 span 评分。
        需求审查可更新；运行、验收和 attempt 字节变化则拒绝沿用旧关联。
    """
    for case in report.cases:
        try:
            state = root / case.name / "state"
            attempts = sorted((state / "attempts").glob("*.json"))
            if not attempts:
                continue
            latest = attempts[-1]
            path = root / "telemetry" / case.name / latest.name
            if not path.is_file():
                continue
            link = AttemptTrace.model_validate_json(path.read_bytes())
            endpoint = telemetry.collector_endpoint()
            if (
                int(link.trace_id, 16) == 0
                or int(link.span_id, 16) == 0
                or link.batch_id != batch_id
                or link.case_id != case.name
                or link.attempt_id != latest.stem
                or link.attempt_sha256 != _digest(latest)
                or link.state_sha256 != _state_hashes(state)
                or (endpoint is not None and link.endpoint != endpoint)
            ):
                continue
            case.trace_id = link.trace_id
            case.span_id = link.span_id
        except (OSError, ValueError, TypeError):
            _logger.warning("Could not associate mechanics report with its trace")
