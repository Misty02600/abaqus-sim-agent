from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic_evals import Case, Dataset

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding import (
    ArtifactKind,
    ArtifactRef,
    CodingRunOutcome,
    CodingRunStatus,
)
from abaqus_sim_agent.evaluation.bar_tension_3d import (
    EVAL_BUDGET,
    GOLD_REQUEST,
    PROMPT_VERSION,
    TASK_INSTRUCTIONS,
    BarTension3DClosedLoopEvaluator,
    BarTension3DEvalInput,
    BarTension3DEvalMetadata,
    BarTension3DObservation,
    _resolve_eval_api_catalog_mode,
    _resolve_eval_concept_retrieval_mode,
    _write_evaluation_artifacts,
    gate_report,
)
from abaqus_sim_agent.verification.bar_run import BarTension3DVerificationAttempt
from abaqus_sim_agent.verification.bar_tension_3d import (
    BarTension3DMeasurements,
    verify_bar_tension_3d,
)


def test_checked_in_bar_request_matches_evaluation_prompt() -> None:
    request_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "bar_tension_3d_request.json"
    )
    checked_in = json.loads(request_path.read_text(encoding="utf-8"))["goal"]

    assert "".join(checked_in.split()) == "".join(GOLD_REQUEST.split())
    assert PROMPT_VERSION == "bar-tension-3d-v1"
    assert "no physical input is missing" in TASK_INSTRUCTIONS
    assert "do not return\nneeds_input" in TASK_INSTRUCTIONS
    assert EVAL_BUDGET.max_clarifications == 0
    assert EVAL_BUDGET.max_model_requests == 40
    assert EVAL_BUDGET.max_abaqus_calls == 12
    assert EVAL_BUDGET.max_solver_calls == 3
    assert EVAL_BUDGET.wall_time_seconds == 2_700.0


def test_bar_catalog_and_concept_modes_require_explicit_artifacts() -> None:
    catalog = ApiCatalog([], runtime_release="2024")

    assert _resolve_eval_api_catalog_mode("off", None) == "off"
    assert (
        _resolve_eval_api_catalog_mode("automatic_verified_only", catalog)
        == "automatic_verified_only"
    )
    assert (
        _resolve_eval_api_catalog_mode("automatic_exact_signature", catalog)
        == "automatic_exact_signature"
    )
    with pytest.raises(ValueError, match="explicit --api-catalog-dir"):
        _resolve_eval_api_catalog_mode("automatic_verified_only", None)
    with pytest.raises(ValueError, match="explicit --api-catalog-dir"):
        _resolve_eval_api_catalog_mode("automatic_exact_signature", None)
    with pytest.raises(ValueError, match="must not receive"):
        _resolve_eval_api_catalog_mode("off", catalog)

    assert _resolve_eval_concept_retrieval_mode("off", None) == "off"
    with pytest.raises(ValueError, match="requires"):
        _resolve_eval_concept_retrieval_mode("tool", None)


def _observation(
    *,
    passed: bool,
    y_contraction: float = -0.003,
) -> BarTension3DObservation:
    freshness = {
        "build_model.py": True,
        "extract_results.py": True,
        "bar_tension.cae": True,
        "bar_tension.inp": True,
        "bar_tension.odb": True,
        "results.json": True,
    }
    measurements = BarTension3DMeasurements(
        odb_path="bar_tension.odb",
        step_name="Tension",
        frame_index=1,
        instance_name="BAR-1",
        element_type_counts={"C3D8R": 80},
        node_count=189,
        axis_spans=(100.0, 10.0, 10.0),
        coordinate_layer_counts=(21, 3, 3),
        right_mean_u1=0.1 if passed else 0.08,
        mean_s11=210.0,
        left_sum_rf1=-21000.0,
        y_face_mean_u2_delta=y_contraction,
        z_face_mean_u3_delta=-0.003,
    )
    verification = verify_bar_tension_3d(
        measurements,
        job_completed=True,
        artifact_freshness=freshness,
        agent_results={
            "right_mean_u1": measurements.right_mean_u1,
            "mean_s11": measurements.mean_s11,
            "left_sum_rf1": measurements.left_sum_rf1,
        },
    )
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
    return BarTension3DObservation(
        run_id="bar3d-1",
        outcome=CodingRunOutcome(
            run_id="bar3d-1",
            status=CodingRunStatus.COMPLETED,
            summary="synthetic",
            artifacts=artifacts,
        ),
        verification=BarTension3DVerificationAttempt(
            passed=verification.passed,
            feedback="synthetic",
            artifacts=artifacts,
            measurements=measurements,
            verification=verification,
            failure_stage=None if verification.passed else "verification",
        ),
        abaqus_runtime_seconds=1.0,
    )


def test_bar_evaluator_exposes_geometry_topology_and_poisson_measurements() -> None:
    async def task(_inputs: BarTension3DEvalInput) -> BarTension3DObservation:
        return _observation(passed=True)

    dataset = Dataset(
        name="synthetic-bar-metrics",
        cases=[
            Case(
                name="bar",
                inputs=BarTension3DEvalInput(),
                metadata=BarTension3DEvalMetadata(),
            )
        ],
        evaluators=[BarTension3DClosedLoopEvaluator()],
    )
    report = asyncio.run(dataset.evaluate(task, progress=False))
    result = report.cases[0]

    assert result.assertions["closed_loop_pass"].value is True
    assert result.scores["geometry_score"].value == 1.0
    assert result.scores["topology_score"].value == 1.0
    assert result.scores["poisson_score"].value == 1.0
    assert result.scores["measured_node_count"].value == 189.0
    assert result.scores["measured_x_span"].value == 100.0
    assert result.scores["measured_x_layers"].value == 21.0
    assert result.scores["measured_y_contraction"].value == -0.003


