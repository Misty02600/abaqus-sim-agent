from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
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
    ApiCatalogBinding,
    CodingRunBudget,
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
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.workspace import ResolvedWorkspace, WorkspaceResolver
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.bar_run import (
    BarTension3DRunVerifier,
    BarTension3DVerificationAttempt,
    bar_tension_3d_oracle_script,
)
from abaqus_sim_agent.verification.bar_tension_3d import BarTension3DOracleSpec

PROMPT_VERSION = "bar-tension-3d-v1"

GOLD_REQUEST = """
请在当前空工作目录中，使用本机 Abaqus 2024 从零建立并完成一个三维矩形杆单轴拉伸分析。

模型是沿 X 方向长 100 mm、Y 和 Z 方向均为 10 mm 的三维实体，单位制为 mm-N-MPa。
材料为各向同性线弹性，弹性模量 E=210000 MPa，泊松比 ν=0.3。使用 Abaqus/Standard 的
Static, General，NLGEOM=OFF；使用 C3D8R，沿长度 20 个单元、两个横向各 2 个单元的
结构化网格，共 80 个单元。

左端面 U1=0；在左端角点 A=(0,0,0) 设置 U2=U3=0，并在左端角点
B=(0,10,0) 设置 U3=0，以最少约束消除其余刚体运动而允许泊松收缩。右端面规定
U1=0.1 mm。请求 U、S、RF。

建模脚本必须命名为 build_model.py，并且只生成 bar_tension.cae 与 bar_tension.inp，不在
CAE 脚本中提交 Job；随后通过独立 Solver 阶段生成 bar_tension.odb，再用命名为
extract_results.py 的独立 odbAccess 脚本生成 results.json。results.json 必须包含数值字段
right_mean_u1、mean_s11 和 left_sum_rf1。分析步名称可自行选择，但只能创建一个分析步。
本任务的物理输入已经完整，不需要询问补充信息；只有在结果产物和运行证据齐全后才请求验证。
""".strip()

