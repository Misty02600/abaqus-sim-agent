"""固定力学测评的提交留证与有限反馈；仍由同一个 CodingRun 执行修复。"""

import json
import shutil
import traceback
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    CodingRunUsage,
    ReadyForVerificationDecision,
)
from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.coding.workspace import WorkspaceResolver, WorkspaceSnapshot
from abaqus_sim_agent.verification.public_mechanics_run import (
    PublicMechanicsAttempt,
    PublicMechanicsRunVerifier,
)

SUBMISSION_POLICY = "first_submission_bounded_feedback_v1"
MAX_SUBMISSIONS = 3


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int
    decision: ReadyForVerificationDecision
    usage: CodingRunUsage
    workspace: WorkspaceSnapshot


def load_submissions(
    state: Path,
) -> list[tuple[Submission, PublicMechanicsAttempt | None]]:
    records = []
    events_path = state / "events.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_bytes().splitlines()]
        if events_path.exists()
        else []
    )
    verifications = [
        event["data"] for event in events if event.get("event") == "verification"
    ]
    if (
        sum(event.get("event") == "verification_feedback" for event in events)
        > MAX_SUBMISSIONS - 1
    ):
        raise ValueError("feedback allowance exceeded")
    for number, folder in enumerate(sorted((state / "submissions").glob("*")), 1):
        submission = Submission.model_validate_json(
            (folder / "submission.json").read_bytes()
        )
        if (
            folder.name != f"{number:03}"
            or submission.number != number
            or number > MAX_SUBMISSIONS
        ):
            raise ValueError("invalid submission sequence")
        result_path = folder / "result.json"
        result = (
            PublicMechanicsAttempt.model_validate_json(result_path.read_bytes())
            if result_path.exists()
            else None
        )
        if result is not None:
            if number > len(verifications):
                result = None  # 评分文件已写出但宿主验收事件未落盘，不推断提交已完成。
            elif result.model_dump(
                exclude={"feedback"}, mode="json"
            ) != PublicMechanicsAttempt.model_validate_json(
                json.dumps(verifications[number - 1])
            ).model_dump(exclude={"feedback"}, mode="json"):
                raise ValueError(
                    "submission score differs from host verification event"
                )
        if (
            result is not None
            and WorkspaceResolver().snapshot(folder / "workspace")
            != submission.workspace
        ):
            raise ValueError("saved submission artifacts changed")
        if records:
            previous, previous_result = records[-1]
            if (
                previous_result is None
                or previous_result.passed
                or previous_result.terminal_failure_stage is not None
            ):
                raise ValueError("submission follows an unsettled or terminal score")
            for field in (
                "model_requests",
                "abaqus_calls",
                "solver_calls",
                "wall_time_seconds",
            ):
                if getattr(submission.usage, field) < getattr(previous.usage, field):
                    raise ValueError("submission usage decreased")
        records.append((submission, result))
    if len(verifications) > len(records):
        raise ValueError("host verification has no captured submission")
    return records


def model_feedback(
    attempt: PublicMechanicsAttempt, metric_names: tuple[str, ...]
) -> str:
    """只输出受控检查名称；原始诊断、测量值和异常文本不进入模型消息。"""
    if attempt.passed:
        return "Independent verification passed."
    if attempt.failure_stage == "evaluation_error":
        return "Evaluation stopped because independent scoring failed."
    checks = []
    for finding in attempt.findings:
        matched = False
        for name in (
            "build_model.py",
            "extract_results.py",
            "model.cae",
            "results.json",
            "report.md",
        ):
            if finding == f"missing/stale artifact {name}":
                checks.append(f"delivery {name}")
                matched = True
        for role in ("primary", "coarse"):
            for detail, label in (
                (
                    f"{role} ODB declaration must identify exactly one ODB",
                    f"{role} ODB declaration",
                ),
                (
                    f"No current solver provenance for {role} result",
                    f"solver provenance for {role} result",
                ),
                (
                    f"No CAE provenance for {role} input before solver",
                    f"CAE provenance for {role} input",
                ),
                (
                    f"{role} solver status does not confirm completion",
                    f"solver completion for {role} result",
                ),
            ):
                if finding == detail:
                    checks.append(label)
                    matched = True
        for detail, label in (
            (
                "No CAE provenance for current model.cae",
                "CAE provenance",
            ),
            (
                "No extraction provenance after current solver outputs",
                "extraction provenance",
            ),
        ):
            if finding == detail:
                checks.append(label)
                matched = True
        for key in metric_names:
            if finding.startswith(f"{key}:"):
                checks.append(f"metric {key}")
                matched = True
            elif finding in {
                f"refinement change exceeds 1% for {key}",
                f"refinement values missing/nonfinite for {key}",
            }:
                checks.append(f"mesh convergence for {key}")
                matched = True
        if not matched:
            # 自由文本中可能混有参考值，不能按数字或关键词删改后回传。
            checks.append(
                "delivery and execution evidence"
                if attempt.failure_stage == "provenance"
                else "submitted result and delivery checks"
            )
    if not checks:
        checks.append(
            "results.json format"
            if attempt.failure_stage == "results"
            else "delivery and execution evidence"
        )
    return "Failed checks: " + "; ".join(dict.fromkeys(checks)) + "."


