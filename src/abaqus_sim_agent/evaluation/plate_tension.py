from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import (
    EvaluationReason,
    Evaluator,
    EvaluatorContext,
)
from pydantic_evals.reporting import EvaluationReport

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.api_lookup import ApiCatalogMode
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.concept_lookup import (
    ConceptRetrievalMode,
    concept_index_binding,
    open_concept_index,
)
from abaqus_sim_agent.coding.contracts import (
    CLARIFICATION_POLICY_VERSION,
    ApiCatalogBinding,
    ClarificationKind,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.harness_compat import (
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
)
from abaqus_sim_agent.coding.instructions import CONCEPT_ADOPTION_POLICY_VERSION
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    DeepSeekGateway,
    create_deepseek_chat_model,
    gateway_base_url,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.resume import create_step_store, load_resume_state
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunService,
)
from abaqus_sim_agent.coding.workspace import ResolvedWorkspace, WorkspaceResolver
from abaqus_sim_agent.evaluation.clarification_policy import load_cases
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.assets import plate_tension_oracle_script
from abaqus_sim_agent.verification.plate_tension import PlateTensionOracleSpec
from abaqus_sim_agent.verification.run import (
    PlateTensionRunVerifier,
    PlateTensionVerificationAttempt,
)

PROMPT_VERSION = "plate-tension-v4"
GOLD_TASK_INSTRUCTIONS = """
This is the fixed plate-tension Gold evaluation. The required missing field id
for plate thickness is `plate_thickness`.

The authoring script must be `build_model.py` and create `plate_tension.cae`
and `plate_tension.inp` without submitting a Job. Use the solver tool on that
input deck to create `plate_tension.odb`. A separate odbAccess script named
`extract_results.py` must create `results.json` with numeric `right_mean_u1`,
`mean_s11`, and `left_sum_rf1`. Request verification only after those artifacts
exist and their runtime evidence has been inspected.
""".strip()
POLICY_PROCEED_TASK_INSTRUCTIONS = """
The request's physical inputs are complete. Preserve every explicit constraint
and choose implementation or numerical details within the verified plate scope
without asking the user to select an Abaqus API or element code.

The authoring script must be `build_model.py` and create `plate_tension.cae`
and `plate_tension.inp` without submitting a Job. Use the solver tool on that
input deck to create `plate_tension.odb`. A separate odbAccess script named
`extract_results.py` must create `results.json` with numeric `right_mean_u1`,
`mean_s11`, and `left_sum_rf1`. Request verification only after those artifacts
and their runtime evidence exist.
""".strip()

GOLD_REQUEST = """
请在当前空工作目录中，使用本机 Abaqus 2024 从零建立并完成一个二维单轴拉伸分析。

模型是 100 mm × 20 mm 的矩形平面应力板，单位制为 mm-N-MPa。材料为各向同性线弹性，
弹性模量 E=210000 MPa，泊松比 ν=0.3。使用 Abaqus/Standard 的 Static, General，
NLGEOM=OFF；使用 CPS4R，沿长度 20 个单元、沿高度 4 个单元的结构化网格。

左边界 U1=0，左下角点 U2=0；右边界规定 U1=0.1 mm。请求 U、S、RF。
建模脚本必须命名为 build_model.py，并且只生成 plate_tension.cae 与 plate_tension.inp，不在 CAE 脚本中
提交 Job；随后通过独立 Solver 阶段生成 plate_tension.odb，再用命名为 extract_results.py 的独立
odbAccess 脚本生成 results.json。分析步名称可自行选择，但只能创建一个分析步。
不要猜测缺失的物理参数；只有在结果产物和运行证据齐全后才请求验证。
""".strip()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlateTensionEvalInput(_FrozenModel):
    goal: str = GOLD_REQUEST
    scripted_answers: dict[str, str] = Field(
        default_factory=lambda: {"plate_thickness": "1 mm"}
    )
    task_instructions: str = GOLD_TASK_INSTRUCTIONS
    oracle_spec: PlateTensionOracleSpec = Field(default_factory=PlateTensionOracleSpec)


class PlateTensionEvalMetadata(_FrozenModel):
    required_missing_field: str | None = "plate_thickness"
    required_clarification_kind: ClarificationKind | None = "missing_physical_input"
    forbidden_question_terms: tuple[str, ...] = (
        "cps4r",
        "cpe4r",
        "findat",
        "assembly.set",
        "method signature",
        "api",
    )
    oracle_spec: PlateTensionOracleSpec = Field(default_factory=PlateTensionOracleSpec)


