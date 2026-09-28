"""CLI与Web共用的产品装配；不拥有HTTP或Console交互。"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from pydantic import SecretStr
from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex

from .agent import create_abaqus_coding_agent
from .api_lookup import bind_api_catalog, load_product_api_catalog
from .clarification import ClarificationProvider
from .context_policy import validate_filesystem_skill_bindings
from .contracts import CodingRunRequest
from .dependencies import CodingAgentDependencies
from .domain_knowledge import (
    DOMAIN_KNOWLEDGE_ALLOWED_PATTERNS,
    materialize_domain_knowledge_bundle,
)
from .execution_verifier import ExecutionEvidenceVerification
from .launch import LaunchManifest
from .model import DeepSeekGateway
from .model_connections import ModelClient
from .model_connections import create_model as create_deepseek_chat_model
from .progress import ProgressObserver
from .response_stream import ModelResponseControl, RunCancellationControl
from .resume import create_step_store
from .service import CodingRunService, ProgressSink
from .static_preflight import (
    AbqpyBasedPyrightPreflight,
    PreparedAbqpySource,
    prepare_static_preflight_source,
)
from .verification_port import CodingRunVerifier
from .workspace import WorkspaceSnapshot

ModelFactory = Callable[..., tuple[Model, ModelClient]]
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
    run_control: RunCancellationControl | None = None,
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
    if launch.schema_version != "coding-run-launch-v10":
        raise ValueError("pre-release CodingRun launch schema is not supported")
    validate_filesystem_skill_bindings(
        launch.skill_library,
        launch.capability_definitions,
    )
    if api_catalog is None:
        assert launch.api_catalog_dir is not None
        api_catalog = load_product_api_catalog(
            launch.api_catalog_dir,
        )
    if request.api_catalog != bind_api_catalog(api_catalog):
        raise ValueError("API catalog does not match the persisted request binding")
    if static_source is None:
        static_source = prepare_static_preflight_source(
            mode=launch.static_preflight_mode,
            wheel=launch.abqpy_static_wheel,
            cache_dir=launch.static_preflight_cache_dir,
            workspace=request.workspace,
        )
    if (static_source is not None) != (launch.static_preflight_mode == "on"):
        raise ValueError("static preflight source differs from the launch mode")
    if request.static_preflight != (static_source.binding() if static_source else None):
        raise ValueError(
            "static preflight source does not match the persisted request binding"
        )
    with materialize_domain_knowledge_bundle(
        request.state_dir, launch.domain_knowledge
    ) as knowledge:
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
            native_model = model
            while isinstance(native_model, WrapperModel):
                native_model = native_model.wrapped
            stream_supported = (
                type(native_model).request_stream is not Model.request_stream
            )
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
                knowledge_resources_dir=knowledge.root if knowledge else None,
                knowledge_allowed_patterns=DOMAIN_KNOWLEDGE_ALLOWED_PATTERNS,
                response_enabled=launch.allows_response(),
                product_coding_tools=(
                    launch.question_protocol == "ask_user_v1"
                    and launch.allows_response()
                ),
                context_policy_enabled=launch.schema_version == "coding-run-launch-v10",
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
                stream_model=stream_model and stream_supported,
                response_control=response_control,
                run_control=run_control,
                resume=resume,
                step_store=store,
                launch_manifest_json=(
                    None if resume else launch.model_dump_json(indent=2)
                ),
                expected_amendment_sequence=expected_amendment_sequence,
                continuation_id=continuation_id,
                continuation_prompt=continuation_prompt,
                question_protocol=launch.question_protocol or "legacy_decision",
                response_enabled=launch.allows_response(),
            )
        finally:
            await client.close()
