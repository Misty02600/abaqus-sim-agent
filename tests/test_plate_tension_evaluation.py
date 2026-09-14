import asyncio
import json
from collections.abc import Iterator
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_evals import Case, Dataset

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding import (
    ClarificationEvent,
    CodingRunOutcome,
    CodingRunStatus,
)
from abaqus_sim_agent.coding.service import CodingRunInfrastructureError
from abaqus_sim_agent.evaluation import plate_tension as plate_tension_module
from abaqus_sim_agent.evaluation.plate_tension import (
    GOLD_REQUEST,
    GOLD_TASK_INSTRUCTIONS,
    PROMPT_VERSION,
    ClosedLoopEvaluator,
    PlateTensionEvalInput,
    PlateTensionEvalMetadata,
    PlateTensionObservation,
    _resolve_eval_api_catalog_mode,
    _run_with_optional_infrastructure_resume,
    _tools_before_first_clarification,
    _write_evaluation_artifacts,
    build_policy_proceed_dataset,
    gate_report,
    run_evaluation,
)
from abaqus_sim_agent.verification.plate_tension import (
    PlateTensionMeasurements,
    verify_plate_tension,
)
from abaqus_sim_agent.verification.run import PlateTensionVerificationAttempt


def test_checked_in_gold_request_matches_evaluation_prompt() -> None:
    request_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "plate_tension_request.json"
    )
    checked_in = json.loads(request_path.read_text(encoding="utf-8"))["goal"]

    assert "".join(checked_in.split()) == "".join(GOLD_REQUEST.split())
    assert PROMPT_VERSION == "plate-tension-v4"
    assert PlateTensionEvalInput().task_instructions == GOLD_TASK_INSTRUCTIONS
    assert "plate_thickness" in GOLD_TASK_INSTRUCTIONS
    assert (
        signature(run_evaluation).parameters["resume_infrastructure_once"].default
        is False
    )


def test_history_reports_every_tool_before_first_clarification(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_bytes(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart("load_capability", {"id": "abaqus-2024-coding"}),
                        ToolCallPart("list_directory", {"path": "."}),
                        ToolCallPart(
                            "final_result_NeedsInputDecision",
                            {
                                "status": "needs_input",
                                "clarification": {
                                    "field_id": "plate_thickness",
                                    "kind": "missing_physical_input",
                                    "question": "What thickness?",
                                    "impact": "Thickness changes the response.",
                                },
                                "summary": "Thickness is required.",
                            },
                        ),
                    ]
                )
            ]
        )
    )

    assert _tools_before_first_clarification(history_path) == (
        "load_capability",
        "list_directory",
    )


def test_plate_catalog_modes_require_consistent_catalog_presence() -> None:
    catalog = ApiCatalog([], runtime_release="2024")

    assert _resolve_eval_api_catalog_mode(None, None) == "off"
    assert _resolve_eval_api_catalog_mode(None, catalog) == "full"
    assert (
        _resolve_eval_api_catalog_mode("automatic_verified_only", catalog)
        == "automatic_verified_only"
    )
    assert (
        _resolve_eval_api_catalog_mode("automatic_exact_signature", catalog)
        == "automatic_exact_signature"
    )
    with pytest.raises(ValueError, match="requires"):
        _resolve_eval_api_catalog_mode("automatic_verified_only", None)
    with pytest.raises(ValueError, match="requires"):
        _resolve_eval_api_catalog_mode("automatic_exact_signature", None)


