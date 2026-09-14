from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

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
from abaqus_sim_agent.evaluation.model_change_runtime import (
    execute_model_change_case,
)
from abaqus_sim_agent.evaluation.verified_plate_seed import (
    load_verified_plate_seed,
    sha256_file,
)
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.assets import plate_hole_change_oracle_script
from abaqus_sim_agent.verification.plate_change_run import (
    PlateHoleChangeRunVerifier,
    PlateHoleChangeVerificationAttempt,
)

PROMPT_VERSION = "plate-hole-change-v2"
MODEL_REQUEST_BUDGET = 36
ABAQUS_CALL_BUDGET = 12
SOLVER_CALL_BUDGET = 3

CHANGE_REQUEST = """
当前工作区提供了一份已经在 Abaqus 2024 中完成物理验收的二维矩形板拉伸模型。请理解已有模型并只修改
build_model.py，不要从空目录另写一个无关模型，也不要修改 extract_results.py。

在现有完整板上增加一个圆形贯穿孔：外轮廓仍为 (0, 0) 到 (100, 20) mm，孔心为
(50, 10) mm，孔半径为 3 mm；不得改成四分之一或二分之一对称模型。

必须保留 mm-N-MPa 单位制、二维平面应力、厚度 1 mm、E=210000 MPa、泊松比 0.3、
Abaqus/Standard、唯一 Static, General 分析步、NLGEOM=OFF、左边界 U1=0、左下角 U2=0、
右边界 U1=0.1 mm，以及 U、S、RF 输出。不得增加压力、集中力、体力或其他机械载荷。

原来的 20×4 结构化网格不再是保留项。最终网格必须只使用 CPS4R，孔边界至少由 24 条单元边离散，
并在孔附近合理加密；partition、seed 方法和总单元数由你决定。

保持现有文件和阶段合同：build_model.py 只能经 CAE noGUI 生成 plate_tension.cae 和
plate_tension.inp，不得提交 Job；Solver 必须从 plate_tension.inp 启动 plate_tension；
extract_results.py 必须经独立 abaqus python/odbAccess 读取 plate_tension.odb 并生成 results.json。

results.json 继续使用已有 extract_results.py 生成，只保留原有的 right_mean_u1、mean_s11、
left_sum_rf1。孔洞拓扑、孔边离散和应力集中由宿主的独立隐藏 oracle 从 ODB 验收，不要求、也不允许
你在 extract_results.py 中实现这些验收。所有物理输入已经给全，不要请求澄清。只有新需求、原合同、
运行产物和原结果提取都完成后才请求验证。
""".strip()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PlateChangeEvalInput(_FrozenModel):
    goal: str = CHANGE_REQUEST


class PlateChangeEvalMetadata(_FrozenModel):
    source_run_id: str


class PlateChangeObservation(_FrozenModel):
    run_id: str
    source_run_id: str
    outcome: CodingRunOutcome
    verification: PlateHoleChangeVerificationAttempt | None = None
    abaqus_runtime_seconds: float = Field(ge=0)


class PlateChangeEvalGate(_FrozenModel):
    attempts: int = Field(ge=0)
    passes: int = Field(ge=0)
    required_passes: int = Field(ge=1)
    passed: bool


@dataclass
class PlateChangeEvaluator(
    Evaluator[PlateChangeEvalInput, PlateChangeObservation, PlateChangeEvalMetadata]
):
    def evaluate(
        self,
        ctx: EvaluatorContext[
            PlateChangeEvalInput,
            PlateChangeObservation,
            PlateChangeEvalMetadata,
        ],
    ) -> dict[str, EvaluationReason | float | str]:
        output = ctx.output
        attempt = output.verification
        verification = attempt.verification if attempt is not None else None
        passed = (
            output.outcome.status is CodingRunStatus.COMPLETED
            and not output.outcome.clarifications
            and attempt is not None
            and attempt.passed
            and verification is not None
            and verification.new_requirement_passed
            and verification.regression_passed
        )
        return {
            "model_change_pass": EvaluationReason(
                value=passed,
                reason=(
                    "center-hole change and preserved baseline contract passed"
                    if passed
                    else "model change failed at "
                    + (output.outcome.failure_stage or "verification")
                ),
            ),
            "new_requirement_score": (
                1.0
                if verification is not None and verification.new_requirement_passed
                else 0.0
            ),
            "regression_score": (
                1.0
                if verification is not None and verification.regression_passed
                else 0.0
            ),
            "diff_evidence_score": (
                1.0
                if attempt is not None
                and attempt.change_report is not None
                and bool(attempt.change_report.build_model_patch)
                and not attempt.change_report.extract_results_patch
                else 0.0
            ),
            "failure_stage": output.outcome.failure_stage or "none",
        }


def build_dataset(
    source_run_id: str,
) -> Dataset[PlateChangeEvalInput, PlateChangeObservation, PlateChangeEvalMetadata]:
    return Dataset(
        name="abaqus2024_plate_hole_change_v2",
        cases=[
            Case(
                name="verified_plate_add_center_hole",
                inputs=PlateChangeEvalInput(),
                metadata=PlateChangeEvalMetadata(source_run_id=source_run_id),
            )
        ],
        evaluators=[PlateChangeEvaluator()],
    )


