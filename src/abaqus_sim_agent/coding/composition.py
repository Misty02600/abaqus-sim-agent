"""CLI与Web共用的产品装配；不拥有HTTP或Console交互。"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal, cast

from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_ai.models import Model

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex

from .agent import create_abaqus_coding_agent
from .clarification import ClarificationProvider
from .contracts import CodingRunRequest
from .dependencies import CodingAgentDependencies
from .execution_verifier import ExecutionEvidenceVerification
from .launch import LaunchManifest
from .model import DeepSeekGateway, create_deepseek_chat_model
from .progress import ProgressObserver
from .response_stream import ModelResponseControl
from .resume import create_step_store
from .service import CodingRunService, ProgressSink
from .static_preflight import AbqpyBasedPyrightPreflight, PreparedAbqpySource
from .verification_port import CodingRunVerifier
from .workspace import WorkspaceSnapshot

ModelFactory = Callable[..., tuple[Model, AsyncOpenAI]]
RunnerFactory = Callable[..., Abaqus2024Runner]


@asynccontextmanager
async def open_coding_run(
    request: CodingRunRequest,
    initial_snapshot: WorkspaceSnapshot,
    launch: LaunchManifest,
    *,
    api_key: SecretStr,
    verifier: CodingRunVerifier[ExecutionEvidenceVerification],
    clarification_provider: ClarificationProvider,
    resume: bool = False,
    expected_amendment_sequence: int | None = None,
    continuation_id: str | None = None,
    continuation_prompt: str | None = None,
    api_catalog: ApiCatalog | None = None,
    concept_index: AbaqusDocumentIndex | None = None,
    static_source: PreparedAbqpySource | None = None,
    progress_sink: ProgressSink | None = None,
    observer: ProgressObserver | None = None,
    stream_model: bool = False,
    response_control: ModelResponseControl | None = None,
    model_factory: ModelFactory = create_deepseek_chat_model,
    runner_factory: RunnerFactory = Abaqus2024Runner,
) -> AsyncIterator[CodingRunService[ExecutionEvidenceVerification]]:
    """在调用方事件循环内装配并释放client；所有退出路径都关闭client。"""
    if (continuation_id is None) != (continuation_prompt is None):
        raise ValueError("continuation id and prompt must be provided together")
    if continuation_prompt is not None and not continuation_prompt.strip():
        raise ValueError("continuation prompt must not be blank")
    if continuation_id is not None and not resume:
        raise ValueError("host continuation requires resume mode")
    execution = launch.effective_execution(pure_edit=request.edit_scope is not None)
    runner = runner_factory(
        request.workspace, request.state_dir, launcher=request.runtime.launcher_path
    )
    model, client = model_factory(
        gateway=launch.model_provider,
        model_name=launch.model_name,
        api_key=api_key,
        http_retries=launch.http_retries,
        timeout_seconds=execution.http_timeout_seconds,
        execution_config=execution,
        conversation_id=request.run_id
        if launch.model_provider is DeepSeekGateway.OPENCODE_GO
        else None,
    )
    try:
        store = create_step_store(request.state_dir)
        agent = create_abaqus_coding_agent(
            model,
            request,
            skill_library=launch.skill_library,
            api_catalog_mode=launch.api_catalog_mode,
            concept_retrieval_mode=launch.concept_retrieval_mode,
            diagnostic_retrieval_mode=launch.diagnostic_retrieval_mode,
            step_store=store,
            execution_config=execution,
            question_protocol=launch.question_protocol or "legacy_decision",
            response_enabled=launch.schema_version == "coding-run-launch-v5",
        )
        dependencies = CodingAgentDependencies(
            request=request,
            runner=runner,
            api_catalog=api_catalog,
            concept_index=concept_index,
            observer=observer,
            static_preflight=AbqpyBasedPyrightPreflight(
                static_source,
                request.state_dir,
                mode=cast(
                    Literal["advisory", "enforced_verified"],
                    launch.static_preflight_mode,
                ),
            )
            if static_source
            else None,
        )
        if request.document_policy is not None:
            from abaqus_sim_agent.evaluation.document_policy_adapter import (
                compose_document_backend,
            )

            dependencies.document_port, dependencies.document_validator = (
                compose_document_backend(
                    request.document_policy,
                    state_dir=request.state_dir,
                    workspace=request.workspace,
                )
            )
        yield CodingRunService(
            agent=agent,
            request=request,
            dependencies=dependencies,
            clarification_provider=clarification_provider,
            verifier=verifier,
            initial_snapshot=initial_snapshot,
            progress_sink=progress_sink,
            observer=observer,
            stream_model=stream_model,
            response_control=response_control,
            resume=resume,
            step_store=store,
            launch_manifest_json=None if resume else launch.model_dump_json(indent=2),
            expected_amendment_sequence=expected_amendment_sequence,
            continuation_id=continuation_id,
            continuation_prompt=continuation_prompt,
            question_protocol=launch.question_protocol or "legacy_decision",
            response_enabled=launch.schema_version == "coding-run-launch-v5",
        )
    finally:
        await client.close()
