from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic_ai import Agent, DeferredToolRequests, RunContext, ToolOutput
from pydantic_ai.capabilities import Capability, Instrumentation
from pydantic_ai.models import Model
from pydantic_ai_harness import Coder, Skills, StepPersistence, ToolGuardrail
from pydantic_ai_harness.compaction import ClearToolResults, WarnNearLimits
from pydantic_ai_harness.filesystem import DEFAULT_TOOL_NAMES, FileSystem
from pydantic_ai_harness.guardrails import GuardrailResult, ToolCallInfo
from pydantic_ai_harness.planning import Planning
from pydantic_ai_harness.repair_tool_arguments import RepairToolArguments
from pydantic_ai_harness.repo_context import RepoContext
from pydantic_ai_harness.shell import (
    LLM_API_KEY_ENV_PATTERNS,
    RUN_SCOPED_TOOL_NAMES,
    Shell,
)
from pydantic_ai_harness.step_persistence import StepStore
from pydantic_ai_harness.tool_output_limits import ToolOutputLimits

from abaqus_sim_agent.coding.api_lookup import (
    ApiCatalogMode as _ApiCatalogMode,
)
from abaqus_sim_agent.coding.api_lookup import (
    register_api_discovery_tool as _register_api_discovery_tool,
)
from abaqus_sim_agent.coding.api_lookup import (
    register_api_lookup_tool as _register_api_lookup_tool,
)
from abaqus_sim_agent.coding.concept_lookup import (
    ConceptRetrievalMode as _ConceptRetrievalMode,
)
from abaqus_sim_agent.coding.concept_lookup import (
    register_document_lookup_tool as _register_document_lookup_tool,
)
from abaqus_sim_agent.coding.context_policy import build_context_policy
from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    FailedDecision,
    NeedsInputDecision,
    ReadyForVerificationDecision,
    ResponseDecision,
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
from abaqus_sim_agent.coding.text_files import AbaqusTextFileSystem
from abaqus_sim_agent.coding.tool_selection import SelectedTools, resolve_tool_names
from abaqus_sim_agent.coding.user_questions import (
    CodingAgentOutput,
    QuestionProtocol,
    UserQuestionCapability,
    register_ask_user,
)
from abaqus_sim_agent.execution.environment import PROVIDER_SECRET_PATTERNS
from abaqus_sim_agent.telemetry import (
    TELEMETRY_CREDENTIAL_ENV_PATTERNS,
    instrumentation_settings,
)

CODER_GENERAL_SHELL_POLICY_VERSION = "coder-general-shell-disabled-v1"
PRODUCT_CODER_SHELL_POLICY_VERSION = "coder-shell-abaqus-launcher-denied-v1"

_ABAQUS_LAUNCHER_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9_.-])"
    r"(?:abaqus(?:20\d{2})?|abq(?:20\d{2})?(?:hf\d+)?)"
    r"(?:\.(?:bat|cmd|exe))?"
    r"(?![a-z0-9_.-])"
)

_LINEAR_STRUCTURAL_FEA_FOUNDATION_SKILLS = (
    "linear-structural-fea-formulation",
    "linear-structural-fea-modeling",
    "linear-structural-fea-discretization",
    "linear-structural-fea-postprocess",
    "linear-structural-fea-verification",
)

_STRUCTURAL_MECHANICS_PACK_SKILLS = {
    "hyperelasticity": (
        "hyperelastic-fea",
        "hyperelastic-calibration",
    ),
    "metal-plasticity": ("plasticity-fea",),
    "geometric-nonlinearity": ("geometric-nonlinear-fea",),
    "structural-dynamics": (
        "structural-dynamics-fea",
        "modal-dynamics-fea",
    ),
    "explicit-dynamics": (
        "explicit-dynamics-fea",
        "quasi-static-explicit-fea",
    ),
    "contact": (
        "contact-fea",
        "contact-diagnostics-fea",
    ),
    "interface-cohesive": ("interface-cohesive-fea",),
}

_STRUCTURAL_MECHANICS_TOPIC_SKILLS = tuple(
    skill_name
    for pack_skills in _STRUCTURAL_MECHANICS_PACK_SKILLS.values()
    for skill_name in pack_skills
)

__all__ = [
    "CODER_GENERAL_SHELL_POLICY_VERSION",
    "PRODUCT_CODER_SHELL_POLICY_VERSION",
    "create_abaqus_coding_agent",
]


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


