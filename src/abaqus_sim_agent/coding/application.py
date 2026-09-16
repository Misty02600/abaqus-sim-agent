"""独立于入口形式的普通CodingRun启动/恢复及问答调用。"""

from dataclasses import dataclass
from pathlib import Path

from ..skills import skill_library_path
from .contracts import CodingRunBudget, CodingRunRequest, RuntimeProfile
from .launch import LaunchManifest, apply_launch_amendments
from .model import ModelExecutionConfig
from .response_stream import StoppedReply, load_stopped_replies
from .resume import load_effective_run_for_resume, load_original_request_for_resume
from .user_questions import UserAnswerInput, UserQuestion, submit_answer
from .workspace import WorkspaceResolver, WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: str
    text: str
    submitted_after_turn: int


@dataclass(frozen=True)
class PreparedCodingRun:
    request: CodingRunRequest
    initial_snapshot: WorkspaceSnapshot
    launch: LaunchManifest
    resume: bool = False
    amendment_sequence: int | None = None
    task_revision: int | None = None
    initial_goal: str | None = None
    conversation_messages: tuple[ConversationMessage, ...] = ()
    stopped_replies: tuple[StoppedReply, ...] = ()


def prepare_new_run(
    *,
    run_id: str,
    goal: str,
    workspace: Path,
    state_dir: Path,
    execution: ModelExecutionConfig,
    budget: CodingRunBudget,
    runtime: RuntimeProfile,
) -> PreparedCodingRun:
    resolved = WorkspaceResolver().resolve_interactive(
        run_id=run_id, workspace=workspace, state_dir=state_dir
    )
    request = CodingRunRequest(
        run_id=run_id,
        goal=goal,
        workspace=resolved.workspace,
        state_dir=resolved.state_dir,
        runtime=runtime,
        budget=budget,
    )
    launch = LaunchManifest(
        schema_version="coding-run-launch-v5",
        question_protocol="ask_user_v1",
        skill_library=skill_library_path().resolve(),
        **execution.model_dump(),
    )
    return PreparedCodingRun(request, resolved.initial_snapshot, launch)


def prepare_resume(state_dir: Path) -> PreparedCodingRun:
    effective = load_effective_run_for_resume(state_dir)
    launch = LaunchManifest.model_validate_json(
        (state_dir / "launch.json").read_bytes()
    )
    return PreparedCodingRun(
        effective.request,
        effective.initial_snapshot,
        apply_launch_amendments(launch, effective.ledger),
        True,
        effective.amendment_sequence,
        effective.task_revision,
        load_original_request_for_resume(state_dir).goal,
        tuple(
            ConversationMessage(
                message_id=m.message_id,
                text=m.goal,
                submitted_after_turn=m.submitted_after_turn,
            )
            for m in effective.ledger.amendments
            if m.message_id is not None
            and m.goal is not None
            and m.submitted_after_turn is not None
        ),
        load_stopped_replies(state_dir),
    )


async def answer_question(
    state_dir: Path, answer: UserAnswerInput, *, expected_task_revision: int
) -> UserQuestion:
    return await submit_answer(
        state_dir,
        answer,
        source="web",
        expected_task_revision=expected_task_revision,
    )
