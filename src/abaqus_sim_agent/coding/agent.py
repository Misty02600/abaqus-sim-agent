from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic_ai import Agent, DeferredToolRequests, RunContext, ToolOutput
from pydantic_ai.models import Model
from pydantic_ai_harness import Coder, Skills, StepPersistence, ToolGuardrail
from pydantic_ai_harness.guardrails import GuardrailResult, ToolCallInfo
from pydantic_ai_harness.planning import Planning
from pydantic_ai_harness.repo_context import RepoContext
from pydantic_ai_harness.shell import Shell
from pydantic_ai_harness.step_persistence import StepStore

from abaqus_sim_agent.coding.api_lookup import (
    ApiCatalogMode as _ApiCatalogMode,
)
from abaqus_sim_agent.coding.api_lookup import (
    register_api_discovery_tool as _register_api_discovery_tool,
)
from abaqus_sim_agent.coding.api_lookup import (
    register_api_lookup_tool as _register_api_lookup_tool,
)
from abaqus_sim_agent.coding.api_lookup import (
    resolve_api_catalog_mode as _resolve_api_catalog_mode,
)
from abaqus_sim_agent.coding.concept_lookup import (
    ConceptRetrievalMode as _ConceptRetrievalMode,
)
from abaqus_sim_agent.coding.concept_lookup import (
    register_document_lookup_tool as _register_document_lookup_tool,
)
from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    FailedDecision,
    NeedsInputDecision,
    ReadyForVerificationDecision,
)
from abaqus_sim_agent.coding.dependencies import (
    CodingAgentDependencies as _CodingAgentDependencies,
)
from abaqus_sim_agent.coding.diagnostic_retrieval import (
    DiagnosticRetrievalMode as _DiagnosticRetrievalMode,
)
from abaqus_sim_agent.coding.document_policy_tools import (
    POLICY_INSTRUCTIONS,
    register_document_policy_tools,
)
from abaqus_sim_agent.coding.edit_scope import ReferenceEditCapability
from abaqus_sim_agent.coding.harness_compat import (
    apply_harness_windows_jsonl_append_patch,
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
)
from abaqus_sim_agent.coding.instructions import (
    build_instructions as _build_instructions,
)
from abaqus_sim_agent.coding.knowledge_files import (
    KNOWLEDGE_INSTRUCTIONS,
    knowledge_toolset,
)
from abaqus_sim_agent.coding.model import ConfiguredModel, ModelExecutionConfig
from abaqus_sim_agent.coding.probe import ProbeProtocolMode as _ProbeProtocolMode
from abaqus_sim_agent.coding.resume import create_step_store as _create_step_store
from abaqus_sim_agent.coding.runtime_tools import (
    register_runtime_tools as _register_runtime_tools,
)
from abaqus_sim_agent.coding.user_questions import (
    CodingAgentOutput,
    QuestionProtocol,
    UserQuestionCapability,
    register_ask_user,
)

CODER_GENERAL_SHELL_POLICY_VERSION = "coder-general-shell-disabled-v1"

_LINEAR_STRUCTURAL_FEA_FOUNDATION_SKILLS = (
    "linear-structural-fea-formulation",
    "linear-structural-fea-modeling",
    "linear-structural-fea-discretization",
    "linear-structural-fea-postprocess",
    "linear-structural-fea-verification",
)

__all__ = ["CODER_GENERAL_SHELL_POLICY_VERSION", "create_abaqus_coding_agent"]


def _production_python_syntax_guard(
    ctx: RunContext[_CodingAgentDependencies],
    call: ToolCallInfo,
) -> GuardrailResult:
    gate = ctx.deps.syntax_gate
    if gate is None:
        return GuardrailResult.block(
            "Python syntax gate is required by this ACI profile but unavailable"
        )
    return gate.guard(call)