def _observation(
    *,
    passed: bool,
    clarified: bool = True,
) -> PlateTensionObservation:
    freshness = {
        "build_model.py": True,
        "extract_results.py": True,
        "plate_tension.cae": True,
        "plate_tension.inp": True,
        "plate_tension.odb": True,
        "results.json": True,
    }
    measurements = PlateTensionMeasurements(
        odb_path="plate_tension.odb",
        step_name="Tension",
        frame_index=1,
        instance_name="PLATE-1",
        element_type_counts={"CPS4R": 80},
        right_mean_u1=0.1 if passed else 0.08,
        mean_s11=210.0,
        left_sum_rf1=-4200.0,
    )
    verification = verify_plate_tension(
        measurements,
        job_completed=True,
        artifact_freshness=freshness,
        agent_results={
            "right_mean_u1": measurements.right_mean_u1,
            "mean_s11": measurements.mean_s11,
            "left_sum_rf1": measurements.left_sum_rf1,
        },
    )
    from abaqus_sim_agent.coding import ArtifactKind, ArtifactRef

    artifacts = tuple(
        ArtifactRef(
            relative_path=path,
            size_bytes=1,
            sha256="0" * 64,
            kind=ArtifactKind.OTHER,
            fresh=True,
        )
        for path in freshness
    )
    return PlateTensionObservation(
        run_id="run-1",
        outcome=CodingRunOutcome(
            run_id="run-1",
            status=(CodingRunStatus.COMPLETED if passed else CodingRunStatus.FAILED),
            summary="synthetic",
            clarifications=(
                (
                    ClarificationEvent(
                        field_id="plate_thickness",
                        question="What thickness?",
                        kind="missing_physical_input",
                        impact="Thickness changes the plane-stress section response.",
                        answer="1 mm",
                        source="scripted",
                        abaqus_calls_before_answer=0,
                    ),
                )
                if clarified
                else ()
            ),
            artifacts=artifacts,
            failure_stage=None if passed else "verification",
        ),
        verification=PlateTensionVerificationAttempt(
            passed=verification.passed,
            feedback="synthetic",
            artifacts=artifacts,
            measurements=measurements,
            verification=verification,
            failure_stage=None if passed else "verification",
        ),
        abaqus_runtime_seconds=1.0,
    )


def test_closed_loop_evaluator_and_gate_require_two_of_three(
    tmp_path: Path,
) -> None:
    outputs = iter(
        [
            _observation(passed=True),
            _observation(passed=False),
            _observation(passed=True),
        ]
    )

    async def task(_inputs: PlateTensionEvalInput) -> PlateTensionObservation:
        return next(outputs)

    dataset = Dataset(
        name="synthetic",
        cases=[
            Case(
                name="plate",
                inputs=PlateTensionEvalInput(),
                metadata=PlateTensionEvalMetadata(),
            )
        ],
        evaluators=[ClosedLoopEvaluator()],
    )
    report = asyncio.run(
        dataset.evaluate(
            task,
            repeat=3,
            max_concurrency=1,
            progress=False,
            metadata={"model_gateway": "opencode-zen"},
        )
    )

    gate = gate_report(report)
    _write_evaluation_artifacts(tmp_path, report, gate)
    summary = json.loads(
        (tmp_path / "evaluation_summary.json").read_text(encoding="utf-8")
    )

    assert gate.attempts == 3
    assert gate.passes == 2
    assert gate.passed
    assert summary["experiment_metadata"] == {"model_gateway": "opencode-zen"}


def test_policy_proceed_dataset_requires_zero_clarification() -> None:
    dataset = build_policy_proceed_dataset("plate-element-derived")
    case = dataset.cases[0]

    assert case.metadata is not None
    assert case.inputs.scripted_answers == {}
    assert case.inputs.task_instructions is not None
    assert case.metadata.required_missing_field is None
    assert case.inputs.oracle_spec.allowed_element_types == ("CPS4", "CPS4R")
    assert case.metadata.oracle_spec == case.inputs.oracle_spec

    async def task(_inputs: PlateTensionEvalInput) -> PlateTensionObservation:
        return _observation(passed=True, clarified=False)

    report = asyncio.run(
        dataset.evaluate(task, repeat=3, max_concurrency=1, progress=False)
    )

    assert gate_report(report).passed


def test_policy_proceed_dataset_rejects_needs_input_case() -> None:
    with pytest.raises(ValueError, match="non-proceed"):
        build_policy_proceed_dataset("plate-out-of-plane-ambiguous")