class PlateTensionObservation(_FrozenModel):
    run_id: str
    outcome: CodingRunOutcome
    verification: PlateTensionVerificationAttempt | None = None
    manual_code_edits: bool = False
    abaqus_runtime_seconds: float = Field(ge=0)
    infrastructure_resume_count: int = Field(default=0, ge=0, le=1)
    pre_clarification_tool_names: tuple[str, ...] = ()


class EvalGate(_FrozenModel):
    attempts: int = Field(ge=0)
    passes: int = Field(ge=0)
    required_passes: int = Field(default=2, ge=1)
    passed: bool


@dataclass
class ClosedLoopEvaluator(
    Evaluator[
        PlateTensionEvalInput,
        PlateTensionObservation,
        PlateTensionEvalMetadata,
    ]
):
    def evaluate(
        self,
        ctx: EvaluatorContext[
            PlateTensionEvalInput,
            PlateTensionObservation,
            PlateTensionEvalMetadata,
        ],
    ) -> dict[str, EvaluationReason | float | str]:
        output = ctx.output
        verification = output.verification
        expected_field = (
            ctx.metadata.required_missing_field
            if ctx.metadata is not None
            else "plate_thickness"
        )
        expected_kind = (
            ctx.metadata.required_clarification_kind
            if ctx.metadata is not None
            else "missing_physical_input"
        )
        forbidden_terms = (
            ctx.metadata.forbidden_question_terms if ctx.metadata is not None else ()
        )
        clarification = (
            output.outcome.clarifications[0]
            if len(output.outcome.clarifications) == 1
            else None
        )
        question_has_forbidden_term = bool(
            clarification is not None
            and any(
                term.casefold() in clarification.question.casefold()
                for term in forbidden_terms
            )
        )
        clarification_ok = (
            not output.outcome.clarifications
            if expected_field is None
            else (
                clarification is not None
                and clarification.field_id == expected_field
                and clarification.kind == expected_kind
                and bool(clarification.impact.strip())
                and clarification.answer == "1 mm"
                and clarification.abaqus_calls_before_answer == 0
                and not output.pre_clarification_tool_names
                and not question_has_forbidden_term
            )
        )
        physics_checks = []
        if verification is not None and verification.verification is not None:
            report = verification.verification
            physics_checks = [report.u1.passed, report.s11.passed, report.rf1.passed]
        physics_score = (
            sum(1.0 for passed in physics_checks if passed) / 3.0
            if physics_checks
            else 0.0
        )
        artifacts = verification.artifacts if verification is not None else ()
        required_artifacts = {
            "build_model.py",
            "extract_results.py",
            "plate_tension.cae",
            "plate_tension.inp",
            "plate_tension.odb",
            "results.json",
        }
        fresh = {artifact.relative_path for artifact in artifacts if artifact.fresh}
        artifact_score = len(required_artifacts & fresh) / len(required_artifacts)
        overall = (
            clarification_ok
            and not output.manual_code_edits
            and output.outcome.status is CodingRunStatus.COMPLETED
            and verification is not None
            and verification.passed
        )
        failure_stage = output.outcome.failure_stage or (
            verification.failure_stage if verification is not None else None
        )
        return {
            "closed_loop_pass": EvaluationReason(
                value=overall,
                reason=(
                    "clarification, artifacts, Job and physics verification passed"
                    if overall
                    else f"closed loop failed at {failure_stage or 'unknown'}"
                ),
            ),
            "clarification_score": 1.0 if clarification_ok else 0.0,
            "pre_clarification_tool_count": float(
                len(output.pre_clarification_tool_names)
                if expected_field is not None
                else 0
            ),
            "pre_clarification_tools": (
                (",".join(output.pre_clarification_tool_names) or "none")
                if expected_field is not None
                else "not_applicable"
            ),
            "clarification_kind": (
                clarification.kind if clarification is not None else "none"
            ),
            "clarification_question_scope": (
                (
                    "implementation_detail"
                    if question_has_forbidden_term
                    else "engineering"
                )
                if expected_field is not None
                else "not_applicable"
            ),
            "artifact_score": artifact_score,
            "physics_score": physics_score,
            "failure_stage": failure_stage or "none",
        }