def gate_report(
    report: EvaluationReport[
        PlateChangeEvalInput,
        PlateChangeObservation,
        PlateChangeEvalMetadata,
    ],
    *,
    repeat: int,
    required_passes: int,
) -> PlateChangeEvalGate:
    cases = list(report.cases)
    passes = sum(_case_passed(case) for case in cases)
    attempts = len(cases) + len(report.failures)
    return PlateChangeEvalGate(
        attempts=attempts,
        passes=passes,
        required_passes=required_passes,
        passed=attempts == repeat and passes >= required_passes,
    )


async def run_evaluation(
    run_root: Path,
    *,
    source_run_dir: Path,
    model_gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    repeat: int = 3,
    required_passes: int = 2,
) -> PlateChangeEvalGate:
    if repeat < 1:
        raise ValueError("repeat must be positive")
    if required_passes < 1 or required_passes > repeat:
        raise ValueError("required_passes must be between 1 and repeat")
    run_root = await asyncio.to_thread(run_root.resolve)
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
        "api_catalog_mode": "off",
        "concept_retrieval_mode": "off",
        "planning_enabled": False,
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
        "repeat": repeat,
        "required_passes": required_passes,
        "model_request_budget": MODEL_REQUEST_BUDGET,
        "abaqus_call_budget": ABAQUS_CALL_BUDGET,
        "solver_call_budget": SOLVER_CALL_BUDGET,
        "verify_on_budget_exhaustion": True,
    }

    budget = CodingRunBudget(
        max_clarifications=0,
        max_model_requests=MODEL_REQUEST_BUDGET,
        max_api_lookup_calls=0,
        max_concept_lookup_calls=0,
        max_abaqus_calls=ABAQUS_CALL_BUDGET,
        max_solver_calls=SOLVER_CALL_BUDGET,
        wall_time_seconds=2_700.0,
    )

    async def run_case(inputs: PlateChangeEvalInput) -> PlateChangeObservation:
        runtime_result = await execute_model_change_case(
            run_root,
            seed=seed,
            goal=inputs.goal,
            run_id_prefix="plate-hole",
            verifier=PlateHoleChangeRunVerifier(
                plate_hole_change_oracle_script(),
                source_run_id=seed.outcome.run_id,
                source_run_verified=True,
                seed_texts=seed.seed_texts,
            ),
            budget=budget,
            model_gateway=model_gateway,
            model_name=model_name,
            api_key=api_key,
            http_retries=http_retries,
            task_instructions=_task_instructions(),
            verify_on_budget_exhaustion=True,
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
        set_eval_attribute("source_run_id", seed.outcome.run_id)
        set_eval_attribute("workspace", str(runtime_result.workspace))
        return PlateChangeObservation(
            run_id=runtime_result.run_id,
            source_run_id=seed.outcome.run_id,
            outcome=runtime_result.outcome,
            verification=runtime_result.verification,
            abaqus_runtime_seconds=runtime_result.abaqus_runtime_seconds,
        )

    dataset = build_dataset(seed.outcome.run_id)
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
        repeat=repeat,
        required_passes=required_passes,
    )
    _write_evaluation_artifacts(run_root, report, gate)
    report.print(include_output=True, include_reasons=True)
    return gate


def _task_instructions() -> str:
    return """
This is a verified existing-model requirement change, not a from-scratch plate
task. All physical inputs are complete, so do not return needs_input. Inspect
the existing Python files, modify only build_model.py, and leave
extract_results.py byte-for-byte unchanged. Preserve the requested CAE -> INP,
Solver -> ODB, and standalone odbAccess -> JSON stage boundary; do not create
results.json through the generic shell. The task is complete only after the
current artifacts have been generated by those three typed Abaqus tools and you
have inspected results.json. Then return ready_for_verification. If the change
cannot be supported by runtime evidence within budget, return failed.
""".strip()


def _write_evaluation_artifacts(
    run_root: Path,
    report: EvaluationReport[
        PlateChangeEvalInput,
        PlateChangeObservation,
        PlateChangeEvalMetadata,
    ],
    gate: PlateChangeEvalGate,
) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "evaluation_report.txt").write_text(
        report.render(include_output=True, include_reasons=True),
        encoding="utf-8",
    )
    summary = {
        "name": report.name,
        "experiment_metadata": report.experiment_metadata,
        **gate.model_dump(mode="json"),
        "cases": [
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


def _case_passed(case: object) -> int:
    assertions = getattr(case, "assertions", {})
    assertion = assertions.get("model_change_pass")
    evaluator_failures = getattr(case, "evaluator_failures", ())
    return int(
        assertion is not None and assertion.value is True and not evaluator_failures
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the seeded Abaqus 2024 plate-hole model-change evaluation."
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
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--required-passes", type=int, default=2)
    args = parser.parse_args()
    gate = asyncio.run(
        run_evaluation(
            args.run_root,
            source_run_dir=args.source_run_dir,
            model_gateway=args.model_provider,
            model_name=args.model,
            http_retries=args.http_retries,
            repeat=args.repeat,
            required_passes=args.required_passes,
        )
    )
    raise SystemExit(0 if gate.passed else 1)


if __name__ == "__main__":
    main()
