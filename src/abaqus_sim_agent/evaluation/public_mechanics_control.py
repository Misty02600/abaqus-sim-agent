"""公开力学评测的传输观测与可重建报告，不修改原始 CodingRun outcome。"""

import asyncio
import hashlib
import json
import os
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from pydantic_evals.reporting import (
    EvaluationReport,
    EvaluationReportAdapter,
    ScalarResult,
)

from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.evaluation.case_metadata import CaseMetadata
from abaqus_sim_agent.evaluation.mechanics_grading import (
    MechanicsObservation,
    evaluate_saved_results,
    native_task_verdict,
    observe_saved_case,
)
from abaqus_sim_agent.evaluation.mechanics_telemetry import associate_report_traces
from abaqus_sim_agent.evaluation.public_mechanics_cases import (
    PUBLIC_MECHANICS_PROTOCOL,
    MechanicsCase,
    load_frozen_cases,
)
from abaqus_sim_agent.evaluation.telemetry import publish_evaluation_feedback


def save_json(path: Path, value):
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def append_event(path: Path, event: str, **values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"event": event, "at": datetime.now(UTC).isoformat(), **values})
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


class ObservedTransport(httpx.AsyncBaseTransport):
    """记录每个实际 HTTP 尝试，包括响应体读取失败；仅用于非流式评测。

    Note:
        不保存请求/响应原文、凭据或任意响应头。强制终止时未结束的尝试保持 unknown。
    """

    def __init__(self, path: Path, inner: httpx.AsyncBaseTransport | None = None):
        self.path = path
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        attempt = uuid4().hex
        started = perf_counter()
        append_event(
            self.path,
            "http_started",
            attempt=attempt,
            retry=request.headers.get("x-stainless-retry-count"),
            body_sha256=hashlib.sha256(request.content).hexdigest(),
        )
        response = None
        try:
            response = await self.inner.handle_async_request(request)
            append_event(
                self.path, "http_headers", attempt=attempt, status=response.status_code
            )
            await response.aread()
            append_event(
                self.path,
                "http_completed",
                attempt=attempt,
                status=response.status_code,
                seconds=perf_counter() - started,
                response_bytes=len(response.content),
            )
            return response
        except BaseException as exc:
            append_event(
                self.path,
                "http_failed",
                attempt=attempt,
                error_type=type(exc).__name__,
                seconds=perf_counter() - started,
            )
            raise
        finally:
            if response is not None:
                await response.aclose()

    async def aclose(self):
        await self.inner.aclose()


def transport_summary(path: Path):
    if not path.exists():
        return {"attempts": None, "completed": None, "failed": None, "unknown": None}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    starts = {r["attempt"] for r in rows if r["event"] == "http_started"}
    completed = {r["attempt"] for r in rows if r["event"] == "http_completed"}
    failed = {r["attempt"] for r in rows if r["event"] == "http_failed"}
    return {
        "attempts": len(starts),
        "completed": len(completed),
        "failed": len(failed),
        "unknown": len(starts - completed - failed),
    }