TASK_INSTRUCTIONS = """
This benchmark is fully specified: no physical input is missing, so do not return
needs_input. The authoring script must be `build_model.py` and create
`bar_tension.cae` and `bar_tension.inp` without submitting a Job. Use the solver
tool on that input deck to create `bar_tension.odb`. A separate odbAccess script
named `extract_results.py` must create `results.json` with numeric
`right_mean_u1`, `mean_s11`, and `left_sum_rf1` fields. Return
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


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BarTension3DEvalInput(_FrozenModel):
    goal: str = GOLD_REQUEST


class BarTension3DEvalMetadata(_FrozenModel):
    oracle_spec: BarTension3DOracleSpec = Field(default_factory=BarTension3DOracleSpec)


class BarTension3DObservation(_FrozenModel):
    run_id: str
    outcome: CodingRunOutcome
    verification: BarTension3DVerificationAttempt | None = None
    manual_code_edits: bool = False
    abaqus_runtime_seconds: float = Field(ge=0)


class BarTension3DEvalGate(_FrozenModel):
    attempts: int = Field(ge=0)
    passes: int = Field(ge=0)
    required_passes: int = Field(default=2, ge=1)
    passed: bool


@dataclass
class BarTension3DClosedLoopEvaluator(
    Evaluator[
        BarTension3DEvalInput,
        BarTension3DObservation,
        BarTension3DEvalMetadata,
    ]
):
    def evaluate(
        self,
        ctx: EvaluatorContext[
            BarTension3DEvalInput,
            BarTension3DObservation,
            BarTension3DEvalMetadata,
        ],
    ) -> dict[str, EvaluationReason | float | str]:
        output = ctx.output
        verification_attempt = output.verification
        physics_checks: list[bool] = []
        geometry_checks: list[bool] = []
        topology_checks: list[bool] = []
        poisson_checks: list[bool] = []
        if (
            verification_attempt is not None
            and verification_attempt.verification is not None
        ):
            report = verification_attempt.verification
            physics_checks = [report.u1.passed, report.s11.passed, report.rf1.passed]
            geometry_checks = [
                report.x_span.passed,
                report.y_span.passed,
                report.z_span.passed,
            ]
            topology_checks = [
                report.element_type_passed,
                report.element_count_passed,
                report.node_count_passed,
                report.coordinate_layers_passed,
            ]
            poisson_checks = [
                report.y_contraction.passed,
                report.z_contraction.passed,
            ]
            physics_checks.extend(poisson_checks)
        physics_score = _pass_fraction(physics_checks)
        required_artifacts = {
            "build_model.py",
            "extract_results.py",
            "bar_tension.cae",
            "bar_tension.inp",
            "bar_tension.odb",
            "results.json",
        }
        artifacts = (
            verification_attempt.artifacts if verification_attempt is not None else ()
        )
        fresh = {artifact.relative_path for artifact in artifacts if artifact.fresh}
        artifact_score = len(required_artifacts & fresh) / len(required_artifacts)
        no_clarification = not output.outcome.clarifications
        overall = (
            no_clarification
            and not output.manual_code_edits
            and output.outcome.status is CodingRunStatus.COMPLETED
            and verification_attempt is not None
            and verification_attempt.passed
        )
        failure_stage = output.outcome.failure_stage or (
            verification_attempt.failure_stage
            if verification_attempt is not None
            else None
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
            "input_contract_score": 1.0 if no_clarification else 0.0,
            "artifact_score": artifact_score,
            "physics_score": physics_score,
            "geometry_score": _pass_fraction(geometry_checks),
            "topology_score": _pass_fraction(topology_checks),
            "poisson_score": _pass_fraction(poisson_checks),
            "failure_stage": failure_stage or "none",
        }
        measurements = (
            verification_attempt.measurements
            if verification_attempt is not None
            else None
        )
        if measurements is not None:
            evaluation.update(
                {
                    "measured_node_count": float(measurements.node_count),
                    "measured_x_span": measurements.axis_spans[0],
                    "measured_y_span": measurements.axis_spans[1],
                    "measured_z_span": measurements.axis_spans[2],
                    "measured_x_layers": float(measurements.coordinate_layer_counts[0]),
                    "measured_y_layers": float(measurements.coordinate_layer_counts[1]),
                    "measured_z_layers": float(measurements.coordinate_layer_counts[2]),
                    "measured_right_mean_u1": measurements.right_mean_u1,
                    "measured_mean_s11": measurements.mean_s11,
                    "measured_left_sum_rf1": measurements.left_sum_rf1,
                    "measured_y_contraction": measurements.y_face_mean_u2_delta,
                    "measured_z_contraction": measurements.z_face_mean_u3_delta,
                }
            )
        return evaluation


def _pass_fraction(checks: list[bool]) -> float:
    if not checks:
        return 0.0
    return sum(1.0 for passed in checks if passed) / len(checks)


def build_dataset() -> Dataset[
    BarTension3DEvalInput,
    BarTension3DObservation,
    BarTension3DEvalMetadata,
]:
    return Dataset(
        name="abaqus2024_bar_tension_3d_closed_loop_v1",
        cases=[
            Case(
                name="bar_tension_3d_fully_specified",
                inputs=BarTension3DEvalInput(),
                metadata=BarTension3DEvalMetadata(),
            )
        ],
        evaluators=[BarTension3DClosedLoopEvaluator()],
    )


def gate_report(
    report: EvaluationReport[
        BarTension3DEvalInput,
        BarTension3DObservation,
        BarTension3DEvalMetadata,
    ],
    *,
    required_passes: int = 2,
) -> BarTension3DEvalGate:
    attempts = len(report.cases) + len(report.failures)
    passes = sum(
        1
        for case in report.cases
        if (assertion := case.assertions.get("closed_loop_pass")) is not None
        and assertion.value is True
        and not case.evaluator_failures
    )
    return BarTension3DEvalGate(
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
) -> BarTension3DEvalGate:
    harness_hash_patch = apply_harness_windows_newline_hash_patch()
    harness_shell_patch = apply_harness_windows_shell_tree_patch()
    api_key = take_gateway_api_key_from_environment(model_gateway)
    oracle_script = bar_tension_3d_oracle_script()
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
        "coding_run_budget": EVAL_BUDGET.model_dump(mode="json"),
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "harness_windows_newline_hash_patch": harness_hash_patch,
        "harness_windows_shell_tree_patch": harness_shell_patch,
        "runtime": "Abaqus 2024",
        "prompt_version": PROMPT_VERSION,
        "price_version": f"genai-prices=={version('genai-prices')}",
    }

    async def run_case(inputs: BarTension3DEvalInput) -> BarTension3DObservation:
        run_id = f"bar3d-{uuid4().hex[:12]}"
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
            **EVAL_BUDGET.as_request_controls(),
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
            dependencies = CodingAgentDependencies(
                request=request,
                runner=runner,
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
                task_instructions=TASK_INSTRUCTIONS,
            )
            service = CodingRunService(
                agent=agent,
                request=request,
                dependencies=dependencies,
                clarification_provider=ScriptedClarificationProvider({}),
                verifier=BarTension3DRunVerifier(oracle_script),
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
        return BarTension3DObservation(
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
    requested: ApiCatalogMode,
    catalog: ApiCatalog | None,
) -> ApiCatalogMode:
    if requested == "off" and catalog is not None:
        raise ValueError("off mode must not receive --api-catalog-dir")
    if requested != "off" and catalog is None:
        raise ValueError(f"{requested} mode requires an explicit --api-catalog-dir")
    return requested


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_evaluation_artifacts(
    run_root: Path,
    report: EvaluationReport[
        BarTension3DEvalInput,
        BarTension3DObservation,
        BarTension3DEvalMetadata,
    ],
    gate: BarTension3DEvalGate,
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
        description="Run the licensed Abaqus 2024 3D bar closed-loop smoke eval."
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("var") / "runs" / "bar-tension-3d",
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
        )
    )
    raise SystemExit(0 if gate.passed else 1)


if __name__ == "__main__":
    main()