def test_bar_physics_score_includes_both_poisson_contractions() -> None:
    async def task(_inputs: BarTension3DEvalInput) -> BarTension3DObservation:
        return _observation(passed=True, y_contraction=0.0)

    dataset = Dataset(
        name="synthetic-bar-poisson",
        cases=[
            Case(
                name="bar",
                inputs=BarTension3DEvalInput(),
                metadata=BarTension3DEvalMetadata(),
            )
        ],
        evaluators=[BarTension3DClosedLoopEvaluator()],
    )
    report = asyncio.run(dataset.evaluate(task, progress=False))
    result = report.cases[0]

    assert result.assertions["closed_loop_pass"].value is False
    assert result.scores["physics_score"].value == pytest.approx(0.8)
    assert result.scores["poisson_score"].value == pytest.approx(0.5)


def test_bar_gate_requires_two_of_three_and_persists_metadata(
    tmp_path: Path,
) -> None:
    outputs = iter(
        [
            _observation(passed=True),
            _observation(passed=False),
            _observation(passed=True),
        ]
    )

    async def task(_inputs: BarTension3DEvalInput) -> BarTension3DObservation:
        return next(outputs)

    dataset = Dataset(
        name="synthetic-bar-gate",
        cases=[
            Case(
                name="bar",
                inputs=BarTension3DEvalInput(),
                metadata=BarTension3DEvalMetadata(),
            )
        ],
        evaluators=[BarTension3DClosedLoopEvaluator()],
    )
    report = asyncio.run(
        dataset.evaluate(
            task,
            repeat=3,
            max_concurrency=1,
            progress=False,
            metadata={"api_catalog_cards_sha256": "a" * 64},
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
    assert summary["experiment_metadata"] == {"api_catalog_cards_sha256": "a" * 64}


def test_bar_gate_counts_task_failure_as_failed_attempt() -> None:
    outputs: Iterator[BarTension3DObservation | RuntimeError] = iter(
        [
            _observation(passed=True),
            RuntimeError("licensed runtime unavailable"),
            _observation(passed=False),
        ]
    )

    async def task(_inputs: BarTension3DEvalInput) -> BarTension3DObservation:
        output = next(outputs)
        if isinstance(output, RuntimeError):
            raise output
        return output

    dataset = Dataset(
        name="synthetic-bar-task-failure",
        cases=[
            Case(
                name="bar",
                inputs=BarTension3DEvalInput(),
                metadata=BarTension3DEvalMetadata(),
            )
        ],
        evaluators=[BarTension3DClosedLoopEvaluator()],
    )
    report = asyncio.run(
        dataset.evaluate(task, repeat=3, max_concurrency=1, progress=False)
    )
    gate = gate_report(report)

    assert len(report.failures) == 1
    assert gate.attempts == 3
    assert gate.passes == 1
    assert not gate.passed


def test_checked_in_bar_concept_evidence_preserves_adoption_and_task_boundary() -> None:
    evidence_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "bar_tension_3d_concept_evaluation_20260824.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    control = evidence["arms"]["automatic_verified_only_control"]
    available = evidence["arms"]["concept_tool_available_without_preflight_policy"]
    preflight = evidence["arms"]["concept_tool_with_from_scratch_preflight_v1"]

    assert control["passes"] == 2
    assert control["attempts"] == 3
    assert control["attempt_profiles"][2]["failure_stage"] == "infrastructure"
    assert available["passes"] == 3
    assert available["concept_lookup_calls"] == 0
    assert preflight["passes"] == 2
    assert preflight["concept_lookup_calls"] == 3
    assert preflight["preflight_adoption"] == "3/3"
    assert preflight["attempt_profiles"][1]["failure_stage"] == "model_budget"
    assert evidence["interpretation"]["product_decision"].startswith(
        "Keep Concept retrieval off by default"
    )


def test_checked_in_final8_baseline_separates_transport_and_task_results() -> None:
    evidence_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "bar_tension_3d_final8_baseline_20260824.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    final_run = evidence["final_run_with_shell_patch"]
    current_run = evidence["current_r3_45m_product_run"]

    assert final_run["attempts"] == 3
    assert final_run["passes"] == 1
    assert not final_run["gate_passed"]
    assert final_run["task_passes_given_transport"] == "1/1"
    assert final_run["killpg_failures"] == 0
    assert final_run["generic_shell_calls"] == 0
    assert final_run["stale_hash_conflicts"] == 0
    assert [failure["stage"] for failure in final_run["failures"]] == [
        "infrastructure",
        "infrastructure",
    ]
    assert final_run["successful_attempt"]["model_requests"] == 9
    assert final_run["successful_attempt"]["abaqus_calls"] == 3
    assert evidence["interpretation"]["retry_policy"].startswith(
        "No eval-level task retry"
    )
    assert current_run["configuration"]["http_retries"] == 3
    assert current_run["configuration"]["wall_time_seconds"] == 2_700.0
    assert current_run["passes"] == 2
    assert current_run["gate_passed"]
    assert current_run["task_passes_given_transport"] == "2/2"
    assert current_run["failures"][0]["unfinished_request_wait_seconds"] == 484.258
    assert (
        current_run["successful_attempts"][0]["legitimate_concurrent_edit_conflicts"]
        == 1
    )
