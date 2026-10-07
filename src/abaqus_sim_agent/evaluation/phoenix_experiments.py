"""把原生 Evals 结果记录为 Phoenix 实验；不执行任务、不重算评分或补传。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic_core import to_json
from pydantic_evals.lifecycle import CaseLifecycle
from pydantic_evals.reporting import ReportCase, ReportCaseFailure

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.coding.persistence import write_text_atomic

_logger = logging.getLogger(__name__)
_TIMEOUT = 5
_SCHEMA = "phoenix-evals-recording-v1"


class PhoenixRecordingError(RuntimeError):
    pass


def _clean(value: Any) -> Any:
    return json.loads(telemetry.redact_text(to_json(value, fallback=str).decode()))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PhoenixExperiment:
    def __init__(self, client: Any, receipt_path: Path, receipt: dict[str, Any]):
        self.client = client
        self.receipt_path = receipt_path
        self.receipt = receipt
        self.example_ids: dict[str, str] = {}
        self.started: dict[str, datetime] = {}
        self.errors: list[str] = []

    def save_receipt(self) -> None:
        write_text_atomic(
            self.receipt_path,
            json.dumps(self.receipt, ensure_ascii=False, indent=2),
        )

    async def start(
        self,
        family: str,
        examples: Mapping[str, dict[str, Any]],
        name: str,
        metadata: Mapping[str, Any],
    ) -> None:
        rows = [
            {
                "input": _clean(value["input"]),
                "output": {},
                "metadata": _clean({**value.get("metadata", {}), "case_id": key}),
            }
            for key, value in sorted(examples.items())
        ]
        if not rows:
            raise ValueError("Phoenix experiment requires nonempty named cases")
        digest = hashlib.sha256(_canonical(rows).encode()).hexdigest()
        dataset_name = f"abaqus-sim-agent-{family}-{digest[:20]}"
        dataset = await self.find_dataset(dataset_name)
        if dataset is None:
            try:
                dataset = await self.client.datasets.create_dataset(
                    name=dataset_name,
                    inputs=[row["input"] for row in rows],
                    outputs=[row["output"] for row in rows],
                    metadata=[row["metadata"] for row in rows],
                    dataset_description=f"{_SCHEMA}; content_sha256={digest}",
                    timeout=_TIMEOUT,
                )
            except httpx.HTTPStatusError as conflict:
                if conflict.response.status_code != 409:
                    raise
                dataset = await self.find_dataset(dataset_name)
                if dataset is None:
                    raise ValueError(
                        "Conflicting Phoenix dataset could not be read"
                    ) from conflict
        actual = sorted(
            (
                {field: row[field] for field in ("input", "output", "metadata")}
                for row in dataset.examples
            ),
            key=lambda row: row["metadata"]["case_id"],
        )
        if _canonical(actual) != _canonical(rows):
            raise ValueError("Phoenix dataset content changed; refusing to reuse it")
        self.example_ids = {
            row["metadata"]["case_id"]: row.get("node_id") or row["id"]
            for row in dataset.examples
        }
        experiment = await self.client.experiments.create(
            dataset_id=dataset.id,
            dataset_version_id=dataset.version_id,
            experiment_name=name,
            experiment_description="Evals executes and grades; Phoenix stores a derived view.",
            experiment_metadata=_clean({**metadata, "recording_schema": _SCHEMA}),
            repetitions=1,
            timeout=_TIMEOUT,
        )
        self.receipt.update(
            dataset_id=dataset.id,
            dataset_version_id=dataset.version_id,
            dataset_name=dataset_name,
            dataset_sha256=digest,
            experiment_id=experiment["id"],
            experiment_name=name,
            status="recording",
            cases={},
            errors=self.errors,
        )
        self.save_receipt()

    async def find_dataset(self, name: str) -> Any:
        for item in await self.client.datasets.list(timeout=_TIMEOUT):
            if item["name"] == name:
                return await self.client.datasets.get_dataset(
                    dataset=item, timeout=_TIMEOUT
                )
        return None

    def lifecycle(
        self,
        base: type[CaseLifecycle],
        *,
        selected: set[str] | None = None,
        link_trace: Callable[[ReportCase | ReportCaseFailure], None] | None = None,
    ) -> type[CaseLifecycle]:
        """在原生命周期保存结果和释放资源之后记录，保留其异常与取消语义。

        Args:
            base: 已有生命周期类，业务状态仍由它拥有。
            selected: 只记录本次执行的题目；其他题目的只读投影不冒充新执行。
            link_trace: 可选的既有追踪关联函数；只允许使用实际运行留下的追踪。
        """
        publisher = self

        class Recording(base):
            async def setup(self) -> None:
                if self.case.name is None:
                    raise ValueError("Phoenix recording requires named Evals cases")
                publisher.started[self.case.name] = datetime.now(UTC)
                await super().setup()

            async def teardown(self, result) -> None:
                await super().teardown(result)
                if self.case.name is not None and (
                    selected is None or self.case.name in selected
                ):
                    await publisher.record(
                        self.case.name, result, link_trace=link_trace
                    )

        return Recording

    async def record(
        self,
        name: str,
        result: ReportCase | ReportCaseFailure | None,
        *,
        link_trace: Callable[[ReportCase | ReportCaseFailure], None] | None = None,
    ) -> None:
        if self.errors:
            return
        try:
            if name in self.receipt["cases"]:
                raise ValueError("Case already recorded in this invocation")
            if link_trace is not None and result is not None:
                link_trace(result)
            failed = isinstance(result, ReportCaseFailure)
            trace_id = result.trace_id if result is not None else None
            output = (
                {
                    "output": _clean(result.output),
                    "metrics": _clean(result.metrics),
                    "attributes": _clean(result.attributes),
                    "evaluator_errors": _clean(
                        [
                            {
                                "name": failure.name,
                                "error_type": failure.error_type,
                                "message": failure.error_message,
                            }
                            for failure in result.evaluator_failures
                        ]
                    ),
                    "duration_scope": "case_execution_and_grading",
                }
                if isinstance(result, ReportCase)
                else None
            )
            run = await self.client.experiments.log_run(
                experiment_id=self.receipt["experiment_id"],
                dataset_example_id=self.example_ids[name],
                output=output,
                start_time=self.started.get(name, datetime.now(UTC)),
                end_time=datetime.now(UTC),
                trace_id=trace_id,
                error=telemetry.redact_text(result.error_message)
                if failed
                else "Case execution cancelled before a result was available"
                if result is None
                else None,
                timeout=_TIMEOUT,
            )
            self.receipt["cases"][name] = {"run_id": run["id"], "status": "partial"}
            if isinstance(result, ReportCase):
                for kind, values in (
                    ("assertion", result.assertions),
                    ("score", result.scores),
                    ("label", result.labels),
                ):
                    for key, item in values.items():
                        value = item.value
                        numeric = isinstance(value, (bool, int, float))
                        await self.client.experiments.log_evaluation(
                            experiment_run_id=run["id"],
                            name=f"{kind}.{key}",
                            score=float(value)
                            if numeric and math.isfinite(value)
                            else None,
                            label=telemetry.redact_text(str(value)),
                            explanation=telemetry.redact_text(item.reason)
                            if item.reason is not None
                            else None,
                            metadata={"kind": kind, "source": "pydantic-evals"},
                            timeout=_TIMEOUT,
                        )
                for index, failure in enumerate(result.evaluator_failures):
                    await self.client.experiments.log_evaluation(
                        experiment_run_id=run["id"],
                        name=f"evaluator_error.{index}.{failure.name}",
                        error=telemetry.redact_text(failure.error_message),
                        timeout=_TIMEOUT,
                    )
            self.receipt["cases"][name]["status"] = "recorded"
            self.save_receipt()
        except Exception as exc:
            self.errors.append(f"{name}: {type(exc).__name__}")
            _logger.error(
                "Phoenix experiment recording failed for %s; local evidence is retained. "
                "No further results will be sent by this invocation.",
                name,
            )


@asynccontextmanager
async def phoenix_experiment(
    *,
    root: Path,
    family: str,
    examples: Mapping[str, dict[str, Any]],
    name: str,
    invocation_id: str,
    metadata: Mapping[str, Any],
) -> AsyncIterator[PhoenixExperiment | None]:
    """启用 Phoenix 时先准备题集和实验；写入失败在业务收尾后显式报错。

    Note:
        只记录当前调用，不重跑任务或发送历史批次。回执不是补传队列或恢复状态。
        调用方的异常或取消优先传播，不能被观测失败覆盖。
    """
    endpoint = telemetry.collector_endpoint()
    if endpoint is None:
        yield None
        return
    from phoenix.client import AsyncClient

    async with httpx.AsyncClient(
        base_url=telemetry.local_endpoint(endpoint), timeout=_TIMEOUT, trust_env=False
    ) as http:
        publisher = PhoenixExperiment(
            AsyncClient(http_client=http),
            root / "telemetry" / "experiments" / f"{invocation_id}.json",
            {
                "schema_version": _SCHEMA,
                "endpoint": endpoint,
                "invocation_id": invocation_id,
                "family": family,
            },
        )
        try:
            await publisher.start(
                family, examples, name, {**metadata, "invocation_id": invocation_id}
            )
        except Exception as exc:
            raise PhoenixRecordingError(
                f"Phoenix experiment setup failed at {endpoint} ({type(exc).__name__}). "
                "Start the local Phoenix service and verify the endpoint; no task was started."
            ) from exc
        interrupted = False
        try:
            yield publisher
        except BaseException:
            interrupted = True
            raise
        finally:
            publisher.receipt.update(
                status="incomplete"
                if publisher.errors
                else "interrupted"
                if interrupted
                else "complete",
                finished_at=datetime.now(UTC).isoformat(),
            )
            try:
                publisher.save_receipt()
            except Exception:
                if not interrupted:
                    raise
                _logger.error("Could not save Phoenix recording receipt")
            if publisher.errors and not interrupted:
                raise PhoenixRecordingError(
                    "Phoenix experiment recording is incomplete; local results are retained. "
                    f"See {publisher.receipt_path} for the recorded failures."
                )
