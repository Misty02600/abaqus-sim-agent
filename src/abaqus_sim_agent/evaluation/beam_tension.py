from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import (
    EvaluationReason,
    Evaluator,
    EvaluatorContext,
)
from pydantic_evals.reporting import EvaluationReport

from abaqus_sim_agent.coding.agent import (
    CODER_GENERAL_SHELL_POLICY_VERSION,
    create_abaqus_coding_agent,
)
from abaqus_sim_agent.coding.api_lookup import ApiCatalogMode
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.concept_lookup import (
    ConceptRetrievalMode,
    concept_index_binding,
)
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    RuntimeProfile,
    RuntimeScriptPolicy,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.harness_compat import (
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
)
from abaqus_sim_agent.coding.instructions import (
    CODING_DECISION_OUTPUT_POLICY_VERSION,
    CONCEPT_ADOPTION_POLICY_VERSION,
    EVIDENCE_REPAIR_CONVERGENCE_POLICY_VERSION,
)
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    DeepSeekGateway,
    create_deepseek_chat_model,
    gateway_base_url,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.static_preflight import (
    AbqpyBasedPyrightPreflight,
    prepare_abqpy_source,
)
from abaqus_sim_agent.evaluation.bar_tension_3d import (
    _create_eval_workspace,
    _eval_api_catalog_binding,
    _load_eval_api_catalog,
    _load_eval_concept_index,
    _resolve_eval_api_catalog_mode,
    _resolve_eval_concept_retrieval_mode,
    _sha256_file,
)
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.beam_run import (
    BeamTensionRunVerifier,
    BeamTensionVerificationAttempt,
    beam_tension_oracle_script,
)
from abaqus_sim_agent.verification.beam_tension import BeamTensionOracleSpec

PROMPT_VERSION = "beam-tension-b31-v1"

GOLD_REQUEST = """
请在当前空工作目录中，使用本机 Abaqus 2024 从零建立并完成一个三维梁单轴拉伸分析。

模型是一条沿全局 X 方向、从 (0,0,0) 到 (100,0,0) mm 的三维 wire，单位制为 mm-N-MPa。
截面为 10×10 mm 的矩形梁截面，材料为各向同性线弹性，弹性模量 E=210000 MPa，泊松比
ν=0.3；必须给出有效且与梁轴线不平行的梁截面方向。使用 Abaqus/Standard 的
Static, General，NLGEOM=OFF；使用 B31，沿长度均匀划分 20 个单元，共 21 个节点。

左端点使用 Encastre，约束全部 6 个自由度；右端点只规定 U1=0.1 mm，其余自由度不额外
约束。请求 U、RF、SF。

建模脚本必须命名为 build_model.py，并且只生成 beam_tension.cae 与 beam_tension.inp，不在
CAE 脚本中提交 Job；随后通过独立 Solver 阶段生成 beam_tension.odb，再用命名为
extract_results.py 的独立 odbAccess 脚本生成 results.json。results.json 必须包含数值字段
right_mean_u1、left_sum_rf1 和 mean_sf1。分析步名称可自行选择，但只能创建一个分析步。
本任务的物理输入已经完整，不需要询问补充信息；只有在结果产物和运行证据齐全后才请求验证。
""".strip()

TASK_INSTRUCTIONS = """
This benchmark is fully specified: no physical input is missing, so do not return
needs_input. The authoring script must be `build_model.py` and create
`beam_tension.cae` and `beam_tension.inp` without submitting a Job. Use the
solver tool on that input deck to create `beam_tension.odb`. A separate
odbAccess script named `extract_results.py` must create `results.json` with
numeric `right_mean_u1`, `left_sum_rf1`, and `mean_sf1` fields. Return
ready_for_verification only after all six required artifacts exist and the
relevant runtime evidence has been inspected.
""".strip()

EVAL_BUDGET = CodingRunBudget(
    max_clarifications=0,
    max_model_requests=40,
    max_api_lookup_calls=4,
    max_concept_lookup_calls=2,
    max_abaqus_calls=12,
    max_solver_calls=3,
    wall_time_seconds=2_700.0,
)