def test_gate_counts_task_failure_as_an_attempt_and_not_a_pass() -> None:
    outputs: Iterator[PlateTensionObservation | RuntimeError] = iter(
        [
            _observation(passed=True),
            RuntimeError("licensed runtime unavailable"),
            _observation(passed=False),
        ]
    )

    async def task(_inputs: PlateTensionEvalInput) -> PlateTensionObservation:
        output = next(outputs)
        if isinstance(output, RuntimeError):
            raise output
        return output

    dataset = Dataset(
        name="synthetic-task-failure",
        cases=[
            Case(
                name="plate",
                inputs=PlateTensionEvalInput(),
                metadata=PlateTensionEvalMetadata(),
            )
        ],
        evaluators=[ClosedLoopEvaluator()],
    )
    report = asyncio.run(
        dataset.evaluate(task, repeat=3, max_concurrency=1, progress=False)
    )

    gate = gate_report(report)

    assert len(report.failures) == 1
    assert gate.attempts == 3
    assert gate.passes == 1
    assert not gate.passed


def _raise_provider_infrastructure_error() -> NoReturn:
    try:
        raise ModelAPIError("deepseek-v4-flash", "provider timeout")
    except ModelAPIError as exc:
        raise CodingRunInfrastructureError("provider timeout") from exc


def test_optional_infrastructure_resume_reuses_same_task_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resume_flags: list[bool] = []
    monkeypatch.setattr(
        plate_tension_module,
        "load_resume_state",
        lambda _state_dir: SimpleNamespace(status="interrupted"),
    )

    async def run_attempt(resume: bool) -> str:
        resume_flags.append(resume)
        if not resume:
            _raise_provider_infrastructure_error()
        return "completed"

    result, resume_count = asyncio.run(
        _run_with_optional_infrastructure_resume(
            run_attempt,
            enabled=True,
            state_dir=tmp_path,
        )
    )

    assert result == "completed"
    assert resume_count == 1
    assert resume_flags == [False, True]


def test_optional_infrastructure_resume_is_default_off_and_never_loops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disabled_flags: list[bool] = []
    monkeypatch.setattr(
        plate_tension_module,
        "load_resume_state",
        lambda _state_dir: SimpleNamespace(status="interrupted"),
    )

    async def fail_disabled(resume: bool) -> str:
        disabled_flags.append(resume)
        _raise_provider_infrastructure_error()

    with pytest.raises(CodingRunInfrastructureError, match="provider timeout"):
        asyncio.run(
            _run_with_optional_infrastructure_resume(
                fail_disabled,
                enabled=False,
                state_dir=tmp_path,
            )
        )
    assert disabled_flags == [False]

    enabled_flags: list[bool] = []

    async def fail_twice(resume: bool) -> str:
        enabled_flags.append(resume)
        _raise_provider_infrastructure_error()

    with pytest.raises(CodingRunInfrastructureError, match="provider timeout"):
        asyncio.run(
            _run_with_optional_infrastructure_resume(
                fail_twice,
                enabled=True,
                state_dir=tmp_path,
            )
        )
    assert enabled_flags == [False, True]


def test_optional_infrastructure_resume_rejects_non_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resume_flags: list[bool] = []
    monkeypatch.setattr(
        plate_tension_module,
        "load_resume_state",
        lambda _state_dir: SimpleNamespace(status="interrupted"),
    )

    async def run_attempt(resume: bool) -> str:
        resume_flags.append(resume)
        try:
            raise RuntimeError("verifier defect")
        except RuntimeError as exc:
            raise CodingRunInfrastructureError("verifier defect") from exc

    with pytest.raises(CodingRunInfrastructureError, match="verifier defect"):
        asyncio.run(
            _run_with_optional_infrastructure_resume(
                run_attempt,
                enabled=True,
                state_dir=tmp_path,
            )
        )
    assert resume_flags == [False]


def test_checked_in_corrected_plate_catalog_evidence_keeps_smoke_limits() -> None:
    evidence_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "plate_tension_catalog_evaluation_20260823_v4.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence["arms"]["skill_only"]["passes"] == 2
    assert evidence["arms"]["catalog_full"]["passes"] == 2
    assert evidence["arms"]["automatic_verified_only"]["passes"] == 3
    assert evidence["arms"]["automatic_verified_only"]["delivered_runtime_notes"] == 3
    assert evidence["interpretation"]["limits"]