def _immutable_workspace_guard(
    paths: tuple[str, ...],
) -> Callable[
    [RunContext[_CodingAgentDependencies], ToolCallInfo],
    GuardrailResult,
]:
    protected = {
        PurePosixPath(path.replace("\\", "/")).as_posix().casefold() for path in paths
    }

    def guard(
        ctx: RunContext[_CodingAgentDependencies],
        call: ToolCallInfo,
    ) -> GuardrailResult:
        if call.name not in {"write_file", "edit_file"}:
            return GuardrailResult.allow()
        raw_path = call.args.get("path")
        if not isinstance(raw_path, str):
            return GuardrailResult.allow()
        workspace = ctx.deps.request.workspace
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        resolved = candidate.resolve(strict=False)
        if resolved != workspace and not resolved.is_relative_to(workspace):
            return GuardrailResult.allow()
        normalized = resolved.relative_to(workspace).as_posix().casefold()
        if normalized not in protected:
            return GuardrailResult.allow()
        return GuardrailResult.block(
            f"Workspace path {raw_path!r} is immutable for this task; repair its "
            "authoring source instead."
        )

    return guard


def create_abaqus_coding_agent(
    model: Model,
    request: CodingRunRequest,
    *,
    skill_library: Path | None = None,
    api_catalog_enabled: bool = False,
    api_catalog_mode: _ApiCatalogMode | None = None,
    concept_retrieval_mode: _ConceptRetrievalMode = "off",
    diagnostic_retrieval_mode: _DiagnosticRetrievalMode = "off",
    diagnostic_retrieval_instructions_enabled: bool | None = None,
    immutable_workspace_paths: tuple[str, ...] = (),
    probe_protocol_mode: _ProbeProtocolMode = "off",
    probe_target_scripts: tuple[str, ...] | None = None,
    planning_enabled: bool = True,
    task_instructions: str | None = None,
    step_store: StepStore | None = None,
    execution_config: ModelExecutionConfig | None = None,
    question_protocol: QuestionProtocol = "legacy_decision",
    foundation_skill_profile: Literal["off", "linear_structural_fea_v1"] = "off",
    knowledge_resources_dir: Path | None = None,
) -> Agent[_CodingAgentDependencies, CodingAgentOutput]:
    """组装只在指定工作区活动的 Abaqus 2024 Coding Agent。

    Args:
        model: Pydantic AI 使用的模型实现。
        request: 已解析工作区、运行时和预算合同。
        skill_library: 可选的领域 Skill 库目录。
        api_catalog_enabled: 兼容旧调用方的开关；未显式指定模式时，
            ``True`` 映射为 ``full``，``False`` 映射为 ``off``。
        api_catalog_mode: Catalog 注入模式。显式值优先于兼容开关；
            ``automatic_verified_only`` 仅在运行时失败后注入已验证短注释。
            ``automatic_exact_signature`` 只在精确 traceback 唯一命中时
            注入紧凑签名，并附带适用的已验证短注释。
        concept_retrieval_mode: ``tool`` 时显式暴露官方建模文档查询；
            默认 ``off``，不改变已有 CodingRun 的提示词和工具集合。
        diagnostic_retrieval_mode: ``advisory`` 时在合格Solver诊断的同一
            tool return中附加固定RRF文档证据；默认 ``off``。
        diagnostic_retrieval_instructions_enabled: 可让对照评测在两臂保持
            完全相同的advisory使用说明；产品默认跟随mode。
        immutable_workspace_paths: 任务级不可由文件工具写入或编辑的生成物
            或保留文件；runtime产生这些文件不受影响。
        probe_protocol_mode: ``typed`` 时要求诊断运行提交结构化结论，并在
            validated 结论采用到 production 脚本前阻断后续诊断与 Solver。
        probe_target_scripts: typed probe 可以采用到的 production Python
            脚本；``None`` 表示不增加任务级 allowlist。
        planning_enabled: 是否向模型暴露 ``write_plan`` 工具。
        task_instructions: 可选的任务专用缺项和完成条件；未提供时使用开放任务要求。
        execution_config: 可选的宿主有效配置，统一推理、Planning与交互策略；
            未提供时保持历史调用方行为。
        step_store: 可选的 Harness 步骤持久化后端；默认在 state 目录使用
            ``FileStepStore``，与模型可写 workspace 隔离。
        foundation_skill_profile: 显式 ``linear_structural_fea_v1`` 时为受控评测
            暴露五个线性结构基础 deferred Skills；默认 ``off``，保持产品现状。
        knowledge_resources_dir: 显式评测的只读资料目录；调用者负责冻结内容。
            仅新增读取工具，不执行资料；尚无普通 CLI/launch/resume 绑定。

    Returns:
        配置完成、但尚未运行的单 Agent 实例。
    """
    if execution_config is not None:
        planning_enabled = execution_config.planning_enabled
        if request.edit_scope is not None and planning_enabled:
            raise ValueError("pure-edit effective config must disable Planning")
        model = ConfiguredModel(model, execution_config)
    resolved_api_catalog_mode = _resolve_api_catalog_mode(
        api_catalog_enabled=api_catalog_enabled,
        api_catalog_mode=api_catalog_mode,
    )
    bounded_documents = request.document_policy is not None
    if bounded_documents and concept_retrieval_mode != "tool":
        raise ValueError("bounded_v1 requires concept_retrieval_mode=tool")
    if request.edit_scope is not None:
        if probe_protocol_mode != "off" or diagnostic_retrieval_mode != "off":
            raise ValueError(
                "pure edit scope cannot enable runtime probe or diagnostic profiles"
            )
        planning_enabled = False
    if foundation_skill_profile not in {"off", "linear_structural_fea_v1"}:
        raise ValueError("unknown foundation Skill profile")
    if foundation_skill_profile != "off" and skill_library is None:
        raise ValueError("foundation Skill profile requires skill_library")
    apply_harness_windows_newline_hash_patch()
    apply_harness_windows_shell_tree_patch()
    apply_harness_windows_jsonl_append_patch()
    # Coder 0.24.0 always creates a Shell capability. Keep its constructor
    # allowlist narrow, then remove the capability fail-closed in _configure_coder.
    coder = Coder[_CodingAgentDependencies](
        request.workspace,
        allowed_commands=("python",),
        subagents=(),
    )
    _configure_coder(
        coder,
        request.workspace,
        planning_enabled=planning_enabled,
    )
    capabilities: list[Any] = [
        coder,
        StepPersistence(store=step_store or _create_step_store(request.state_dir)),
    ]
    if request.edit_scope is not None:
        capabilities.append(ReferenceEditCapability())
    if (
        request.static_preflight is not None
        and request.static_preflight.mode == "enforced_verified"
    ):
        capabilities.append(
            ToolGuardrail[_CodingAgentDependencies](
                guard=_production_python_syntax_guard,
                tools=("write_file", "edit_file"),
            )
        )
    if immutable_workspace_paths:
        capabilities.append(
            ToolGuardrail[_CodingAgentDependencies](
                guard=_immutable_workspace_guard(immutable_workspace_paths),
                tools=("write_file", "edit_file"),
            )
        )
    if skill_library is not None:
        skill_include = ("abaqus-2024-coding",)
        if foundation_skill_profile == "linear_structural_fea_v1":
            skill_include += _LINEAR_STRUCTURAL_FEA_FOUNDATION_SKILLS
        capabilities.append(
            Skills[_CodingAgentDependencies](
                skill_library,
                include=skill_include,
            )
        )

    outputs: list[Any] = [
        ToolOutput(
            NeedsInputDecision,
            name="final_result_NeedsInputDecision",
            description=(
                "Submit the final host-control decision when a required physical "
                "input is missing, physical intent is materially ambiguous, or "
                "explicit user constraints conflict. This validates the CodingRun "
                "decision, not a workspace artifact. Call this tool instead of "
                "replying in prose."
            ),
        ),
        ToolOutput(
            ReadyForVerificationDecision,
            name="final_result_ReadyForVerificationDecision",
            description=(
                "Submit the final host-control decision after the required "
                "artifacts exist and were inspected. This validates the "
                "CodingRun decision, not any workspace JSON file. Call this "
                "tool instead of replying in prose."
            ),
        ),
        ToolOutput(
            FailedDecision,
            name="final_result_FailedDecision",
            description=(
                "Submit the final host-control decision when evidence shows the "
                "task cannot finish within its contract. This validates the "
                "CodingRun decision, not a workspace artifact. Call this tool "
                "instead of replying in prose."
            ),
        ),
    ]
    if question_protocol == "ask_user_v1":
        if (
            request.document_policy
            or request.edit_scope
            or probe_protocol_mode != "off"
            or concept_retrieval_mode != "off"
            or diagnostic_retrieval_mode != "off"
            or request.static_preflight is not None
        ):
            raise ValueError(
                "ask_user_v1 is not enabled for this legacy explicit profile"
            )
        outputs = [*outputs[1:], DeferredToolRequests]
        capabilities.append(UserQuestionCapability())
    elif question_protocol != "legacy_decision":
        raise ValueError("unknown question protocol")

    agent = Agent[_CodingAgentDependencies, CodingAgentOutput](
        model,
        toolsets=(
            [
                knowledge_toolset(
                    knowledge_resources_dir,
                    workspace=request.workspace,
                    state_dir=request.state_dir,
                )
            ]
            if knowledge_resources_dir is not None
            else ()
        ),
        name="abaqus_2024_coder",
        deps_type=_CodingAgentDependencies,
        output_type=outputs,
        instructions=_build_instructions(
            request,
            api_catalog_enabled=resolved_api_catalog_mode
            in {"full", "full_with_discovery"},
            api_discovery_enabled=resolved_api_catalog_mode == "full_with_discovery",
            concept_retrieval_enabled=concept_retrieval_mode == "tool"
            and not bounded_documents,
            diagnostic_retrieval_enabled=(
                diagnostic_retrieval_mode == "advisory"
                if diagnostic_retrieval_instructions_enabled is None
                else diagnostic_retrieval_instructions_enabled
            ),
            probe_protocol_enabled=probe_protocol_mode == "typed",
            probe_target_scripts=probe_target_scripts,
            task_instructions=task_instructions,
            question_protocol=question_protocol,
            interaction_policy=execution_config.interaction_policy
            if execution_config
            else "legacy",
        )
        + (
            "\n\n" + KNOWLEDGE_INSTRUCTIONS
            if knowledge_resources_dir is not None
            else ""
        )
        + (
            "\n\n"
            + POLICY_INSTRUCTIONS
            + "\nHost document configuration: "
            + request.document_policy.spec.model_dump_json(
                include={"scope", "host_needs"}
            )
            if request.document_policy is not None
            else ""
        ),
        capabilities=capabilities,
        retries=2,
        end_strategy="graceful",
        model_settings=execution_config.model_settings() if execution_config else None,
    )

    if question_protocol == "ask_user_v1":
        register_ask_user(agent)
    if request.edit_scope is None:
        _register_runtime_tools(
            agent,
            probe_protocol_mode=probe_protocol_mode,
            probe_target_scripts=probe_target_scripts,
            api_catalog_mode=resolved_api_catalog_mode,
            diagnostic_retrieval_mode=diagnostic_retrieval_mode,
        )
    if resolved_api_catalog_mode in {"full", "full_with_discovery"}:
        _register_api_lookup_tool(agent)
    if resolved_api_catalog_mode == "full_with_discovery":
        _register_api_discovery_tool(agent)
    if concept_retrieval_mode == "tool":
        if bounded_documents:
            register_document_policy_tools(agent)
        else:
            _register_document_lookup_tool(agent)
    return agent


