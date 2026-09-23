"""独立于入口形式的普通CodingRun启动/恢复及问答调用。"""

from dataclasses import dataclass
from pathlib import Path

from ..skills import skill_library_path
from .api_lookup import (
    DEFAULT_PRODUCT_API_CATALOG_MODE,
    ProductApiCatalogMode,
    bind_api_catalog,
    load_product_api_catalog,
)
from .context_policy import (
    CONTEXT_POLICY_VERSION,
    bind_filesystem_skills,
)
from .contracts import (
    CodingRunLimits,
    CodingRunRequest,
    EvaluationToolLimits,
    RuntimeExecutionPolicy,
    RuntimeProfile,
)
from .domain_knowledge import prepare_domain_knowledge_bundle
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
    runtime: RuntimeProfile,
    api_catalog_dir: Path,
    api_catalog_mode: ProductApiCatalogMode = DEFAULT_PRODUCT_API_CATALOG_MODE,
    limits: CodingRunLimits | None = None,
    runtime_policy: RuntimeExecutionPolicy | None = None,
    evaluation_tool_limits: EvaluationToolLimits | None = None,
    knowledge_pack_ids: tuple[str, ...] = (),
    knowledge_packs_root: Path | None = None,
) -> PreparedCodingRun:
    resolved = WorkspaceResolver().resolve_interactive(
        run_id=run_id, workspace=workspace, state_dir=state_dir
    )
    api_catalog = load_product_api_catalog(
        api_catalog_dir,
        workspace=resolved.workspace,
    )
    request = CodingRunRequest(
        run_id=run_id,
        goal=goal,
        workspace=resolved.workspace,
        state_dir=resolved.state_dir,
        runtime=runtime,
        api_catalog=bind_api_catalog(api_catalog),
        limits=limits or CodingRunLimits(),
        runtime_policy=runtime_policy or RuntimeExecutionPolicy(),
        evaluation_tool_limits=evaluation_tool_limits,
    )
    domain_knowledge = prepare_domain_knowledge_bundle(
        knowledge_pack_ids,
        state_dir=resolved.state_dir,
        workspace=resolved.workspace,
        packs_root=knowledge_packs_root,
    )
    skill_library = skill_library_path().resolve()
    launch = LaunchManifest(
        schema_version="coding-run-launch-v10",
        question_protocol="ask_user_v1",
        response_enabled=True,
        domain_knowledge=domain_knowledge,
        skill_library=skill_library,
        capability_definitions=bind_filesystem_skills(
            skill_library,
            ("abaqus-2024-coding",),
        ),
        context_policy_version=CONTEXT_POLICY_VERSION,
        api_catalog_dir=api_catalog_dir.resolve(strict=True),
        api_catalog_mode=api_catalog_mode,
        **execution.model_dump(),
    )
    return PreparedCodingRun(request, resolved.initial_snapshot, launch)


def prepare_resume(state_dir: Path) -> PreparedCodingRun:
    effective = load_effective_run_for_resume(state_dir)
    launch = LaunchManifest.model_validate_json(
        (state_dir / "launch.json").read_bytes()
    )
    if launch.schema_version != "coding-run-launch-v10":
        raise ValueError("pre-release CodingRun launch schema is not supported")
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