def _abaqus_launcher_shell_guard(
    ctx: RunContext[_CodingAgentDependencies],
    call: ToolCallInfo,
) -> GuardrailResult:
    if call.name not in {"run_command", "start_command"}:
        return GuardrailResult.allow()
    command = call.args.get("command")
    if not isinstance(command, str):
        return GuardrailResult.allow()
    normalized = command.replace("\\", "/").casefold()
    launcher = ctx.deps.request.runtime.launcher_path.as_posix().casefold()
    if launcher not in normalized and _ABAQUS_LAUNCHER_PATTERN.search(command) is None:
        return GuardrailResult.allow()
    return GuardrailResult.block(
        "Abaqus launchers are not available through the general shell; use "
        "run_cae_script, solver_job, or run_abaqus_python."
    )


def create_abaqus_coding_agent(
    model: Model,
    request: CodingRunRequest,
    *,
    skill_library: Path | None = None,
    api_catalog_mode: _ApiCatalogMode = "off",
    tools: tuple[str, ...] | None = None,
    concept_retrieval_mode: _ConceptRetrievalMode = "off",
    diagnostic_retrieval_mode: _DiagnosticRetrievalMode = "off",
    diagnostic_retrieval_instructions_enabled: bool | None = None,
    immutable_workspace_paths: tuple[str, ...] = (),
    probe_protocol_mode: _ProbeProtocolMode = "off",
    probe_target_scripts: tuple[str, ...] | None = None,
    planning_enabled: bool = False,
    task_instructions: str | None = None,
    step_store: StepStore | None = None,
    execution_config: ModelExecutionConfig | None = None,
    question_protocol: QuestionProtocol = "legacy_decision",
    foundation_skill_profile: Literal["off", "linear_structural_fea_v1"] = "off",
    structural_topic_skill_profile: Literal["off", "structural_mechanics_v1"] = "off",
    knowledge_resources_dir: Path | None = None,
    knowledge_allowed_patterns: tuple[str, ...] = ("*.md", "*.py"),
    response_enabled: bool = False,
    product_coding_tools: bool = False,
    context_policy_enabled: bool = False,
) -> Agent[_CodingAgentDependencies, CodingAgentOutput]:
    """组装只在指定工作区活动的 Abaqus 2024 Coding Agent。

    Args:
        model: Pydantic AI 使用的模型实现。
        request: 已解析工作区、运行时和预算合同。
        skill_library: 可选的领域 Skill 库目录。
        api_catalog_mode: 显式评测沿用的Catalog合同；普通产品由tools控制可见性，报错提示固定开启。
            ``automatic_verified_only`` 仅在运行时失败后注入已验证短注释。
            ``automatic_exact_signature`` 只在精确 traceback 唯一命中时
            注入紧凑签名，并附带适用的已验证短注释。
        tools: 普通产品launch保存的实际工具集合；提供时固定保留报错后的Catalog提示。
            未提供时仅供显式评测沿用原来的profile合同。
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
        planning_enabled: 是否向模型暴露 Harness Planning 工具。
        task_instructions: 可选的任务专用缺项和完成条件；未提供时使用开放任务要求。
        execution_config: 可选的宿主有效配置，统一推理、Planning与交互策略；
            未提供时保持历史调用方行为。
        step_store: 可选的 Harness 步骤持久化后端；默认在 state 目录使用
            ``FileStepStore``，与模型可写 workspace 隔离。
        foundation_skill_profile: 显式 ``linear_structural_fea_v1`` 时为受控评测
            暴露五个线性结构基础 deferred Skills；默认 ``off``，保持产品现状。
        structural_topic_skill_profile: 显式 ``structural_mechanics_v1`` 时暴露
            超弹性、塑性、几何非线性、结构动力学、Explicit 和接触六个专题 Skill；
            默认 ``off``，不改变普通产品行为。
        knowledge_resources_dir: 宿主准备的只读资料目录；仅新增读取工具，不执行资料。
            产品调用方传入 run 绑定快照的临时物化副本，显式评测调用方负责自行冻结内容。
        knowledge_allowed_patterns: 只读资料工具允许访问的文本文件 glob。
        response_enabled: 新普通会话是否允许无文件的文字答复完成；固定任务及旧会话默认关闭。
        product_coding_tools: 是否启用普通产品的受限 Shell 与后台 Solver 工具。
        context_policy_enabled: 是否启用按实际模型窗口绑定的分层上下文管理。

    Returns:
        配置完成、但尚未运行的单 Agent 实例。
    """
    if execution_config is not None:
        planning_enabled = execution_config.planning_enabled
        if request.edit_scope is not None and planning_enabled:
            raise ValueError("pure-edit effective config must disable Planning")
        model = ConfiguredModel(model, execution_config)
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
    if structural_topic_skill_profile not in {"off", "structural_mechanics_v1"}:
        raise ValueError("unknown structural topic Skill profile")
    if (
        foundation_skill_profile != "off" or structural_topic_skill_profile != "off"
    ) and skill_library is None:
        raise ValueError("Skill profiles require skill_library")
    apply_harness_windows_newline_hash_patch()
    apply_harness_windows_shell_tree_patch()
    apply_harness_windows_jsonl_append_patch()
    product_shell_enabled = product_coding_tools
    if tools is not None:
        selected = resolve_tool_names(
            tools,
            shell=product_shell_enabled,
            runtime=request.edit_scope is None,
            questions=question_protocol == "ask_user_v1",
            knowledge=knowledge_resources_dir is not None,
            documents=concept_retrieval_mode == "tool",
            bounded_documents=bounded_documents,
        )
        if set(selected) != set(tools):
            raise ValueError("saved tools are missing required protocol tools")
        api_catalog_mode = "full"
    if product_shell_enabled and (
        question_protocol != "ask_user_v1"
        or not response_enabled
        or task_instructions is not None
    ):
        raise ValueError(
            "product coding tools require an open ask_user response session"
        )
    coder = Coder[_CodingAgentDependencies](request.workspace, sub_agents=False)
    coder = _configure_coder(
        coder,
        request.workspace,
        shell_enabled=product_shell_enabled,
        context_policy_enabled=context_policy_enabled,
        native_instructions_enabled=response_enabled,
        tools=tools,
    )
    capabilities: list[Any] = [
        coder,
        StepPersistence(store=step_store or _create_step_store(request.state_dir)),
    ]
    if tools is not None:
        capabilities.append(SelectedTools(tools))
    if execution_config is not None and execution_config.output_truncation_recovery:
        from .output_recovery import OutputTruncationRecovery

        capabilities.append(OutputTruncationRecovery())
    if (trace_settings := instrumentation_settings()) is not None:
        capabilities.append(Instrumentation(settings=trace_settings))
    if planning_enabled:
        planning = Planning[_CodingAgentDependencies]()
        if not product_shell_enabled:
            planning.tools = ("write_plan",)
            planning.guidance = (
                "After all required user input is known, use write_plan at most "
                "once for a genuinely multi-stage task. Keep it concise; do not "
                "rewrite or administratively update the plan during repair."
            )
        capabilities.append(planning)
    if context_policy_enabled:
        capabilities.insert(
            1,
            build_context_policy(
                context_window=execution_config.context_window
                if execution_config
                else None
            ),
        )
    shell_guard_tools = tuple(
        name
        for name in ("run_command", "start_command")
        if tools is None or name in tools
    )
    if product_shell_enabled and shell_guard_tools:
        capabilities.append(
            ToolGuardrail[_CodingAgentDependencies](
                guard=_abaqus_launcher_shell_guard,
                tools=shell_guard_tools,
            )
        )
    if request.edit_scope is not None:
        capabilities.append(ReferenceEditCapability())
    edit_guard_tools = tuple(
        name for name in ("write_file", "edit_file") if tools is None or name in tools
    )
    if immutable_workspace_paths and edit_guard_tools:
        capabilities.append(
            ToolGuardrail[_CodingAgentDependencies](
                guard=_immutable_workspace_guard(immutable_workspace_paths),
                tools=edit_guard_tools,
            )
        )
    if skill_library is not None:
        skill_include = ("abaqus-2024-coding",)
        if foundation_skill_profile == "linear_structural_fea_v1":
            skill_include += _LINEAR_STRUCTURAL_FEA_FOUNDATION_SKILLS
        if structural_topic_skill_profile == "structural_mechanics_v1":
            skill_include += _STRUCTURAL_MECHANICS_TOPIC_SKILLS
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
        ):
            raise ValueError(
                "ask_user_v1 is not enabled for this legacy explicit profile"
            )
        outputs = [*outputs[1:], DeferredToolRequests]
        capabilities.append(UserQuestionCapability())
    elif question_protocol != "legacy_decision":
        raise ValueError("unknown question protocol")

    if response_enabled:
        if question_protocol != "ask_user_v1" or task_instructions is not None:
            raise ValueError(
                "response completion is only available to open ask_user tasks"
            )
        outputs.append(
            ToolOutput(
                ResponseDecision,
                name="final_result_ResponseDecision",
                description="Deliver the complete text answer to a question or explanation request. No file or Abaqus execution is required. Do not use this to claim completion of requested file changes or execution.",
            )
        )

    agent = Agent[_CodingAgentDependencies, CodingAgentOutput](
        model,
        toolsets=(
            [
                knowledge_toolset(
                    knowledge_resources_dir,
                    workspace=request.workspace,
                    state_dir=request.state_dir,
                    allowed_patterns=knowledge_allowed_patterns,
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
            api_catalog_enabled=(
                "lookup_abaqus_api" in tools
                if tools is not None
                else api_catalog_mode in {"full", "full_with_discovery"}
            ),
            api_discovery_enabled=(
                "discover_abaqus_api" in tools
                if tools is not None
                else api_catalog_mode == "full_with_discovery"
            ),
            concept_retrieval_enabled=concept_retrieval_mode == "tool"
            and (tools is None or "search_abaqus_docs" in tools)
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
            response_enabled=response_enabled,
            interaction_policy=execution_config.interaction_policy
            if execution_config
            else "legacy",
            general_shell_enabled=product_shell_enabled
            and (tools is None or any(name in tools for name in RUN_SCOPED_TOOL_NAMES)),
            background_solver_enabled=product_shell_enabled,
            foundation_skill_profile=foundation_skill_profile,
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
            api_catalog_mode=api_catalog_mode,
            diagnostic_retrieval_mode=diagnostic_retrieval_mode,
            background_solver_enabled=product_shell_enabled,
        )
    if api_catalog_mode in {"full", "full_with_discovery"}:
        _register_api_lookup_tool(agent)
    if tools is not None or api_catalog_mode == "full_with_discovery":
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
    shell_enabled: bool,
    context_policy_enabled: bool,
    native_instructions_enabled: bool,
    tools: tuple[str, ...] | None = None,
) -> Coder[_CodingAgentDependencies]:
    shell_capabilities: list[Shell[_CodingAgentDependencies]] = []

    def find_shell(capability):
        if isinstance(capability, Shell):
            shell_capabilities.append(capability)

    coder.apply(find_shell)
    if len(shell_capabilities) != 1:
        raise RuntimeError(
            "Expected exactly one Harness Shell capability before applying the "
            "product shell policy"
        )

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

    def configure(capability):
        # 普通产品沿用原生通用说明；显式评测保持原来的提示组合。
        # Shell 生命周期、参数修复等执行能力仍分别选择。
        if type(capability) is Capability:
            return capability if native_instructions_enabled else None
        if isinstance(capability, RepairToolArguments):
            return None
        if isinstance(capability, FileSystem):
            return AbaqusTextFileSystem[_CodingAgentDependencies](
                root_dir=workspace,
                cwd=capability.cwd,
                allowed_patterns=capability.allowed_patterns,
                denied_patterns=capability.denied_patterns,
                protected_patterns=capability.protected_patterns,
                max_read_lines=capability.max_read_lines,
                max_read_chars=capability.max_read_chars,
                max_list_results=capability.max_list_results,
                max_search_results=capability.max_search_results,
                max_find_results=capability.max_find_results,
                read_only=capability.read_only,
                content_hashes=capability.content_hashes,
                id=capability.id,
                description=capability.description,
                defer_loading=capability.defer_loading,
                tools=tuple(
                    name
                    for name in DEFAULT_TOOL_NAMES
                    if tools is None or name in tools
                ),
            )
        if isinstance(capability, ToolOutputLimits):
            # Keep spill + read_tool_result instead of Coder's lossy-only truncation.
            return ToolOutputLimits[_CodingAgentDependencies]()
        if context_policy_enabled and isinstance(
            capability, (ClearToolResults, WarnNearLimits)
        ):
            return None
        if isinstance(capability, Shell):
            if not shell_enabled:
                return None
            return Shell[_CodingAgentDependencies](
                cwd=workspace,
                denied_commands=("abaqus", "abq"),
                denied_env_patterns=LLM_API_KEY_ENV_PATTERNS
                + TELEMETRY_CREDENTIAL_ENV_PATTERNS
                + PROVIDER_SECRET_PATTERNS,
                tools=tuple(
                    name
                    for name in RUN_SCOPED_TOOL_NAMES
                    if tools is None or name in tools
                ),
            )
        if isinstance(capability, RepoContext) and not has_repo_context:
            return None
        return capability

    configured = coder.visit_and_replace(configure)
    if not isinstance(configured, Coder):
        raise RuntimeError("Harness Coder lost its composition while applying policy")
    return configured
