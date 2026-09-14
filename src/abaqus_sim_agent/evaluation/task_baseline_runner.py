"""共享Pydantic Evals记录层；本模块CLI仅自测/审查，真实试点由批准入口调用。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import ReportEvaluator, ReportEvaluatorContext
from pydantic_evals.lifecycle import CaseLifecycle
from pydantic_evals.reporting import ReportCase, ReportCaseFailure
from pydantic_evals.reporting.analyses import ReportAnalysis, ScalarResult

from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.evaluation.task_baseline import (
    HumanReview,
    TaskTrialReview,
    baseline_execution_config,
    planned_slots,
    simple_slots,
    simple_task_candidates,
    task_candidates,
)


class ProviderFixtureTimeout(TimeoutError):
    pass


class EnvironmentUnavailable(RuntimeError):
    pass


class _NotStarted(RuntimeError):
    pass


class OfflineBatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal[
        "offline-task-batch-v1", "live-task-pilot-v1", "live-simple-modeling-v1"
    ] = "offline-task-batch-v1"
    purpose: Literal[
        "fixture_only_not_agent_benchmark",
        "live_development_pilot",
        "live_simple_modeling",
    ] = "fixture_only_not_agent_benchmark"
    batch_id: str
    max_concurrency: int = Field(ge=1, le=2)
    status: Literal["running", "finished", "interrupted", "stopped"] = "running"
    slots: tuple[str, ...]
    records: tuple[TaskTrialReview, ...]

    @model_validator(mode="after")
    def exact_slots(self):
        live = self.purpose != "fixture_only_not_agent_benchmark"
        if live:
            pair = self.purpose == "live_simple_modeling"
            if (
                self.schema_version
                != ("live-simple-modeling-v1" if pair else "live-task-pilot-v1")
                or self.slots != (simple_slots() if pair else planned_slots()[:1])
                or self.max_concurrency != (2 if pair else 1)
            ):
                raise ValueError(
                    "live schema must match its fixed slots and concurrency"
                )
            if any(
                r.observation_kind != "live_service"
                or (r.human_review and r.human_review.source != "human")
                for r in self.records
            ):
                raise ValueError(
                    "live pilot cannot contain fixture observations or reviews"
                )
        elif self.schema_version != "offline-task-batch-v1" or any(
            r.observation_kind == "live_service" for r in self.records
        ):
            raise ValueError("offline schema cannot contain live observations")
        if (not live and self.slots != planned_slots()) or tuple(
            r.slot for r in self.records
        ) != self.slots:
            raise ValueError(
                "batch must contain all twelve unique slots in frozen block order"
            )
        if any(
            not live and r.human_review and r.human_review.source != "fixture"
            for r in self.records
        ):
            raise ValueError(
                "offline batch accepts fixture reviews, not human benchmark claims"
            )
        return self


@dataclass(frozen=True)
class TrialContext:
    slot: str
    workspace: Path
    state_dir: Path


def _json(model: BaseModel) -> str:
    return model.model_dump_json(indent=2, exclude_computed_fields=True)


def _safe_file(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or ":" in relative
        or not path.parts
    ):
        raise ValueError("evidence reference must be a relative in-batch path")
    candidate = root.joinpath(*path.parts)
    if candidate.is_symlink() or any(
        p.is_symlink() for p in candidate.parents if p != root.parent
    ):
        raise ValueError("evidence reference cannot traverse symlinks")
    if (
        not candidate.resolve().is_relative_to(root.resolve())
        or not candidate.is_file()
    ):
        raise ValueError(f"evidence missing or outside batch: {relative}")
    return candidate


def _context(root: Path, slot: str) -> TrialContext:
    trial_root = root / "trials" / slot
    return TrialContext(slot, trial_root / "workspace", trial_root / "state")


def _load(root: Path) -> OfflineBatch:
    return OfflineBatch.model_validate_json(_safe_file(root, "batch.json").read_bytes())


def _check_evidence(root: Path, row: TaskTrialReview) -> None:
    prefix = f"trials/{row.slot}/"
    refs = list(row.evidence_references)
    bindings = dict(row.evidence_sha256)
    if row.post_run_check:
        post = row.post_run_check
        if any(
            not ref.startswith(prefix + "state/post-verification/")
            for ref in (post.report_path, *post.evidence_sha256)
        ):
            raise ValueError(
                "post-run evidence must stay in this slot's post-verification directory"
            )
        report_file = _safe_file(root, post.report_path)
        if hashlib.sha256(report_file.read_bytes()).hexdigest() != post.report_sha256:
            raise ValueError("post-run report changed")
        report = json.loads(report_file.read_bytes())
        original_file = _safe_file(root, f"trials/{row.slot}/state/trial-result.json")
        if (
            not isinstance(report, dict)
            or report.get("state") != post.state
            or report.get("trial_result_sha256") != post.trial_result_sha256
            or hashlib.sha256(original_file.read_bytes()).hexdigest()
            != post.trial_result_sha256
        ):
            raise ValueError("post-run check does not match original trial")
        refs += [post.report_path, *post.evidence_sha256]
        bindings.update(post.evidence_sha256)
        bindings[post.report_path] = post.report_sha256
    if row.human_review:
        refs += [
            ref for check in row.human_review.checks.values() for ref in check.evidence
        ]
    for ref in refs:
        if not ref.startswith(prefix):
            raise ValueError("evidence belongs to another slot")
        evidence = _safe_file(root, ref)
        if hashlib.sha256(evidence.read_bytes()).hexdigest() != bindings.get(ref):
            raise ValueError(
                "evidence changed or was not bound when the trial finished"
            )
    if row.terminal not in ("not_started", "running"):
        original = _safe_file(root, f"trials/{row.slot}/state/trial-result.json")
        facts = TaskTrialReview.model_validate_json(original.read_bytes())
        if row.model_dump(
            exclude={"human_review", "post_run_check"}, exclude_computed_fields=True
        ) != facts.model_dump(
            exclude={"human_review", "post_run_check"}, exclude_computed_fields=True
        ):
            raise ValueError("batch facts differ from the original trial result")
        if (
            row.observation_kind == "live_service"
            and row.human_review
            and row.human_review.trial_result_sha256
            != hashlib.sha256(original.read_bytes()).hexdigest()
        ):
            raise ValueError("human review is not bound to this trial result")


def _summary(root: Path, batch: OfflineBatch) -> dict:
    rows = []
    for row in batch.records:
        integrity_error = None
        try:
            _check_evidence(root, row)
        except (OSError, ValueError) as exc:
            integrity_error = str(exc)
        rows.append(
            {
                "slot": row.slot,
                "observation_kind": row.observation_kind,
                "service_run_id": row.service_run_id,
                "service_verification_scope": row.service_verification_scope,
                "terminal": row.terminal,
                "cause": row.cause,
                "verdict": "unknown" if integrity_error else row.verdict,
                "reviewed": row.human_review is not None,
                "interaction_check": "unknown"
                if integrity_error
                else row.interaction_acceptance,
                "numeric_check": "unknown"
                if integrity_error
                else row.post_run_check.state
                if row.post_run_check
                else row.numeric_acceptance,
                "integrity_error": integrity_error,
            }
        )
    counts = {
        state: sum(row["verdict"] == state for row in rows)
        for state in ("pass", "fail", "unknown")
    }
    costs = {}
    for field in (
        "logical_requests",
        "model_requests_started",
        "http_attempts",
        "input_tokens",
        "output_tokens",
        "runtime_calls",
        "solver_calls",
        "host_oracle_calls",
        "wall_seconds",
        "service_wall_seconds",
    ):
        values = [
            getattr(row, field) if not status["integrity_error"] else None
            for row, status in zip(batch.records, rows, strict=True)
            if row.terminal != "not_started"
        ]
        costs[field] = {
            "known_sum": sum(v for v in values if v is not None),
            "unknown_trials": sum(v is None for v in values),
        }
    return {
        "purpose": batch.purpose,
        "batch_id": batch.batch_id,
        "status": batch.status,
        "planned": len(batch.slots),
        "started": sum(r.terminal != "not_started" for r in batch.records),
        "reviewed": sum(r["reviewed"] for r in rows),
        "counts": counts,
        "confirmed_pass_per_planned": counts["pass"] / len(batch.slots),
        "costs": costs,
        "post_run_host_calls": sum(
            r.post_run_check.host_calls for r in batch.records if r.post_run_check
        ),
        "post_run_wall_seconds": sum(
            r.post_run_check.wall_seconds for r in batch.records if r.post_run_check
        ),
        "rows": rows,
    }


def _write_summary(root: Path, batch: OfflineBatch) -> dict:
    result = _summary(root, batch)
    write_text_atomic(
        root / "summary.json", json.dumps(result, ensure_ascii=False, indent=2)
    )
    lines = [
        "# Development evaluation — automatic evidence and human review"
        if batch.purpose != "fixture_only_not_agent_benchmark"
        else "# Offline self-test — synthetic results, not Agent capability",
        "",
        f"Status: {batch.status}; planned: {len(batch.slots)}; started: {result['started']}; reviews: {result['reviewed']}",
        f"Pass/fail/unknown: {result['counts']}; confirmed pass / planned: {result['confirmed_pass_per_planned']:.3f}",
        "",
        "| Slot | Terminal | Cause | Verdict | Review |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['slot']} | {r['terminal']} | {r['cause']} | {r['verdict']} | {r['reviewed']} |"
        for r in result["rows"]
    ]
    lines += ["", "Missing evidence or tampered projections:"]
    lines += [
        f"- {r['slot']}: {r['integrity_error']}"
        for r in result["rows"]
        if r["integrity_error"]
    ]
    write_text_atomic(root / "summary.md", "\n".join(lines) + "\n")
    return result


def summarize_batch(root: Path) -> dict:
    """只读取原结果和审查记录并生成摘要，不调用任务函数。"""
    root = root.resolve(strict=True)
    with CodingRunFileLock(root):
        return _write_summary(root, _load(root))


def record_review(root: Path, slot: str, review: HumanReview) -> dict:
    """按批次profile接收模拟/人工审查并保留前版，不修改任务事实或触发重跑。

    Raises:
        ValueError: 批次仍在运行、槽位未运行、证据缺失或审查来源/绑定不匹配。
    """
    review = HumanReview.model_validate_json(_json(review))
    root = root.resolve(strict=True)
    with CodingRunFileLock(root):
        batch = _load(root)
        if (
            batch.status == "running"
            or slot not in batch.slots
            or review.source
            != (
                "human"
                if batch.purpose != "fixture_only_not_agent_benchmark"
                else "fixture"
            )
        ):
            raise ValueError("review requires a settled offline batch and known slot")
        row = next(r for r in batch.records if r.slot == slot)
        if row.terminal in ("not_started", "running"):
            raise ValueError("cannot review an unstarted or running slot")
        reviewed = row.model_copy(update={"human_review": review})
        _check_evidence(root, reviewed)
        updated = batch.model_copy(
            update={
                "records": tuple(
                    reviewed if r.slot == slot else r for r in batch.records
                )
            }
        )
        archive = root / "review-history"
        archive.mkdir(exist_ok=True)
        write_text_atomic(archive / f"{uuid4().hex}.json", _json(batch))
        write_text_atomic(root / "batch.json", _json(updated))
        return _write_summary(root, updated)


async def _run_batch(
    root: Path,
    task: Callable[[TrialContext], Awaitable[TaskTrialReview]],
    *,
    max_concurrency: int = 1,
    purpose: Literal[
        "fixture_only_not_agent_benchmark",
        "live_development_pilot",
        "live_simple_modeling",
    ] = "fixture_only_not_agent_benchmark",
    manifest_extra: dict | None = None,
) -> dict:
    """用Pydantic Evals运行固定profile的槽位，按轮分块、逐例落盘。

    Args:
        root: 必须不存在的新批次目录，避免覆盖旧证据。
        task: 可信fixture或已批准的真实服务调用；本函数不是网络/进程沙箱。
        max_concurrency: 每轮最多同时活动的任务，固定profile仅允许1或2。

    Note:
        环境异常阻止后续任务启动；已在运行的同轮任务可正常结束。取消批次时保留
        已结束结果、将活动槽位标为中断。子任务取消同样终止整个批次，避免框架
        将被取消的子任务当作空报表项。没有自动resume，也不承诺断电事务恢复。
    """
    if type(max_concurrency) is not int or max_concurrency not in (1, 2):
        raise ValueError("max_concurrency must be 1 or 2")
    live = purpose != "fixture_only_not_agent_benchmark"
    pair = purpose == "live_simple_modeling"
    if purpose == "live_development_pilot" and max_concurrency != 1:
        raise ValueError("live pilot must be serial")
    if pair and max_concurrency != 2:
        raise ValueError("simple modeling pair requires concurrency 2")
    root = await asyncio.to_thread(root.resolve)
    root.mkdir(parents=True, exist_ok=False)
    slots = simple_slots() if pair else planned_slots()[:1] if live else planned_slots()
    records = {
        slot: TaskTrialReview(
            slot=slot,
            terminal="not_started",
            cause="none",
            observation_kind="live_service" if live else "fixture_stub",
        )
        for slot in slots
    }
    batch_id = uuid4().hex
    stopped = False
    batch_task = asyncio.current_task()
    assert batch_task is not None
    started_at: dict[str, float] = {}
    errors: dict[str, str] = {}
    status: Literal["running", "finished", "interrupted", "stopped"] = "running"

    def snapshot() -> OfflineBatch:
        return OfflineBatch(
            schema_version="live-simple-modeling-v1"
            if pair
            else "live-task-pilot-v1"
            if live
            else "offline-task-batch-v1",
            purpose=purpose,
            batch_id=batch_id,
            max_concurrency=max_concurrency,
            status=status,
            slots=slots,
            records=tuple(records.values()),
        )

    def save_progress():
        write_text_atomic(root / "batch.json", _json(snapshot()))

    class Lifecycle(CaseLifecycle[TrialContext, TaskTrialReview, None]):
        async def setup(self):
            nonlocal stopped
            context = self.case.inputs
            if stopped:
                raise _NotStarted("batch stopped before this slot")
            started_at[context.slot] = time.monotonic()
            records[context.slot] = TaskTrialReview(
                slot=context.slot,
                terminal="running",
                cause="none",
                observation_kind="live_service" if live else "fixture_stub",
            )
            save_progress()
            try:
                context.workspace.mkdir(parents=True, exist_ok=False)
                context.state_dir.mkdir(exist_ok=False)
            except OSError as exc:
                stopped = True
                errors[context.slot] = "EnvironmentUnavailable"
                raise EnvironmentUnavailable("fixture workspace setup failed") from exc

        async def teardown(self, result: ReportCase | ReportCaseFailure | None):
            slot = self.case.inputs.slot
            if slot not in started_at:
                return
            if isinstance(result, ReportCase):
                row = result.output
            elif (
                result is None
                and (
                    receipt := self.case.inputs.state_dir
                    / "interrupted-observation.json"
                ).is_file()
            ):
                row = TaskTrialReview.model_validate_json(receipt.read_bytes())
                if (
                    row.slot != slot
                    or row.terminal != "interrupted"
                    or row.human_review is not None
                ):
                    raise ValueError("invalid interrupted task observation")
            else:
                error_type = errors.get(
                    slot, "CancelledError" if result is None else "UnknownError"
                )
                if isinstance(result, ReportCaseFailure):
                    write_text_atomic(
                        self.case.inputs.state_dir / "framework-error.txt",
                        result.error_stacktrace,
                    )
                cause = (
                    "provider"
                    if error_type == "ProviderFixtureTimeout"
                    else "environment"
                    if error_type == "EnvironmentUnavailable"
                    else "unknown"
                )
                row = TaskTrialReview(
                    slot=slot,
                    observation_kind="live_service" if live else "fixture_stub",
                    terminal="interrupted" if result is None else "failed",
                    cause=cause,
                    error_type=error_type,
                )
            row = row.model_copy(
                update={"wall_seconds": time.monotonic() - started_at[slot]}
            )
            row = row.model_copy(
                update={
                    "evidence_sha256": {
                        ref: hashlib.sha256(
                            _safe_file(root, ref).read_bytes()
                        ).hexdigest()
                        for ref in row.evidence_references
                    }
                }
            )
            # No await between memory update and atomic file writes: callbacks share one event loop.
            write_text_atomic(
                self.case.inputs.state_dir / "trial-result.json", _json(row)
            )
            records[slot] = row
            save_progress()

    class PlannedSummary(ReportEvaluator):
        def evaluate(self, ctx: ReportEvaluatorContext) -> list[ReportAnalysis]:
            del ctx
            counts = _summary(root, snapshot())["counts"]
            return [
                ScalarResult(
                    title=f"{label}_per_planned_slot", value=counts[label] / len(slots)
                )
                for label in counts
            ]

    async def invoke(context: TrialContext) -> TaskTrialReview:
        nonlocal stopped
        try:
            row = await task(context)
            row = TaskTrialReview.model_validate_json(_json(row))
            if (
                row.slot != context.slot
                or row.human_review is not None
                or row.post_run_check is not None
                or (row.observation_kind == "live_service") != live
                or row.terminal in ("running", "not_started")
            ):
                raise ValueError(
                    "task cannot change slot identity, self-review, or return an unsettled result"
                )
            for ref in row.evidence_references:
                if not ref.startswith(f"trials/{context.slot}/"):
                    raise ValueError("task evidence belongs to another slot")
                _safe_file(root, ref)
            return row
        except BaseException as exc:
            errors[context.slot] = type(exc).__name__
            if isinstance(exc, EnvironmentUnavailable):
                stopped = True
            if isinstance(exc, asyncio.CancelledError):
                stopped = True
                # Evals' AnyIO gather may swallow child-only cancellation and
                # return a None report item. Cancel the batch owner instead;
                # existing lifecycle teardown/finally preserve every slot.
                if not batch_task.cancelling():
                    batch_task.cancel()
            raise

    with CodingRunFileLock(root):
        save_progress()
        write_text_atomic(
            root / "manifest.json",
            json.dumps(
                {
                    "purpose": purpose,
                    "batch_id": batch_id,
                    "slots": slots,
                    "max_concurrency": max_concurrency,
                    "pydantic_evals_version": version("pydantic-evals"),
                    (
                        "effective_configuration"
                        if live
                        else "target_configuration_not_executed"
                    ): baseline_execution_config().model_dump(mode="json"),
                    "case_candidates": [
                        c.model_dump(mode="json")
                        for c in (
                            simple_task_candidates() if pair else task_candidates()
                        )
                        if c.task_id in {slot.split("/")[0] for slot in slots}
                    ],
                    "runner_sha256": hashlib.sha256(
                        await asyncio.to_thread(Path(__file__).read_bytes)
                    ).hexdigest(),
                    "pilot": manifest_extra,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        try:
            for offset in range(0, len(slots), 4):
                if stopped:
                    break
                dataset = Dataset(
                    name=f"offline-block-{offset // 4 + 1}",
                    cases=[
                        Case(name=slot, inputs=_context(root, slot))
                        for slot in slots[offset : offset + 4]
                    ],
                    report_evaluators=[PlannedSummary()],
                )
                framework_report = await dataset.evaluate(
                    invoke,
                    max_concurrency=max_concurrency,
                    repeat=1,
                    retry_task=None,
                    retry_evaluators=None,
                    lifecycle=Lifecycle,
                    progress=False,
                )
                if framework_report.report_evaluator_failures:
                    raise RuntimeError(
                        "offline report evaluator failed; preserve original trial results"
                    )
                write_text_atomic(
                    root / f"block-{offset // 4 + 1}-analyses.json",
                    json.dumps(
                        [a.model_dump(mode="json") for a in framework_report.analyses],
                        indent=2,
                    ),
                )
            status = "stopped" if stopped else "finished"
        except BaseException:
            status = "interrupted"
            raise
        finally:
            for slot, row in tuple(records.items()):
                if row.terminal == "running":
                    row = row.model_copy(
                        update={
                            "terminal": "interrupted",
                            "cause": "unknown",
                            "error_type": "BatchInterrupted",
                        }
                    )
                    write_text_atomic(
                        _context(root, slot).state_dir / "trial-result.json", _json(row)
                    )
                    records[slot] = row
            save_progress()
            _write_summary(root, snapshot())
    return _summary(root, snapshot())


async def run_offline_batch(
    root: Path,
    task: Callable[[TrialContext], Awaitable[TaskTrialReview]],
    *,
    max_concurrency: int = 1,
) -> dict:
    """保留固定十二槽位的离线入口；不会自行创建模型或运行进程。"""
    return await _run_batch(root, task, max_concurrency=max_concurrency)


async def demo_fixture(context: TrialContext) -> TaskTrialReview:
    evidence = context.state_dir / "fixture-evidence.json"
    write_text_atomic(
        evidence,
        json.dumps(
            {
                "fixture": True,
                "slot": context.slot,
                "note": "No model, tools or Abaqus executed",
            }
        ),
    )
    await asyncio.sleep(0.01)
    if context.slot.endswith("/r2") and context.slot.startswith("missing-input"):
        raise ProviderFixtureTimeout("synthetic timeout")
    return TaskTrialReview(
        slot=context.slot,
        terminal="completed",
        cause="none",
        artifact_acceptance="pass",
        execution_acceptance="pass",
        numeric_acceptance="pass",
        interaction_acceptance="pass",
        authority_acceptance="pass",
        evidence_references=(f"trials/{context.slot}/state/fixture-evidence.json",),
        logical_requests=0,
        http_attempts=0,
        input_tokens=0,
        output_tokens=0,
        runtime_calls=0,
        solver_calls=0,
        host_oracle_calls=0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("demo", "summarize", "review"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--max-concurrency", type=int, choices=(1, 2), default=1)
    parser.add_argument("--slot")
    parser.add_argument("--review-file", type=Path)
    args = parser.parse_args()
    if args.mode == "review":
        if args.slot is None or args.review_file is None:
            parser.error(
                "review requires --slot and --review-file (fixture review JSON)"
            )
        result = record_review(
            args.root,
            args.slot,
            HumanReview.model_validate_json(args.review_file.read_bytes()),
        )
    elif args.mode == "demo":
        result = asyncio.run(
            run_offline_batch(
                args.root, demo_fixture, max_concurrency=args.max_concurrency
            )
        )
    else:
        result = summarize_batch(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