async def run_until_pause(service, marker: Path):
    """取消由 service 持有的任务，让它记录 interrupted 并清理 typed runtime。"""
    task = asyncio.create_task(service.run())

    async def watch():
        # A cross-process pause marker requires polling, not an in-process Event.
        while not marker.exists():  # noqa: ASYNC110, ASYNC240
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait(
            (task, watcher), return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            return await task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return None
    finally:
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


def summarize_case(root: Path, name: str):
    state = root / name / "state"
    outcome = read_json(state / "outcome.json")
    resume = read_json(state / "resume_state.json")
    attempts = [read_json(p) for p in sorted((state / "attempts").glob("*.json"))]
    last = attempts[-1] if attempts else {}
    if outcome:
        classification = (
            "completed" if outcome["status"] == "completed" else "task_failed"
        )
    elif resume:
        classification = {
            "running": "interrupted_unknown",
            "interrupted": "interrupted",
            "needs_reconcile": "needs_reconcile",
        }.get(resume["status"], resume["status"])
        if resume.get("last_error_type") in {"ModelAPIError", "ModelHTTPError"}:
            classification = "provider_interrupted"
    elif last.get("error_type"):
        classification = "setup_error"
    elif state.exists():
        classification = "interrupted_unknown"
    else:
        classification = "not_started"
    usage = resume.get("usage") if resume else None
    deps = resume.get("dependencies", {}) if resume else {}
    counts = Counter()
    for path in (state / "step-persistence").glob("*/events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            counts[json.loads(line).get("kind")] += 1
    return {
        "case_id": name,
        "classification": classification,
        "outcome": outcome,
        "persisted_status": resume.get("status") if resume else None,
        "error_type": last.get("error_type"),
        "last_error_type": resume.get("last_error_type") if resume else None,
        "received_usage": usage,
        "model_request_events": {
            key: counts[key]
            for key in (
                "model_request_started",
                "model_request_completed",
                "model_request_failed",
            )
        },
        "http": transport_summary(state / "transport.jsonl"),
        "abaqus_calls": deps.get("abaqus_calls"),
        "solver_calls": deps.get("solver_calls"),
        "api_lookup_calls": deps.get("api_lookup_calls"),
        "persisted_active_wall_seconds": resume.get("active_wall_time_seconds")
        if resume
        else None,
        "attempts": attempts,
        "evidence_path": str(state),
        "billed_cost_usd": None,
    }


def _case_row(
    root: Path,
    case: MechanicsCase,
    manifest: dict,
    *,
    evaluation_errors: list[dict] | None = None,
) -> dict:
    try:
        row = summarize_case(root, case.case_id)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        row = {
            "case_id": case.case_id,
            "classification": "invalid_evidence",
            "evidence_error_type": type(exc).__name__,
            "received_usage": None,
            "http": {"attempts": None, "failed": None, "unknown": None},
            "abaqus_calls": None,
            "solver_calls": None,
        }
    errors = (
        _evaluation_errors(root) if evaluation_errors is None else evaluation_errors
    )
    if (
        row["classification"] in {"not_started", "interrupted_unknown"}
        and row.get("persisted_status") is None
        and row.get("outcome") is None
        and any(e["case_id"] == case.case_id and e["stage"] == "task" for e in errors)
    ):
        row["classification"] = "setup_error"
    row["metadata"] = case.metadata.model_dump(mode="json")
    row["included_in_score"] = case.case_id in manifest["scoring_case_ids"]
    return row


def read_case_observation(
    root: Path, case: MechanicsCase, manifest: dict
) -> MechanicsObservation:
    return observe_saved_case(
        root, case, _case_row(root, case, manifest), manifest, bindings_unchanged=True
    )


def _execution_binding(observation: MechanicsObservation) -> tuple:
    # Reviews can change after a Case finishes; execution evidence and observed usage must not.
    return (
        observation.case_id,
        observation.status,
        observation.metrics,
        observation.evidence_path,
        {
            key: value
            for key, value in observation.evidence_sha256.items()
            if key != "requirements_review_sha256"
        },
    )


def _evaluation_errors(root: Path) -> list[dict]:
    path = root / "control/events.jsonl"
    if not path.exists():
        return []
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [event for event in events if event["event"] == "case_evaluation_error"]


def build_report(root: Path, *, bindings_unchanged: bool):
    return asyncio.run(build_report_async(root, bindings_unchanged=bindings_unchanged))


async def build_report_async(
    root: Path,
    *,
    bindings_unchanged: bool,
    native_report: EvaluationReport[str, MechanicsObservation, CaseMetadata]
    | None = None,
):
    """保存原生评分的派生视图，或在没有执行报告时只读重建。

    Args:
        root: 已冻结批次目录。
        bindings_unchanged: 调用方对本次批次绑定的核查结论。
        native_report: 当前Dataset执行产生的完整报告；提供时保留其评分失败，不重新评分。

    Raises:
        ValueError: 协议/绑定不符，或执行报告不再对应当前保存的证据。
    """
    manifest = read_json(root / "manifest.json")
    if manifest.get("version") != PUBLIC_MECHANICS_PROTOCOL:
        raise ValueError("historical reports require their original worker")
    cases = load_frozen_cases(root, manifest["case_ids"])
    scoring_ids = manifest.get("scoring_case_ids")
    if (
        not isinstance(scoring_ids, list)
        or not scoring_ids
        or not all(isinstance(name, str) for name in scoring_ids)
        or len(set(scoring_ids)) != len(scoring_ids)
    ):
        raise ValueError("invalid frozen scoring_case_ids")
    evaluation_errors = _evaluation_errors(root)
    rows = [
        _case_row(root, case, manifest, evaluation_errors=evaluation_errors)
        for case in cases
    ]
    scored = [row for row in rows if row["included_in_score"]]
    observations = {
        case.case_id: observe_saved_case(
            root, case, row, manifest, bindings_unchanged=bindings_unchanged
        )
        for case, row in zip(cases, rows, strict=True)
    }
    review_updates_pending: list[str] = []
    if native_report is None:
        native = await evaluate_saved_results(
            cases,
            observations,
            tuple(scoring_ids),
            {
                "protocol": manifest["version"],
                "root": str(root),
                "scoring_case_ids": scoring_ids,
            },
        )
    else:
        names = [c.name for c in native_report.cases] + [
            c.name for c in native_report.failures
        ]
        expected = {c.case_id: c for c in cases}
        if (
            not bindings_unchanged
            or len(names) != len(expected)
            or set(names) != set(expected)
        ):
            raise ValueError(
                "native execution report does not match frozen cases/bindings"
            )
        if any(
            item.inputs != item.name or item.metadata != expected[item.name].metadata
            for item in [*native_report.cases, *native_report.failures]
        ) or any(
            _execution_binding(item.output)
            != _execution_binding(observations[item.name])
            for item in native_report.cases
        ):
            raise ValueError("native execution report no longer matches saved evidence")
        review_updates_pending = [
            item.name
            for item in native_report.cases
            if item.output.evidence_sha256.get("requirements_review_sha256")
            != observations[item.name].evidence_sha256.get("requirements_review_sha256")
        ]
        native = native_report
    associate_report_traces(root, manifest.get("batch_id", ""), native)
    verdicts = {case.name: native_task_verdict(case) for case in native.cases}
    for row in rows:
        row["task_verdict"] = verdicts.get(row["case_id"], "grading_error")
    summary = {
        item.title: item.value
        for item in native.analyses
        if isinstance(item, ScalarResult)
    }
    summary_valid = not native.report_evaluator_failures
    report = {
        "schema_version": "public-mechanics-report-v8",
        "review_updates_pending": review_updates_pending,
        "evaluation_errors": evaluation_errors,
        "native_result_kind": "case_execution"
        if native_report is not None
        else "saved_result_projection",
        "scoring": {
            "case_ids": [row["case_id"] for row in scored],
            "reference_case_ids": scoring_ids,
            "full_set_selected": {row["case_id"] for row in scored} == set(scoring_ids),
            "case_count": len(scored),
            "classification_counts": dict(
                Counter(row["classification"] for row in scored)
            ),
            "weighting": "one_per_physical_case",
            "task_summary": summary,
            "summary_valid": summary_valid,
        },
        "verification_policy": manifest.get("verification_policy"),
        "profile": {
            "foundation": manifest.get("foundation"),
            "catalog_mode": manifest.get("catalog_mode"),
            "knowledge_resources": manifest.get("knowledge_resources"),
            "max_task_concurrency": manifest.get("max_task_concurrency", 1),
            "max_runtime_concurrency": manifest.get("max_runtime_concurrency", 1),
        },
        "script_review_status": "see_bound_requirements_review_and_task_verdict",
        "at": datetime.now(UTC).isoformat(),
        "trials": rows,
        "bindings_unchanged": bindings_unchanged,
        "seal_sha256": hashlib.sha256((root / "seal.json").read_bytes()).hexdigest(),
        "native_report": "pydantic-evals-report.json",
        "note": "Derived view. Task pass requires completion, saved verification and a bound requirements review. Incomplete/posthoc/evidence failures remain in the planned denominator. HTTP usage is not billing.",
    }
    write_text_atomic(
        root / "pydantic-evals-report.json",
        EvaluationReportAdapter.dump_json(native, indent=2).decode("utf-8"),
    )
    write_text_atomic(
        root / "pydantic-evals-report.txt",
        native.render(
            include_reasons=True,
            include_durations=False,
            include_averages=False,
            width=160,
        ),
    )
    save_json(root / "report.json", report)
    if not summary_valid:
        summary_line = "整批评分器异常：汇总不可用，详见原生报告report_evaluator_failures；不生成通过率结论。"
    elif summary.get("planned_tasks") == 0:
        summary_line = "本批没有计分题，仅展示诊断记录，不生成通过率。"
    else:
        summary_line = (
            f"已确认任务通过：{summary.get('tasks_passed', 0)}/{summary.get('planned_tasks', 0)}；"
            f"待审：{summary.get('tasks_pending_review', 0)}。未运行/中断不是已证实的物理失败，且不从计划分母剔除。"
        )
    lines = [
        "# 公开力学评测运行报告",
        "",
        "自动派生视图，不修改原 outcome；收到的 usage 不包含失败调用账单。自动结果检查与评测者脚本审查分开记录。",
        "",
        "计分成员来自本批冻结的manifest，标签不改变分母；历史诊断题不计分。这里只汇总自动终态，不替代脚本审查。",
        "",
        summary_line,
        "",
        *(
            [
                "审查记录已有更新："
                + ", ".join(review_updates_pending)
                + "。当前保留Case完成时的评分快照；运行report读取最新审查。",
                "",
            ]
            if review_updates_pending
            else []
        ),
        "| 案例 | 计分 | 运行状态 | 任务判定 | 收到响应 | HTTP尝试/失败/未知 | Abaqus/Solver |",
        "|---|---|---|---|---:|---|---|",
    ]
    for row in rows:
        u = row["received_usage"] or {}
        h = row["http"]
        lines.append(
            f"| {row['case_id']} | {'是' if row['included_in_score'] else '否'} | {row['classification']} | {row['task_verdict']} | {u.get('requests', 'unknown')} | "
            f"{h['attempts']}/{h['failed']}/{h['unknown']} | {row['abaqus_calls']}/{row['solver_calls']} |"
        )
    if evaluation_errors:
        lines.extend(
            [
                "",
                "## 已记录的评测异常",
                "",
                "来自批次事件日志；历史错误保留，本次重建判定不表示原错误从未发生。",
                "",
                "| 案例 | 阶段 | 本次调用 | 错误 |",
                "|---|---|---|---|",
            ]
        )
        for error in evaluation_errors:
            detail = "; ".join(item["message"] for item in error["errors"])
            lines.append(
                f"| {_table_text(error['case_id'])} | {error['stage']} | "
                f"{error['evaluation_invocation_id']} | {_table_text(detail)} |"
            )
    lines.extend(
        [
            "",
            "## 案例元数据（来自冻结cases.json）",
            "",
            "| 案例 | 测评目的 | 标签 | 来源 | 文档版本 |",
            "|---|---|---|---|---|",
        ]
    )
    for case in cases:
        metadata = case.metadata
        source = metadata.source
        reference = _table_text(source.reference)
        if source.url is not None:
            reference += f" · [来源]({source.url})"
        lines.append(
            f"| {case.case_id} | {_table_text(metadata.description)} | "
            f"{', '.join(metadata.tags)} | {source.kind}: {reference} | "
            f"{_table_text(source.document_version or '未指定')} |"
        )
    lines.extend(
        [
            "",
            "无运行证据的计数为 unknown，不推断为0。not_started 不构成失败。",
            "恢复前的 attempt、失败HTTP与累计 usage 全部保留；未完成的工具作用必须由产品恢复门禁处理。",
            "私有 oracle 和 posthoc 与正式成绩分开；本报告不宣称 Foundation 因果收益。",
        ]
    )
    write_text_atomic(root / "report.md", "\n".join(lines) + "\n")
    # A later review must be explicitly re-read before its old grade is sent to Phoenix again.
    feedback = replace(
        native,
        cases=[
            case for case in native.cases if case.name not in review_updates_pending
        ],
    )
    await publish_evaluation_feedback(
        feedback,
        expected_annotations={
            case.case_id: {
                "assertion": (
                    "task_pass",
                    "execution_ok",
                    "requirements_ok",
                    "physics_ok",
                    "automatic_pass",
                ),
                "score": tuple(f"relative_error.{key}" for key in case.metrics),
            }
            for case in cases
        },
    )
    return report


def _table_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