class MechanicsSubmissionVerifier:
    def __init__(self, request: CodingRunRequest, verifier: PublicMechanicsRunVerifier):
        self.request = request
        self.verifier = verifier
        self.current: Path | None = None
        self.records = load_submissions(request.state_dir)
        if self.records:
            last = self.records[-1][1]
            if last is None or last.passed or last.terminal_failure_stage is not None:
                raise ValueError(
                    "submission is unsettled or terminal; cannot restart scoring or repair"
                )
            events = [
                json.loads(line)
                for line in (request.state_dir / "events.jsonl")
                .read_bytes()
                .splitlines()
            ]
            if sum(e.get("event") == "verification_feedback" for e in events) != len(
                self.records
            ):
                raise ValueError("submission feedback checkpoint is unsettled")

    def capture(
        self, decision: ReadyForVerificationDecision, usage: CodingRunUsage
    ) -> None:
        """评分前保存实际文件和累计用量；已存在的提交目录永不覆盖。

        Note:
            复制中断会留下未完成记录并阻止续跑，不根据现有工作区补造首次交付。
        """
        number = len(self.records) + 1
        if self.current is not None or number > MAX_SUBMISSIONS:
            raise ValueError("submission allowance exhausted or scoring is pending")
        folder = self.request.state_dir / "submissions" / f"{number:03}"
        folder.mkdir(parents=True, exist_ok=False)
        resolver = WorkspaceResolver(captured_cwd=self.request.workspace)
        snapshot = resolver.snapshot(self.request.workspace)
        submission = Submission(
            number=number, decision=decision, usage=usage, workspace=snapshot
        )
        write_text_atomic(
            folder / "submission.json", submission.model_dump_json(indent=2)
        )
        saved = folder / "workspace"
        saved.mkdir()
        for item in snapshot.files:
            destination = saved / item.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.request.workspace / item.relative_path, destination)
        if resolver.snapshot(saved) != snapshot:
            raise ValueError("workspace changed while capturing submission")
        self.records.append((submission, None))
        self.current = folder

    async def verify(
        self, request, runner, initial_snapshot, executions, decision=None
    ) -> PublicMechanicsAttempt:
        if self.current is None:
            raise ValueError("submission must be captured before independent scoring")
        try:
            attempt = await self.verifier.verify(
                request, runner, initial_snapshot, executions, decision
            )
        except Exception as exc:
            # 完整诊断只落在宿主目录，绝不作为模型修复线索。
            write_text_atomic(
                self.current / "error.json",
                json.dumps(
                    {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                ),
            )
            attempt = PublicMechanicsAttempt(
                passed=False,
                feedback="Independent scoring raised an exception.",
                failure_stage="evaluation_error",
            )
        terminal = None
        if attempt.failure_stage == "evaluation_error":
            terminal = "evaluation_error"
        elif not attempt.passed and len(self.records) == MAX_SUBMISSIONS:
            terminal = "submission_limit"
        attempt = attempt.model_copy(update={"terminal_failure_stage": terminal})
        write_text_atomic(
            self.current / "result.json", attempt.model_dump_json(indent=2)
        )
        self.records[-1] = (self.records[-1][0], attempt)
        self.current = None
        feedback = model_feedback(attempt, tuple(self.verifier.case.metrics))
        if terminal == "submission_limit":
            feedback += " Submission limit reached."
        return attempt.model_copy(update={"feedback": feedback})
