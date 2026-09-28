"""通过 Evals 公开生命周期保留 Case 追踪，评分仍由原 evaluator 产生。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AbstractContextManager
from typing import Any

import httpx
from opentelemetry import trace
from pydantic_core import to_json
from pydantic_evals import Case
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.lifecycle import CaseLifecycle
from pydantic_evals.reporting import EvaluationReport, ReportCase, ReportCaseFailure

from abaqus_sim_agent import telemetry

_logger = logging.getLogger(__name__)


def _json(value: object) -> str:
    return to_json(value, fallback=str).decode()


class PhoenixCaseLifecycle[InputsT, OutputT, MetadataT](
    CaseLifecycle[InputsT, OutputT, MetadataT]
):
    """每次 Case/重复运行独立建 span；不更改输入、评分、重试与业务终态。"""

    def __init__(self, case: Case[InputsT, OutputT, MetadataT]) -> None:
        super().__init__(case)
        self._scope: AbstractContextManager[trace.Span] | None = None
        self._identity: AbstractContextManager[None] | None = None
        self._span = trace.INVALID_SPAN

    async def setup(self) -> None:
        self._identity = telemetry.execution_context(
            case_id=self.case.name or "unnamed"
        )
        self._identity.__enter__()
        self._scope = telemetry.span(
            "evaluation.case", case_id=self.case.name or "unnamed"
        )
        self._span = self._scope.__enter__()
        if self._span.is_recording():
            self._span.set_attributes(
                {
                    "openinference.span.kind": "CHAIN",
                    "input.value": _json(self.case.inputs),
                    "input.mime_type": "application/json",
                    "evaluation.expected_output": _json(self.case.expected_output),
                }
            )

    async def prepare_context(
        self, ctx: EvaluatorContext[InputsT, OutputT, MetadataT]
    ) -> EvaluatorContext[InputsT, OutputT, MetadataT]:
        if isinstance(run_id := ctx.attributes.get("run_id"), str):
            self._span.set_attribute("session.id", run_id)
        return ctx

    async def teardown(
        self,
        result: ReportCase[InputsT, OutputT, MetadataT]
        | ReportCaseFailure[InputsT, OutputT, MetadataT]
        | None,
    ) -> None:
        try:
            if not self._span.is_recording():
                return
            ctx = self._span.get_span_context()
            if result is not None:
                result.trace_id = f"{ctx.trace_id:032x}"
                result.span_id = f"{ctx.span_id:016x}"
                self._span.set_attribute("case_name", result.name)
            if isinstance(result, ReportCase):
                self._span.set_attributes(
                    {
                        "evaluation.status": "completed",
                        "output.value": _json(result.output),
                        "output.mime_type": "application/json",
                        "evaluation.metrics": _json(result.metrics),
                        "evaluation.assertions": _json(
                            {key: item.value for key, item in result.assertions.items()}
                        ),
                        "evaluation.scores": _json(
                            {key: item.value for key, item in result.scores.items()}
                        ),
                        "evaluation.labels": _json(
                            {key: item.value for key, item in result.labels.items()}
                        ),
                    }
                )
            elif isinstance(result, ReportCaseFailure):
                self._span.set_attribute("evaluation.status", "failed")
                self._span.set_attribute("evaluation.error", result.error_message)
                self._span.set_status(trace.StatusCode.ERROR, "Case execution failed")
            else:
                self._span.set_attribute("evaluation.status", "interrupted")
        except Exception:
            _logger.warning("Could not record Phoenix Case metadata")
        finally:
            try:
                if self._scope is not None:
                    self._scope.__exit__(None, None, None)
            finally:
                if self._identity is not None:
                    self._identity.__exit__(None, None, None)


async def publish_evaluation_feedback(report: EvaluationReport[Any, Any, Any]) -> None:
    """向本机 Phoenix 复制已有评分；无后端或发送失败不改本地 report/gate。"""
    endpoint = telemetry.collector_endpoint()
    if endpoint is None:
        return

    def publish() -> None:
        from phoenix.client import Client
        from phoenix.client.__generated__.v1 import AnnotationResult, SpanAnnotationData

        annotations: list[SpanAnnotationData] = []
        for case in report.cases:
            if not case.span_id:
                continue
            for kind, scores in (
                ("assertion", case.assertions),
                ("score", case.scores),
                ("label", case.labels),
            ):
                for name, score in scores.items():
                    value = score.value
                    result: AnnotationResult = {}
                    if isinstance(value, (int, float)):
                        result["score"] = float(value)
                    if isinstance(value, (bool, str)):
                        result["label"] = telemetry.redact_text(str(value))
                    if score.reason is not None:
                        result["explanation"] = telemetry.redact_text(score.reason)
                    annotations.append(
                        {
                            "span_id": case.span_id,
                            "name": name,
                            "annotator_kind": "CODE",
                            "identifier": f"{kind}:{name}",
                            "result": result,
                            "metadata": {
                                "case_name": case.name,
                                "kind": kind,
                                "source": "pydantic-evals-report",
                            },
                        }
                    )
        if not annotations:
            return
        if not telemetry.flush():
            _logger.warning(
                "Phoenix trace flush did not complete; feedback may arrive before spans"
            )
        with httpx.Client(base_url=endpoint, timeout=2, trust_env=False) as http:
            Client(http_client=http).spans.log_span_annotations(
                span_annotations=annotations, sync=False
            )

    try:
        await asyncio.to_thread(publish)
    except Exception:
        _logger.warning(
            "Could not send Phoenix evaluation feedback; local report is unchanged"
        )