def build_dataset() -> Dataset[
    PlateTensionEvalInput,
    PlateTensionObservation,
    PlateTensionEvalMetadata,
]:
    return Dataset(
        name="abaqus2024_closed_loop_v1",
        cases=[
            Case(
                name="plate_tension_missing_thickness",
                inputs=PlateTensionEvalInput(),
                metadata=PlateTensionEvalMetadata(),
            )
        ],
        evaluators=[ClosedLoopEvaluator()],
    )


def build_policy_proceed_dataset(
    case_id: str,
) -> Dataset[
    PlateTensionEvalInput,
    PlateTensionObservation,
    PlateTensionEvalMetadata,
]:
    case = next((case for case in load_cases() if case.case_id == case_id), None)
    if case is None or case.expected_decision != "proceed":
        raise ValueError(f"unknown or non-proceed clarification policy case: {case_id}")
    oracle_spec = (
        PlateTensionOracleSpec(allowed_element_types=case.allowed_element_types)
        if case.allowed_element_types
        else PlateTensionOracleSpec()
    )
    task_instructions = POLICY_PROCEED_TASK_INSTRUCTIONS
    if case.allowed_element_types:
        choices = ", ".join(case.allowed_element_types)
        task_instructions += (
            "\n\nThe host-verified numerical capability profile for this Case allows "
            f"exactly one uniform element choice from: {choices}. Choose one without "
            "asking the user for an Abaqus element code."
        )
    return Dataset(
        name=f"abaqus2024_{case_id}_v1",
        cases=[
            Case(
                name=case.case_id,
                inputs=PlateTensionEvalInput(
                    goal=case.goal,
                    scripted_answers={},
                    task_instructions=task_instructions,
                    oracle_spec=oracle_spec,
                ),
                metadata=PlateTensionEvalMetadata(
                    required_missing_field=None,
                    required_clarification_kind=None,
                    oracle_spec=oracle_spec,
                ),
            )
        ],
        evaluators=[ClosedLoopEvaluator()],
    )


def gate_report(
    report: EvaluationReport[
        PlateTensionEvalInput,
        PlateTensionObservation,
        PlateTensionEvalMetadata,
    ],
    *,
    required_passes: int = 2,
) -> EvalGate:
    attempts = len(report.cases) + len(report.failures)
    passes = sum(
        1
        for case in report.cases
        if (assertion := case.assertions.get("closed_loop_pass")) is not None
        and assertion.value is True
        and not case.evaluator_failures
    )
    return EvalGate(
        attempts=attempts,
        passes=passes,
        required_passes=required_passes,
        passed=attempts == 3 and passes >= required_passes,
    )


