from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.agent import CODER_GENERAL_SHELL_POLICY_VERSION
from abaqus_sim_agent.coding.api_lookup import ApiCatalogMode
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunOutcome,
    CodingRunStatus,
)
from abaqus_sim_agent.coding.harness_compat import (
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
)
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    DeepSeekGateway,
    gateway_base_url,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.probe import (
    PROBE_PROTOCOL_VERSION,
    ProbeProtocolMode,
)
from abaqus_sim_agent.evaluation.load_change import (
    CHANGE_REQUEST as LOAD_CHANGE_REQUEST,
)
from abaqus_sim_agent.evaluation.material_change import (
    CHANGE_REQUEST as MATERIAL_CHANGE_REQUEST,
)
from abaqus_sim_agent.evaluation.model_change_runtime import (
    execute_model_change_case,
)
from abaqus_sim_agent.evaluation.plate_change import (
    CHANGE_REQUEST as HOLE_CHANGE_REQUEST,
)
from abaqus_sim_agent.evaluation.verified_plate_seed import (
    VerifiedPlateSeed,
    load_verified_plate_seed,
    sha256_file,
)
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.assets import (
    plate_hole_change_oracle_script,
    plate_load_change_oracle_script,
    plate_material_change_oracle_script,
)
from abaqus_sim_agent.verification.load_change_run import (
    LoadModeChangeRunVerifier,
    LoadModeChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.material_change_run import (
    TwoMaterialPlateChangeRunVerifier,
    TwoMaterialPlateChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.plate_change_run import (
    PlateHoleChangeRunVerifier,
    PlateHoleChangeVerificationAttempt,
)

CaseId = Literal["center_hole", "dual_material", "load_mode"]
PROMPT_VERSION = "plate-model-change-suite-v2"
MODEL_REQUEST_BUDGET = 36
ABAQUS_CALL_BUDGET = 12
SOLVER_CALL_BUDGET = 3


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ModelChangeSuiteInput(_FrozenModel):
    case_id: CaseId
    goal: str = Field(min_length=1)


class ModelChangeSuiteMetadata(_FrozenModel):
    case_id: CaseId
    source_run_id: str


ChangeAttempt = Annotated[
    PlateHoleChangeVerificationAttempt
    | TwoMaterialPlateChangeVerificationAttempt
    | LoadModeChangeVerificationAttempt,
    Field(discriminator="case_id"),
]


class ModelChangeSuiteObservation(_FrozenModel):
    case_id: CaseId
    run_id: str
    source_run_id: str
    outcome: CodingRunOutcome
    verification: ChangeAttempt | None = None
    abaqus_runtime_seconds: float = Field(ge=0)


class ModelChangeCaseGate(_FrozenModel):
    case_id: CaseId
    attempts: int = Field(ge=0)
    passes: int = Field(ge=0)
    required_passes: int = Field(ge=1)
    passed: bool


class ModelChangeSuiteGate(_FrozenModel):
    cases: tuple[ModelChangeCaseGate, ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class ModelChangeCaseSpec:
    case_id: CaseId
    goal: str
    run_id_prefix: str
    extractor_should_change: bool
    probe_target_scripts: tuple[str, ...]


CASE_SPECS = (
    ModelChangeCaseSpec(
        case_id="center_hole",
        goal=HOLE_CHANGE_REQUEST,
        run_id_prefix="plate-hole",
        extractor_should_change=False,
        probe_target_scripts=("build_model.py",),
    ),
    ModelChangeCaseSpec(
        case_id="dual_material",
        goal=MATERIAL_CHANGE_REQUEST,
        run_id_prefix="plate-material",
        extractor_should_change=True,
        probe_target_scripts=("build_model.py", "extract_results.py"),
    ),
    ModelChangeCaseSpec(
        case_id="load_mode",
        goal=LOAD_CHANGE_REQUEST,
        run_id_prefix="plate-load",
        extractor_should_change=False,
        probe_target_scripts=("build_model.py",),
    ),
)


@dataclass
class ModelChangeSuiteEvaluator(
    Evaluator[
        ModelChangeSuiteInput,
        ModelChangeSuiteObservation,
        ModelChangeSuiteMetadata,
    ]
):
    def evaluate(
        self,
        ctx: EvaluatorContext[
            ModelChangeSuiteInput,
            ModelChangeSuiteObservation,
            ModelChangeSuiteMetadata,
        ],
    ) -> dict[str, EvaluationReason | float | str]:
        output = ctx.output
        attempt = output.verification
        spec = _case_by_id(output.case_id)
        diff_ok = False
        if attempt is not None and attempt.change_report is not None:
            diff_ok = bool(attempt.change_report.build_model_patch) and (
                bool(attempt.change_report.extract_results_patch)
                == spec.extractor_should_change
            )
        passed = (
            output.outcome.status is CodingRunStatus.COMPLETED
            and not output.outcome.clarifications
            and attempt is not None
            and attempt.passed
            and diff_ok
        )
        return {
            "model_change_pass": EvaluationReason(
                value=passed,
                reason=(
                    f"{output.case_id} change and regression contract passed"
                    if passed
                    else f"{output.case_id} failed at "
                    + (output.outcome.failure_stage or "verification")
                ),
            ),
            "diff_contract_score": 1.0 if diff_ok else 0.0,
            "case_id": output.case_id,
            "failure_stage": output.outcome.failure_stage or "none",
        }


def build_dataset(
    source_run_id: str,
    *,
    case_ids: tuple[CaseId, ...] | None = None,
) -> Dataset[
    ModelChangeSuiteInput,
    ModelChangeSuiteObservation,
    ModelChangeSuiteMetadata,
]:
    selected = set(case_ids or tuple(spec.case_id for spec in CASE_SPECS))
    return Dataset(
        name="abaqus2024_verified_plate_model_change_suite_v2",
        cases=[
            Case(
                name=spec.case_id,
                inputs=ModelChangeSuiteInput(case_id=spec.case_id, goal=spec.goal),
                metadata=ModelChangeSuiteMetadata(
                    case_id=spec.case_id,
                    source_run_id=source_run_id,
                ),
            )
            for spec in CASE_SPECS
            if spec.case_id in selected
        ],
        evaluators=[ModelChangeSuiteEvaluator()],
    )


def gate_report(
    report: EvaluationReport[
        ModelChangeSuiteInput,
        ModelChangeSuiteObservation,
        ModelChangeSuiteMetadata,
    ],
    *,
    case_ids: tuple[CaseId, ...],
    repeat: int,
    required_passes: int,
) -> ModelChangeSuiteGate:
    gates: list[ModelChangeCaseGate] = []
    for case_id in case_ids:
        completed = [
            case for case in report.cases if _base_case_name(case.name) == case_id
        ]
        failed = [
            failure
            for failure in report.failures
            if _base_case_name(failure.name) == case_id
        ]
        passes = sum(_case_passed(case) for case in completed)
        attempts = len(completed) + len(failed)
        gates.append(
            ModelChangeCaseGate(
                case_id=case_id,
                attempts=attempts,
                passes=passes,
                required_passes=required_passes,
                passed=attempts == repeat and passes >= required_passes,
            )
        )
    return ModelChangeSuiteGate(
        cases=tuple(gates),
        passed=bool(gates) and all(gate.passed for gate in gates),
    )


async def run_evaluation(
    run_root: Path,
    *,
    source_run_dir: Path,
    model_gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    api_catalog_mode: ApiCatalogMode = "off",
    api_catalog_dir: Path | None = None,
    probe_protocol_mode: ProbeProtocolMode = "off",
    case_ids: tuple[CaseId, ...] | None = None,
    repeat: int = 3,
    required_passes: int = 2,
    model_request_budget: int = MODEL_REQUEST_BUDGET,
    abaqus_call_budget: int = ABAQUS_CALL_BUDGET,
    solver_call_budget: int = SOLVER_CALL_BUDGET,
) -> ModelChangeSuiteGate:
    if repeat < 1:
        raise ValueError("repeat must be positive")
    if required_passes < 1 or required_passes > repeat:
        raise ValueError("required_passes must be between 1 and repeat")
    _validate_evaluation_budgets(
        model_request_budget=model_request_budget,
        abaqus_call_budget=abaqus_call_budget,
        solver_call_budget=solver_call_budget,
    )
    selected = case_ids or tuple(spec.case_id for spec in CASE_SPECS)
    if len(set(selected)) != len(selected):
        raise ValueError("case IDs must be unique")
    run_root = await asyncio.to_thread(run_root.resolve)
    api_catalog = await asyncio.to_thread(
        _load_api_catalog,
        run_root,
        api_catalog_mode,
        api_catalog_dir,
    )
    seed = await asyncio.to_thread(
        load_verified_plate_seed,
        source_run_dir,
        forbidden_root=run_root,
    )
    harness_hash_patch = apply_harness_windows_newline_hash_patch()
    harness_shell_patch = apply_harness_windows_shell_tree_patch()
    api_key = take_gateway_api_key_from_environment(model_gateway)
    skill_digest = await asyncio.to_thread(
        sha256_file,
        skill_library_path() / "abaqus-2024-coding" / "SKILL.md",
    )
    budget = CodingRunBudget(
        max_clarifications=0,
        max_model_requests=model_request_budget,
        max_api_lookup_calls=3 if api_catalog is not None else 0,
        max_concept_lookup_calls=0,
        max_abaqus_calls=abaqus_call_budget,
        max_solver_calls=solver_call_budget,
        wall_time_seconds=2_700.0,
    )
    experiment_metadata = {
        "model_gateway": model_gateway.value,
        "model": model_name,
        "base_url": gateway_base_url(model_gateway),
        "protocol": "openai-chat-completions",
        "profile_family": "pydantic-ai-deepseek",
        "http_retries": http_retries,
        "http_timeout_seconds": DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
        "api_catalog_mode": api_catalog_mode,
        "api_catalog_cards_sha256": (
            api_catalog.manifest.cards_sha256
            if api_catalog is not None and api_catalog.manifest is not None
            else None
        ),
        "api_catalog_card_count": (
            len(api_catalog.cards) if api_catalog is not None else 0
        ),
        "concept_retrieval_mode": "off",
        "probe_protocol_mode": probe_protocol_mode,
        "probe_protocol_version": (
            PROBE_PROTOCOL_VERSION if probe_protocol_mode == "typed" else None
        ),
        "probe_target_scripts": (
            {
                spec.case_id: list(spec.probe_target_scripts)
                for spec in CASE_SPECS
                if spec.case_id in selected
            }
            if probe_protocol_mode == "typed"
            else None
        ),
        "planning_enabled": False,
        "coder_general_shell_policy": CODER_GENERAL_SHELL_POLICY_VERSION,
        "source_run_id": seed.outcome.run_id,
        "source_request_sha256": seed.provenance.request_sha256,
        "source_outcome_sha256": seed.provenance.outcome_sha256,
        "source_verification_sha256": seed.provenance.verification_sha256,
        "source_artifact_sha256": seed.provenance.artifact_sha256,
        "skill_sha256": skill_digest,
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "harness_windows_newline_hash_patch": harness_hash_patch,
        "harness_windows_shell_tree_patch": harness_shell_patch,
        "runtime": "Abaqus 2024",
        "prompt_version": PROMPT_VERSION,
        "case_ids": list(selected),
        "repeat": repeat,
        "required_passes_per_case": required_passes,
        "model_request_budget": model_request_budget,
        "api_lookup_call_budget": budget.max_api_lookup_calls,
        "abaqus_call_budget": abaqus_call_budget,
        "solver_call_budget": solver_call_budget,
        "verify_on_budget_exhaustion": True,
    }

    async def run_case(
        inputs: ModelChangeSuiteInput,
    ) -> ModelChangeSuiteObservation:
        spec = _case_by_id(inputs.case_id)
        runtime_result = await _execute_case(
            spec,
            run_root=run_root,
            seed=seed,
            budget=budget,
            model_gateway=model_gateway,
            model_name=model_name,
            api_key=api_key,
            http_retries=http_retries,
            api_catalog=api_catalog,
            api_catalog_mode=api_catalog_mode,
            probe_protocol_mode=probe_protocol_mode,
        )
        usage = runtime_result.outcome.usage
        for name, value in {
            "model_requests": usage.model_requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "tool_calls": usage.tool_calls,
            "abaqus_calls": usage.abaqus_calls,
            "solver_calls": usage.solver_calls,
            "abaqus_runtime_s": runtime_result.abaqus_runtime_seconds,
        }.items():
            increment_eval_metric(name, value)
        set_eval_attribute("run_id", runtime_result.run_id)
        set_eval_attribute("case_id", spec.case_id)
        set_eval_attribute("source_run_id", seed.outcome.run_id)
        set_eval_attribute("workspace", str(runtime_result.workspace))
        return ModelChangeSuiteObservation(
            case_id=spec.case_id,
            run_id=runtime_result.run_id,
            source_run_id=seed.outcome.run_id,
            outcome=runtime_result.outcome,
            verification=runtime_result.verification,
            abaqus_runtime_seconds=runtime_result.abaqus_runtime_seconds,
        )

    dataset = build_dataset(seed.outcome.run_id, case_ids=selected)
    report = await dataset.evaluate(
        run_case,
        name=f"{model_gateway.value}-{model_name}-abaqus2024-{PROMPT_VERSION}",
        repeat=repeat,
        max_concurrency=1,
        retry_task=None,
        retry_evaluators=None,
        metadata=experiment_metadata,
    )
    gate = gate_report(
        report,
        case_ids=selected,
        repeat=repeat,
        required_passes=required_passes,
    )
    _write_evaluation_artifacts(run_root, report, gate)
    report.print(include_output=True, include_reasons=True)
    return gate


async def _execute_case(
    spec: ModelChangeCaseSpec,
    *,
    run_root: Path,
    seed: VerifiedPlateSeed,
    budget: CodingRunBudget,
    model_gateway: DeepSeekGateway,
    model_name: str,
    api_key,
    http_retries: int,
    api_catalog: ApiCatalog | None,
    api_catalog_mode: ApiCatalogMode,
    probe_protocol_mode: ProbeProtocolMode,
):
    common = {
        "run_root": run_root,
        "seed": seed,
        "goal": spec.goal,
        "run_id_prefix": spec.run_id_prefix,
        "budget": budget,
        "model_gateway": model_gateway,
        "model_name": model_name,
        "api_key": api_key,
        "http_retries": http_retries,
        "api_catalog": api_catalog,
        "api_catalog_mode": api_catalog_mode,
        "probe_protocol_mode": probe_protocol_mode,
        "probe_target_scripts": spec.probe_target_scripts,
        "verify_on_budget_exhaustion": True,
    }
    if spec.case_id == "center_hole":
        return await execute_model_change_case(
            verifier=PlateHoleChangeRunVerifier(
                plate_hole_change_oracle_script(),
                source_run_id=seed.outcome.run_id,
                source_run_verified=True,
                seed_texts=seed.seed_texts,
            ),
            **common,
        )
    if spec.case_id == "dual_material":
        return await execute_model_change_case(
            verifier=TwoMaterialPlateChangeRunVerifier(
                plate_material_change_oracle_script(),
                source_run_id=seed.outcome.run_id,
                source_run_verified=True,
                seed_texts=seed.seed_texts,
            ),
            **common,
        )
    return await execute_model_change_case(
        verifier=LoadModeChangeRunVerifier(
            plate_load_change_oracle_script(),
            source_run_id=seed.outcome.run_id,
            source_run_verified=True,
            seed_texts=seed.seed_texts,
        ),
        **common,
    )


def _case_by_id(case_id: CaseId) -> ModelChangeCaseSpec:
    return next(spec for spec in CASE_SPECS if spec.case_id == case_id)


def _load_api_catalog(
    run_root: Path,
    mode: ApiCatalogMode,
    api_catalog_dir: Path | None,
) -> ApiCatalog | None:
    if mode == "off":
        if api_catalog_dir is not None:
            raise ValueError("off mode must not receive an API catalog")
        return None
    if api_catalog_dir is None:
        raise ValueError(f"{mode} mode requires --api-catalog-dir")
    catalog_path = api_catalog_dir.resolve(strict=True)
    resolved_run_root = run_root.resolve()
    if catalog_path == resolved_run_root or catalog_path.is_relative_to(
        resolved_run_root
    ):
        raise ValueError("API catalog must be outside the evaluation run root")
    return ApiCatalog.open(catalog_path)


def _validate_evaluation_budgets(
    *,
    model_request_budget: int,
    abaqus_call_budget: int,
    solver_call_budget: int,
) -> None:
    if model_request_budget < 1:
        raise ValueError("model request budget must be positive")
    if abaqus_call_budget < 1:
        raise ValueError("Abaqus call budget must be positive")
    if solver_call_budget < 1:
        raise ValueError("solver call budget must be positive")
    if solver_call_budget > abaqus_call_budget:
        raise ValueError("solver call budget must not exceed Abaqus call budget")


def _base_case_name(name: str) -> str:
    return name.split(" [", maxsplit=1)[0]


def _case_passed(case: object) -> int:
    assertions = getattr(case, "assertions", {})
    assertion = assertions.get("model_change_pass")
    evaluator_failures = getattr(case, "evaluator_failures", ())
    return int(
        assertion is not None and assertion.value is True and not evaluator_failures
    )


def _write_evaluation_artifacts(
    run_root: Path,
    report: EvaluationReport[
        ModelChangeSuiteInput,
        ModelChangeSuiteObservation,
        ModelChangeSuiteMetadata,
    ],
    gate: ModelChangeSuiteGate,
) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "evaluation_report.txt").write_text(
        report.render(include_output=True, include_reasons=True), encoding="utf-8"
    )
    summary = {
        "name": report.name,
        "experiment_metadata": report.experiment_metadata,
        **gate.model_dump(mode="json"),
        "attempts": [
            {
                "name": case.name,
                "model_change_pass": (
                    case.assertions["model_change_pass"].value
                    if "model_change_pass" in case.assertions
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
        gate.model_dump_json(indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the verified-seed Abaqus 2024 model-change suite."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-run-dir", type=Path, required=True)
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        default=DeepSeekGateway.OPENCODE_GO,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--http-retries", type=int, default=DEFAULT_MODEL_HTTP_RETRIES)
    parser.add_argument(
        "--api-catalog-mode",
        choices=(
            "off",
            "full",
            "automatic_verified_only",
            "automatic_exact_signature",
        ),
        default="off",
    )
    parser.add_argument("--api-catalog-dir", type=Path)
    parser.add_argument(
        "--probe-protocol-mode",
        choices=("off", "typed"),
        default="off",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        choices=tuple(spec.case_id for spec in CASE_SPECS),
        dest="case_ids",
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--required-passes", type=int, default=2)
    parser.add_argument(
        "--model-request-budget",
        type=int,
        default=MODEL_REQUEST_BUDGET,
    )
    parser.add_argument(
        "--abaqus-call-budget",
        type=int,
        default=ABAQUS_CALL_BUDGET,
    )
    parser.add_argument(
        "--solver-call-budget",
        type=int,
        default=SOLVER_CALL_BUDGET,
    )
    args = parser.parse_args()
    gate = asyncio.run(
        run_evaluation(
            args.run_root,
            source_run_dir=args.source_run_dir,
            model_gateway=args.model_provider,
            model_name=args.model,
            http_retries=args.http_retries,
            api_catalog_mode=args.api_catalog_mode,
            api_catalog_dir=args.api_catalog_dir,
            probe_protocol_mode=args.probe_protocol_mode,
            case_ids=tuple(args.case_ids) if args.case_ids else None,
            repeat=args.repeat,
            required_passes=args.required_passes,
            model_request_budget=args.model_request_budget,
            abaqus_call_budget=args.abaqus_call_budget,
            solver_call_budget=args.solver_call_budget,
        )
    )
    raise SystemExit(0 if gate.passed else 1)


if __name__ == "__main__":
    main()