BeamRuntimeScriptPolicyMode = Literal["off", "production_allowlist"]
BeamStaticPreflightMode = Literal["off", "advisory", "enforced_verified"]
B31_RUNTIME_SCRIPT_POLICY = RuntimeScriptPolicy(
    allowed_cae_scripts=("build_model.py",),
    allowed_abaqus_python_scripts=("extract_results.py",),
    allowed_solver_inputs=("beam_tension.inp",),
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BeamTensionEvalInput(_FrozenModel):
    goal: str = GOLD_REQUEST


class BeamTensionEvalMetadata(_FrozenModel):
    oracle_spec: BeamTensionOracleSpec = Field(default_factory=BeamTensionOracleSpec)


class BeamTensionObservation(_FrozenModel):
    run_id: str
    outcome: CodingRunOutcome
    verification: BeamTensionVerificationAttempt | None = None
    manual_code_edits: bool = False
    abaqus_runtime_seconds: float = Field(ge=0)


class BeamTensionEvalGate(_FrozenModel):
    attempts: int = Field(ge=0)
    passes: int = Field(ge=0)
    required_passes: int = Field(default=2, ge=1)
    passed: bool


@dataclass
class BeamTensionClosedLoopEvaluator(
    Evaluator[
        BeamTensionEvalInput,
        BeamTensionObservation,
        BeamTensionEvalMetadata,
    ]
):
    def evaluate(
        self,
        ctx: EvaluatorContext[
            BeamTensionEvalInput,
            BeamTensionObservation,
            BeamTensionEvalMetadata,
        ],
    ) -> dict[str, EvaluationReason | float | str]:
        output = ctx.output
        attempt = output.verification
        physics_checks: list[bool] = []
        geometry_checks: list[bool] = []
        topology_checks: list[bool] = []
        model_contract_checks: list[bool] = []
        verifier_input_contract_passed = False
        if attempt is not None and attempt.verification is not None:
            report = attempt.verification
            verifier_input_contract_passed = report.input_contract_passed
            physics_checks = [
                report.u1.passed,
                report.transverse_u.passed,
                report.left_rf1.passed,
                report.right_rf1.passed,
                report.rf1_balance.passed,
                report.sf1.passed,
                report.sf1_uniformity.passed,
                report.transverse_sf.passed,
            ]
            geometry_checks = [
                report.x_span.passed,
                report.transverse_coordinate.passed,
                report.coordinates_passed,
            ]
            topology_checks = [
                report.element_type_passed,
                report.element_count_passed,
                report.node_count_passed,
                report.coordinate_layers_passed,
                report.analysis_step_count_passed,
                report.step_definition_passed,
                report.connectivity_passed,
                report.sf1_value_count_passed,
                report.sf1_element_coverage_passed,
                report.sf1_sign_passed,
            ]
            model_contract_checks = [
                report.material_passed,
                report.profile_passed,
                report.section_linkage_passed,
                report.orientation_passed,
            ]

        required_artifacts = {
            "build_model.py",
            "extract_results.py",
            "beam_tension.cae",
            "beam_tension.inp",
            "beam_tension.odb",
            "results.json",
        }
        artifacts = attempt.artifacts if attempt is not None else ()
        fresh = {artifact.relative_path for artifact in artifacts if artifact.fresh}
        artifact_score = len(required_artifacts & fresh) / len(required_artifacts)
        no_clarification = not output.outcome.clarifications
        overall = (
            no_clarification
            and not output.manual_code_edits
            and output.outcome.status is CodingRunStatus.COMPLETED
            and attempt is not None
            and attempt.passed
        )
        failure_stage = output.outcome.failure_stage or (
            attempt.failure_stage if attempt is not None else None
        )
        evaluation: dict[str, EvaluationReason | float | str] = {
            "closed_loop_pass": EvaluationReason(
                value=overall,
                reason=(
                    "zero clarification, artifacts, Job and physics verification passed"
                    if overall
                    else f"closed loop failed at {failure_stage or 'unknown'}"
                ),
            ),
            "input_contract_score": (
                1.0 if no_clarification and verifier_input_contract_passed else 0.0
            ),
            "artifact_score": artifact_score,
            "physics_score": _pass_fraction(physics_checks),
            "geometry_score": _pass_fraction(geometry_checks),
            "topology_score": _pass_fraction(topology_checks),
            "model_contract_score": _pass_fraction(model_contract_checks),
            "failure_stage": failure_stage or "none",
        }
        measurements = attempt.measurements if attempt is not None else None
        if measurements is not None:
            evaluation.update(
                {
                    "measured_node_count": float(measurements.node_count),
                    "measured_element_count": float(
                        measurements.element_type_counts.get("B31", 0)
                    ),
                    "measured_step_count": float(measurements.analysis_step_count),
                    "measured_connectivity_score": (
                        1.0 if measurements.connectivity_is_x_chain else 0.0
                    ),
                    "measured_x_span": measurements.x_span,
                    "measured_max_abs_transverse_coordinate": (
                        measurements.max_abs_transverse_coordinate
                    ),
                    "measured_right_mean_u1": measurements.right_mean_u1,
                    "measured_max_abs_transverse_u": (
                        measurements.max_abs_transverse_u
                    ),
                    "measured_left_sum_rf1": measurements.left_sum_rf1,
                    "measured_right_sum_rf1": measurements.right_sum_rf1,
                    "measured_mean_sf1": measurements.mean_sf1,
                    "measured_sf1_min": measurements.sf1_min,
                    "measured_sf1_max": measurements.sf1_max,
                    "measured_sf1_value_count": float(measurements.sf1_value_count),
                    "measured_max_abs_transverse_sf": (
                        measurements.max_abs_transverse_sf
                    ),
                    "measured_elastic_e": measurements.elastic_e,
                    "measured_elastic_nu": measurements.elastic_nu,
                    "measured_profile_a": measurements.profile_a,
                    "measured_profile_b": measurements.profile_b,
                }
            )
        return evaluation


def _pass_fraction(checks: list[bool]) -> float:
    if not checks:
        return 0.0
    return sum(1.0 for passed in checks if passed) / len(checks)


def build_dataset() -> Dataset[
    BeamTensionEvalInput,
    BeamTensionObservation,
    BeamTensionEvalMetadata,
]:
    return Dataset(
        name="abaqus2024_beam_tension_closed_loop_v1",
        cases=[
            Case(
                name="beam_tension_fully_specified",
                inputs=BeamTensionEvalInput(),
                metadata=BeamTensionEvalMetadata(),
            )
        ],
        evaluators=[BeamTensionClosedLoopEvaluator()],
    )


def gate_report(
    report: EvaluationReport[
        BeamTensionEvalInput,
        BeamTensionObservation,
        BeamTensionEvalMetadata,
    ],
    *,
    required_passes: int = 2,
) -> BeamTensionEvalGate:
    attempts = len(report.cases) + len(report.failures)
    passes = sum(
        1
        for case in report.cases
        if (assertion := case.assertions.get("closed_loop_pass")) is not None
        and assertion.value is True
        and not case.evaluator_failures
    )
    return BeamTensionEvalGate(
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
    api_catalog_mode: ApiCatalogMode = "automatic_verified_only",
    concept_index_dir: Path | None = None,
    concept_retrieval_mode: ConceptRetrievalMode = "off",
    runtime_script_policy_mode: BeamRuntimeScriptPolicyMode = "off",
    static_preflight_mode: BeamStaticPreflightMode = "off",
    abqpy_static_wheel: Path | None = None,
    static_preflight_cache_dir: Path | None = None,
) -> BeamTensionEvalGate:
    harness_hash_patch = apply_harness_windows_newline_hash_patch()
    harness_shell_patch = apply_harness_windows_shell_tree_patch()
    api_key = take_gateway_api_key_from_environment(model_gateway)
    oracle_script = beam_tension_oracle_script()
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
    if runtime_script_policy_mode == "production_allowlist":
        runtime_script_policy = B31_RUNTIME_SCRIPT_POLICY
    elif runtime_script_policy_mode == "off":
        runtime_script_policy = RuntimeScriptPolicy()
    else:
        raise ValueError(
            f"unknown runtime script policy mode: {runtime_script_policy_mode}"
        )
    if static_preflight_mode == "advisory":
        configured_static_preflight_mode: (
            Literal["advisory", "enforced_verified"] | None
        ) = "advisory"
    elif static_preflight_mode == "enforced_verified":
        configured_static_preflight_mode = "enforced_verified"
    elif static_preflight_mode == "off":
        configured_static_preflight_mode = None
    else:
        raise ValueError(f"unknown static preflight mode: {static_preflight_mode}")
    if configured_static_preflight_mode is not None:
        if abqpy_static_wheel is None or static_preflight_cache_dir is None:
            raise ValueError(
                f"{configured_static_preflight_mode} static preflight requires an abqpy "
                "wheel and cache dir"
            )
        if (
            configured_static_preflight_mode == "enforced_verified"
            and runtime_script_policy_mode != "production_allowlist"
        ):
            raise ValueError(
                "enforced_verified static preflight requires the production "
                "runtime allowlist"
            )
        static_preflight_source = await asyncio.to_thread(
            prepare_abqpy_source,
            abqpy_static_wheel,
            static_preflight_cache_dir,
        )
    else:
        if abqpy_static_wheel is not None or static_preflight_cache_dir is not None:
            raise ValueError("off static preflight must not receive source paths")
        static_preflight_source = None
    if static_preflight_source is not None:
        assert configured_static_preflight_mode is not None
        static_preflight_binding = static_preflight_source.binding(
            configured_static_preflight_mode
        )
    else:
        static_preflight_binding = None
    api_manifest = api_catalog.manifest if api_catalog is not None else None
    concept_manifest = (
        concept_index.read_manifest() if concept_index is not None else None
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
        "api_catalog_cards_sha256": (
            api_manifest.cards_sha256 if api_manifest is not None else None
        ),
        "api_catalog_schema_version": (
            api_manifest.schema_version if api_manifest is not None else None
        ),
        "api_catalog_runtime_release": (
            api_manifest.runtime_release if api_manifest is not None else None
        ),
        "api_catalog_card_count": (
            len(api_catalog.cards) if api_catalog is not None else 0
        ),
        "concept_retrieval_mode": resolved_concept_retrieval_mode,
        "concept_adoption_policy_version": (
            CONCEPT_ADOPTION_POLICY_VERSION
            if resolved_concept_retrieval_mode == "tool"
            else None
        ),
        "concept_index_enabled": concept_index is not None,
        "concept_index_release": (
            concept_manifest.release if concept_manifest is not None else None
        ),
        "concept_index_schema_version": (
            concept_manifest.schema_version if concept_manifest is not None else None
        ),
        "concept_index_collection": (
            concept_manifest.collection_name if concept_manifest is not None else None
        ),
        "concept_index_document_count": (
            concept_manifest.documents_indexed if concept_manifest is not None else 0
        ),
        "concept_manifest_sha256": (
            _sha256_file(concept_index.manifest_path)
            if concept_index is not None
            else None
        ),
        "concept_embedding": (
            concept_manifest.embedding if concept_manifest is not None else None
        ),
        "planning_enabled": False,
        "coder_general_shell_policy": CODER_GENERAL_SHELL_POLICY_VERSION,
        "coding_decision_output_policy": CODING_DECISION_OUTPUT_POLICY_VERSION,
        "evidence_repair_convergence_policy": (
            EVIDENCE_REPAIR_CONVERGENCE_POLICY_VERSION
        ),
        "runtime_script_policy_mode": runtime_script_policy_mode,
        "runtime_script_policy": runtime_script_policy.model_dump(mode="json"),
        "static_preflight_mode": static_preflight_mode,
        "static_preflight_binding": (
            static_preflight_binding.model_dump(mode="json")
            if static_preflight_binding is not None
            else None
        ),
        "coding_run_budget": EVAL_BUDGET.model_dump(mode="json"),
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "harness_windows_newline_hash_patch": harness_hash_patch,
        "harness_windows_shell_tree_patch": harness_shell_patch,
        "runtime": "Abaqus 2024",
        "prompt_version": PROMPT_VERSION,
        "price_version": f"genai-prices=={version('genai-prices')}",
    }

    async def run_case(inputs: BeamTensionEvalInput) -> BeamTensionObservation:
        run_id = f"beam-{uuid4().hex[:12]}"
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
            runtime_script_policy=runtime_script_policy,
            static_preflight=(static_preflight_binding),
            api_catalog=_eval_api_catalog_binding(api_catalog),
            concept_index=(
                concept_index_binding(concept_index)
                if concept_index is not None
                else None
            ),
            budget=EVAL_BUDGET,
        )
        runner = Abaqus2024Runner(
            request.workspace,
            request.state_dir,
            launcher=request.runtime.launcher_path,
        )
        model, client = create_deepseek_chat_model(
            gateway=model_gateway,
            model_name=model_name,
            api_key=api_key,
            http_retries=http_retries,
            conversation_id=request.run_id
            if model_gateway is DeepSeekGateway.OPENCODE_GO
            else None,
        )
        try:
            static_preflight = (
                AbqpyBasedPyrightPreflight(
                    static_preflight_source,
                    request.state_dir,
                    mode=static_preflight_binding.mode,
                )
                if static_preflight_source is not None
                and static_preflight_binding is not None
                else None
            )
            dependencies = CodingAgentDependencies(
                request=request,
                runner=runner,
                api_catalog=api_catalog,
                concept_index=concept_index,
                static_preflight=static_preflight,
            )
            agent = create_abaqus_coding_agent(
                model,
                request,
                skill_library=skill_library_path(),
                api_catalog_mode=resolved_api_catalog_mode,
                concept_retrieval_mode=resolved_concept_retrieval_mode,
                planning_enabled=False,
                task_instructions=TASK_INSTRUCTIONS,
            )
            service = CodingRunService(
                agent=agent,
                request=request,
                dependencies=dependencies,
                clarification_provider=ScriptedClarificationProvider({}),
                verifier=BeamTensionRunVerifier(oracle_script),
                initial_snapshot=resolved.initial_snapshot,
            )
            result = await service.run()
        finally:
            await client.close()

        usage = result.outcome.usage
        abaqus_runtime_seconds = sum(
            execution.duration_seconds for execution in dependencies.executions
        )
        for name, value in {
            "model_requests": usage.model_requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "tool_calls": usage.tool_calls,
            "api_lookup_calls": usage.api_lookup_calls,
            "concept_lookup_calls": usage.concept_lookup_calls,
            "syntax_edit_checks": usage.syntax_edit_checks,
            "syntax_runtime_checks": usage.syntax_runtime_checks,
            "syntax_denials": usage.syntax_denials,
            "static_preflight_calls": usage.static_preflight_calls,
            "abaqus_calls": usage.abaqus_calls,
            "solver_calls": usage.solver_calls,
            "abaqus_runtime_s": abaqus_runtime_seconds,
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
        set_eval_attribute("api_catalog_mode", resolved_api_catalog_mode)
        set_eval_attribute("concept_retrieval_mode", resolved_concept_retrieval_mode)
        return BeamTensionObservation(
            run_id=run_id,
            outcome=result.outcome,
            verification=result.verification,
            abaqus_runtime_seconds=abaqus_runtime_seconds,
        )

    report = await build_dataset().evaluate(
        run_case,
        name=f"{model_gateway.value}-{model_name}-abaqus2024-{PROMPT_VERSION}",
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


def _write_evaluation_artifacts(
    run_root: Path,
    report: EvaluationReport[
        BeamTensionEvalInput,
        BeamTensionObservation,
        BeamTensionEvalMetadata,
    ],
    gate: BeamTensionEvalGate,
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
        description="Run the licensed Abaqus 2024 B31 beam closed-loop smoke eval."
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("var") / "runs" / "beam-tension",
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
        default="automatic_verified_only",
    )
    parser.add_argument("--concept-index-dir", type=Path)
    parser.add_argument(
        "--concept-retrieval-mode",
        choices=("off", "tool"),
        default="off",
    )
    parser.add_argument(
        "--runtime-script-policy-mode",
        choices=("off", "production_allowlist"),
        default="off",
    )
    parser.add_argument(
        "--static-preflight-mode",
        choices=("off", "advisory", "enforced_verified"),
        default="off",
    )
    parser.add_argument("--abqpy-static-wheel", type=Path)
    parser.add_argument("--static-preflight-cache-dir", type=Path)
    args = parser.parse_args()
    if args.api_catalog_mode != "off" and args.api_catalog_dir is None:
        parser.error(
            f"--api-catalog-mode {args.api_catalog_mode} requires --api-catalog-dir"
        )
    if args.api_catalog_mode == "off" and args.api_catalog_dir is not None:
        parser.error("--api-catalog-dir cannot be used with --api-catalog-mode off")
    if args.concept_retrieval_mode == "tool" and args.concept_index_dir is None:
        parser.error("--concept-retrieval-mode tool requires --concept-index-dir")
    if args.concept_retrieval_mode == "off" and args.concept_index_dir is not None:
        parser.error(
            "--concept-index-dir cannot be used with --concept-retrieval-mode off"
        )
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
            runtime_script_policy_mode=args.runtime_script_policy_mode,
            static_preflight_mode=args.static_preflight_mode,
            abqpy_static_wheel=args.abqpy_static_wheel,
            static_preflight_cache_dir=args.static_preflight_cache_dir,
        )
    )
    raise SystemExit(0 if gate.passed else 1)


if __name__ == "__main__":
    main()