async def run_evaluation(
    run_root: Path,
    *,
    model_gateway: DeepSeekGateway = DeepSeekGateway.OFFICIAL,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    api_catalog_dir: Path | None = None,
    api_catalog_mode: ApiCatalogMode | None = None,
    concept_index_dir: Path | None = None,
    concept_retrieval_mode: ConceptRetrievalMode = "off",
    clarification_policy_case: str | None = None,
    resume_infrastructure_once: bool = False,
) -> EvalGate:
    harness_hash_patch = apply_harness_windows_newline_hash_patch()
    harness_shell_patch = apply_harness_windows_shell_tree_patch()
    api_key = take_gateway_api_key_from_environment(model_gateway)
    oracle_script = plate_tension_oracle_script()
    api_catalog = await asyncio.to_thread(
        _load_eval_api_catalog,
        run_root,
        api_catalog_dir,
    )
    resolved_api_catalog_mode = _resolve_eval_api_catalog_mode(
        api_catalog_mode,
        api_catalog,
    )
    concept_index = await asyncio.to_thread(
        _load_eval_concept_index,
        run_root,
        concept_index_dir,
    )
    resolved_concept_retrieval_mode = _resolve_eval_concept_retrieval_mode(
        concept_retrieval_mode,
        concept_index,
    )
    experiment_metadata = {
        "model_gateway": model_gateway.value,
        "model": model_name,
        "base_url": gateway_base_url(model_gateway),
        "protocol": "openai-chat-completions",
        "profile_family": "pydantic-ai-deepseek",
        "thinking": "provider-default",
        "thinking_history_replay": (
            "disabled-go-compat"
            if model_gateway is DeepSeekGateway.OPENCODE_GO
            else "field"
        ),
        "http_retries": http_retries,
        "http_timeout_seconds": DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
        "api_catalog_enabled": api_catalog is not None,
        "api_catalog_mode": resolved_api_catalog_mode,
        "concept_retrieval_mode": resolved_concept_retrieval_mode,
        "concept_adoption_policy_version": (
            CONCEPT_ADOPTION_POLICY_VERSION
            if resolved_concept_retrieval_mode == "tool"
            else None
        ),
        "concept_index_enabled": concept_index is not None,
        "concept_index_release": (
            concept_index.read_manifest().release if concept_index is not None else None
        ),
        "planning_enabled": False,
        "clarification_policy_version": CLARIFICATION_POLICY_VERSION,
        "clarification_policy_case": clarification_policy_case,
        "resume_infrastructure_once": resume_infrastructure_once,
        "resume_policy_version": (
            "same-run-infrastructure-once-v1" if resume_infrastructure_once else "off"
        ),
        "max_infrastructure_resumes_per_run": (1 if resume_infrastructure_once else 0),
        "eval_task_retry": False,
        "api_catalog_cards_sha256": (
            api_catalog.manifest.cards_sha256
            if api_catalog is not None and api_catalog.manifest is not None
            else None
        ),
        "api_catalog_card_count": (
            len(api_catalog.cards) if api_catalog is not None else 0
        ),
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "harness_windows_newline_hash_patch": harness_hash_patch,
        "harness_windows_shell_tree_patch": harness_shell_patch,
        "runtime": "Abaqus 2024",
        "prompt_version": PROMPT_VERSION,
        "price_version": f"genai-prices=={version('genai-prices')}",
    }

    async def run_case(inputs: PlateTensionEvalInput) -> PlateTensionObservation:
        run_id = f"eval-{uuid4().hex[:12]}"
        resolved = await asyncio.to_thread(
            _create_eval_workspace,
            run_root,
            run_id,
        )
        request = CodingRunRequest(
            run_id=run_id,
            goal=inputs.goal,
            workspace=resolved.workspace,
            state_dir=resolved.state_dir,
            runtime=RuntimeProfile(
                launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()
            ),
            api_catalog=_eval_api_catalog_binding(api_catalog),
            concept_index=(
                concept_index_binding(concept_index)
                if concept_index is not None
                else None
            ),
        )

        async def run_attempt(resume: bool):
            runner = Abaqus2024Runner(
                request.workspace,
                request.state_dir,
                launcher=request.runtime.launcher_path,
            )
            step_store = create_step_store(request.state_dir)
            model, client = create_deepseek_chat_model(
                gateway=model_gateway,
                model_name=model_name,
                api_key=api_key,
                http_retries=http_retries,
                conversation_id=request.run_id
                if model_gateway is DeepSeekGateway.OPENCODE_GO
                else None,
            )
            dependencies = CodingAgentDependencies(
                request=request,
                runner=runner,
                step_store=step_store,
                api_catalog=api_catalog,
                concept_index=concept_index,
            )
            agent = create_abaqus_coding_agent(
                model,
                request,
                skill_library=skill_library_path(),
                api_catalog_mode=resolved_api_catalog_mode,
                concept_retrieval_mode=resolved_concept_retrieval_mode,
                planning_enabled=False,
                task_instructions=inputs.task_instructions,
                step_store=step_store,
            )
            service = CodingRunService(
                agent=agent,
                request=request,
                dependencies=dependencies,
                clarification_provider=ScriptedClarificationProvider(
                    inputs.scripted_answers
                ),
                verifier=PlateTensionRunVerifier(oracle_script, inputs.oracle_spec),
                initial_snapshot=resolved.initial_snapshot,
                resume=resume,
                step_store=step_store,
            )
            try:
                return await service.run(), dependencies
            finally:
                await client.close()

        (
            (result, dependencies),
            infrastructure_resume_count,
        ) = await _run_with_optional_infrastructure_resume(
            run_attempt,
            enabled=resume_infrastructure_once,
            state_dir=request.state_dir,
        )

        usage = result.outcome.usage
        for name, value in {
            "model_requests": usage.model_requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "tool_calls": usage.tool_calls,
            "api_lookup_calls": usage.api_lookup_calls,
            "concept_lookup_calls": usage.concept_lookup_calls,
            "abaqus_calls": usage.abaqus_calls,
            "solver_calls": usage.solver_calls,
            "infrastructure_resume_count": infrastructure_resume_count,
            "abaqus_runtime_s": sum(
                execution.duration_seconds for execution in dependencies.executions
            ),
        }.items():
            increment_eval_metric(name, value)
        if (
            model_gateway is DeepSeekGateway.OFFICIAL
            and usage.estimated_cost_usd is not None
        ):
            increment_eval_metric("cost_usd_estimate", usage.estimated_cost_usd)
            set_eval_attribute("cost_status", "available")
        else:
            set_eval_attribute("cost_status", "unavailable")
        set_eval_attribute("run_id", run_id)
        set_eval_attribute("workspace", str(request.workspace))
        set_eval_attribute("model_gateway", model_gateway.value)
        set_eval_attribute("model", model_name)
        set_eval_attribute(
            "infrastructure_resume_count",
            infrastructure_resume_count,
        )
        return PlateTensionObservation(
            run_id=run_id,
            outcome=result.outcome,
            verification=result.verification,
            abaqus_runtime_seconds=sum(
                execution.duration_seconds for execution in dependencies.executions
            ),
            infrastructure_resume_count=infrastructure_resume_count,
            pre_clarification_tool_names=_tools_before_first_clarification(
                request.state_dir / "history.json"
            ),
        )

    dataset = (
        build_policy_proceed_dataset(clarification_policy_case)
        if clarification_policy_case is not None
        else build_dataset()
    )
    report = await dataset.evaluate(
        run_case,
        name=(f"{model_gateway.value}-{model_name}-abaqus2024-{PROMPT_VERSION}"),
        repeat=3,
        max_concurrency=1,
        retry_task=None,
        retry_evaluators=None,
        metadata=experiment_metadata,
    )
    gate = gate_report(report)
    _write_evaluation_artifacts(run_root, report, gate)
    report.print(include_output=True, include_reasons=True)
    return gate