def _configure_coder(
    coder: Coder[_CodingAgentDependencies],
    workspace: Path,
    *,
    planning_enabled: bool,
) -> None:
    shell_capabilities = [
        capability for capability in coder.capabilities if isinstance(capability, Shell)
    ]
    if len(shell_capabilities) != 1:
        raise RuntimeError(
            "Expected exactly one Harness Shell capability before enforcing "
            "the no-general-shell policy"
        )
    blocked_shell = shell_capabilities[0]
    coder.capabilities = [
        capability
        for capability in coder.capabilities
        if capability is not blocked_shell
    ]

    has_repo_context = any(
        (workspace / name).exists()
        for name in (
            ".git",
            "AGENTS.md",
            "CLAUDE.md",
            ".agents",
            ".claude",
            ".codex",
            ".grok",
        )
    )
    for capability in coder.capabilities:
        if isinstance(capability, RepoContext) and not has_repo_context:
            capability.autoload_instructions = False
            capability.expose_inventory_tool = False
        elif isinstance(capability, Planning):
            capability.tools = ("write_plan",) if planning_enabled else ()
            capability.guidance = (
                "After all required user input is known, use write_plan at most "
                "once for a genuinely multi-stage task. Keep it concise; do not "
                "rewrite or administratively update the plan during repair."
            )