async def _run_with_optional_infrastructure_resume[T](
    run_attempt: Callable[[bool], Awaitable[T]],
    *,
    enabled: bool,
    state_dir: Path,
) -> tuple[T, int]:
    try:
        return await run_attempt(False), 0
    except CodingRunInfrastructureError as exc:
        if (
            not enabled
            or not isinstance(exc.__cause__, ModelAPIError)
            or load_resume_state(state_dir).status != "interrupted"
        ):
            raise
    return await run_attempt(True), 1


def _create_eval_workspace(run_root: Path, run_id: str) -> ResolvedWorkspace:
    run_dir = run_root / run_id
    workspace = run_dir / "workspace"
    state_dir = run_dir / "state"
    workspace.mkdir(parents=True, exist_ok=False)
    return WorkspaceResolver(state_root=run_root).resolve_eval(
        run_id=run_id,
        workspace=workspace,
        state_dir=state_dir,
    )


def _tools_before_first_clarification(history_path: Path) -> tuple[str, ...]:
    messages = ModelMessagesTypeAdapter.validate_json(history_path.read_bytes())
    tool_names: list[str] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            if part.tool_name == "final_result_NeedsInputDecision":
                return tuple(tool_names)
            if part.tool_name.startswith("final_result_"):
                return tuple(tool_names)
            tool_names.append(part.tool_name)
    return tuple(tool_names)


def _load_eval_api_catalog(
    run_root: Path,
    api_catalog_dir: Path | None,
) -> ApiCatalog | None:
    if api_catalog_dir is None:
        return None
    catalog_path = api_catalog_dir.resolve(strict=True)
    resolved_run_root = run_root.resolve()
    if catalog_path == resolved_run_root or catalog_path.is_relative_to(
        resolved_run_root
    ):
        raise ValueError("API catalog must be outside the evaluation run root")
    return ApiCatalog.open(catalog_path)


def _load_eval_concept_index(
    run_root: Path,
    concept_index_dir: Path | None,
) -> AbaqusDocumentIndex | None:
    if concept_index_dir is None:
        return None
    return open_concept_index(concept_index_dir, forbidden_root=run_root)


def _eval_api_catalog_binding(
    catalog: ApiCatalog | None,
) -> ApiCatalogBinding | None:
    if catalog is None:
        return None
    manifest = catalog.manifest
    if manifest is None:
        raise ValueError("persisted API catalog is missing its manifest")
    return ApiCatalogBinding(
        schema_version=manifest.schema_version,
        cards_sha256=manifest.cards_sha256,
        card_count=manifest.card_count,
        member_count=manifest.member_count,
        members_sha256=manifest.members_sha256,
        runtime_release=manifest.runtime_release,
    )


def _resolve_eval_api_catalog_mode(
    requested: ApiCatalogMode | None,
    catalog: ApiCatalog | None,
) -> ApiCatalogMode:
    mode: ApiCatalogMode = requested or ("full" if catalog is not None else "off")
    if mode == "off" and catalog is not None:
        raise ValueError("off mode must not receive --api-catalog-dir")
    if mode != "off" and catalog is None:
        raise ValueError(f"{mode} mode requires --api-catalog-dir")
    return mode


def _resolve_eval_concept_retrieval_mode(
    requested: ConceptRetrievalMode,
    index: AbaqusDocumentIndex | None,
) -> ConceptRetrievalMode:
    if requested == "off" and index is not None:
        raise ValueError(
            "off mode must not receive --concept-index-dir; explicitly select tool"
        )
    if requested == "tool" and index is None:
        raise ValueError("tool mode requires --concept-index-dir")
    return requested


def _write_evaluation_artifacts(
    run_root: Path,
    report: EvaluationReport[
        PlateTensionEvalInput,
        PlateTensionObservation,
        PlateTensionEvalMetadata,
    ],
    gate: EvalGate,
) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "evaluation_report.txt").write_text(
        report.render(include_output=True, include_reasons=True),
        encoding="utf-8",
    )
    summary = {
        "name": report.name,
        "experiment_metadata": report.experiment_metadata,
        "attempts": gate.attempts,
        "passes": gate.passes,
        "required_passes": gate.required_passes,
        "passed": gate.passed,
        "cases": [
            {
                "name": case.name,
                "closed_loop_pass": (
                    case.assertions["closed_loop_pass"].value
                    if "closed_loop_pass" in case.assertions
                    else False
                ),
                "metrics": case.metrics,
                "labels": {key: result.value for key, result in case.labels.items()},
                "scores": {key: result.value for key, result in case.scores.items()},
                "task_duration": case.task_duration,
            }
            for case in report.cases
        ],
        "failures": [failure.name for failure in report.failures],
    }
    (run_root / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (run_root / "gate.json").write_text(
        gate.model_dump_json(indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the licensed Abaqus 2024 closed-loop smoke evaluation."
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("var") / "runs",
    )
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        default=DeepSeekGateway.OFFICIAL,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--http-retries", type=int, default=DEFAULT_MODEL_HTTP_RETRIES)
    parser.add_argument("--api-catalog-dir", type=Path)
    parser.add_argument(
        "--api-catalog-mode",
        choices=(
            "off",
            "full",
            "automatic_verified_only",
            "automatic_exact_signature",
        ),
    )
    parser.add_argument("--concept-index-dir", type=Path)
    parser.add_argument(
        "--concept-retrieval-mode",
        choices=("off", "tool"),
        default="off",
    )
    parser.add_argument(
        "--clarification-policy-case",
        help="Run one checked-in proceed case with zero expected clarification.",
    )
    parser.add_argument(
        "--resume-infrastructure-once",
        action="store_true",
        help=(
            "Resume the same CodingRun once after a Model API infrastructure "
            "interruption; task-level evaluation retries remain disabled."
        ),
    )
    args = parser.parse_args()
    gate = asyncio.run(
        run_evaluation(
            args.run_root.resolve(),
            model_gateway=args.model_provider,
            model_name=args.model,
            http_retries=args.http_retries,
            api_catalog_dir=args.api_catalog_dir,
            api_catalog_mode=args.api_catalog_mode,
            concept_index_dir=args.concept_index_dir,
            concept_retrieval_mode=args.concept_retrieval_mode,
            clarification_policy_case=args.clarification_policy_case,
            resume_infrastructure_once=args.resume_infrastructure_once,
        )
    )
    raise SystemExit(0 if gate.passed else 1)


if __name__ == "__main__":
    main()
