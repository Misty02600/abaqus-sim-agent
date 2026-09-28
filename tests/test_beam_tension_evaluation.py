from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from inspect import signature
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
from abaqus_sim_agent.coding.model import DEFAULT_MODEL_HTTP_RETRIES
from abaqus_sim_agent.evaluation.beam_tension import (
    B31_RUNTIME_SCRIPT_POLICY,
    EVAL_BUDGET,
    GOLD_REQUEST,
    PROMPT_VERSION,
    TASK_INSTRUCTIONS,
    BeamTensionClosedLoopEvaluator,
    BeamTensionEvalInput,
    BeamTensionEvalMetadata,
    BeamTensionObservation,
    _resolve_eval_api_catalog_mode,
    _resolve_eval_concept_retrieval_mode,
    _write_evaluation_artifacts,
    gate_report,
    run_evaluation,
)
from abaqus_sim_agent.verification.beam_run import BeamTensionVerificationAttempt
from abaqus_sim_agent.verification.beam_tension import (
    BeamTensionMeasurements,
    verify_beam_tension,
)


def test_checked_in_beam_request_matches_evaluation_prompt_and_product_budget() -> None:
    request_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "beam_tension_request.json"
    )
    checked_in = json.loads(request_path.read_text(encoding="utf-8"))["goal"]

    assert "".join(checked_in.split()) == "".join(GOLD_REQUEST.split())
    assert PROMPT_VERSION == "beam-tension-b31-v1"
    assert "B31" in GOLD_REQUEST
    assert "20 个单元，共 21 个节点" in GOLD_REQUEST
    assert "10×10 mm" in GOLD_REQUEST
    assert "Encastre" in GOLD_REQUEST
    assert "right_mean_u1、left_sum_rf1 和 mean_sf1" in GOLD_REQUEST
    assert "no physical input is missing" in TASK_INSTRUCTIONS
    assert "do not return\nneeds_input" in TASK_INSTRUCTIONS
    assert EVAL_BUDGET.max_clarifications == 0
    assert EVAL_BUDGET.max_model_requests == 40
    assert EVAL_BUDGET.max_api_lookup_calls == 4
    assert EVAL_BUDGET.max_concept_lookup_calls == 2
    assert EVAL_BUDGET.max_static_preflight_calls == 12
    assert EVAL_BUDGET.max_abaqus_calls == 12
    assert EVAL_BUDGET.max_solver_calls == 3
    assert EVAL_BUDGET.wall_time_seconds == 2_700.0
    parameters = signature(run_evaluation).parameters
    assert parameters["http_retries"].default == DEFAULT_MODEL_HTTP_RETRIES
    assert parameters["api_catalog_mode"].default == "automatic_verified_only"
    assert parameters["concept_retrieval_mode"].default == "off"
    assert parameters["runtime_script_policy_mode"].default == "off"
    assert parameters["static_preflight_mode"].default == "off"
    assert B31_RUNTIME_SCRIPT_POLICY.allowed_cae_scripts == ("build_model.py",)
    assert B31_RUNTIME_SCRIPT_POLICY.allowed_abaqus_python_scripts == (
        "extract_results.py",
    )
    assert B31_RUNTIME_SCRIPT_POLICY.allowed_solver_inputs == ("beam_tension.inp",)


def test_beam_catalog_and_concept_modes_require_explicit_artifacts() -> None:
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
    mean_sf1: float = 21_000.0,
) -> BeamTensionObservation:
    freshness = {
        "build_model.py": True,
        "extract_results.py": True,
        "beam_tension.cae": True,
        "beam_tension.inp": True,
        "beam_tension.odb": True,
        "results.json": True,
    }
    measurements = BeamTensionMeasurements(
        odb_path="beam_tension.odb",
        step_name="Tension",
        frame_index=1,
        analysis_step_count=1,
        step_procedure="*STATIC",
        step_nlgeom=False,
        instance_name="BEAM-1",
        element_type_counts={"B31": 20},
        node_count=21,
        connectivity_is_x_chain=True,
        coordinate_layer_counts=(21, 1, 1),
        x_coordinates=tuple(float(index * 5) for index in range(21)),
        x_span=100.0,
        max_abs_transverse_coordinate=0.0,
        right_mean_u1=0.1 if passed else 0.08,
        max_abs_transverse_u=0.0,
        left_sum_rf1=-21_000.0,
        right_sum_rf1=21_000.0,
        mean_sf1=mean_sf1,
        sf1_min=mean_sf1,
        sf1_max=mean_sf1,
        sf1_value_count=20,
        sf1_element_coverage=True,
        sf1_has_mixed_signs=False,
        max_abs_transverse_sf=0.0,
        material_name="Steel",
        elastic_e=210_000.0,
        elastic_nu=0.3,
        profile_name="Rect10x10",
        profile_a=10.0,
        profile_b=10.0,
        section_name="BeamSection",
        section_material_name="Steel",
        section_profile_name="Rect10x10",
        section_integration="DURING_ANALYSIS",
        section_assignment_name="BeamSection",
        section_assignment_covers_all_elements=True,
        orientation_method="N1_COSINES",
        orientation_vector=(0.0, 0.0, 1.0),
        orientation_covers_all_elements=True,
    )
    verification = verify_beam_tension(
        measurements,
        job_completed=True,
        artifact_freshness=freshness,
        agent_results={
            "right_mean_u1": measurements.right_mean_u1,
            "left_sum_rf1": measurements.left_sum_rf1,
            "mean_sf1": measurements.mean_sf1,
        },
        input_contract_passed=True,
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
    return BeamTensionObservation(
        run_id="beam-1",
        outcome=CodingRunOutcome(
            run_id="beam-1",
            status=CodingRunStatus.COMPLETED,
            summary="synthetic",
            artifacts=artifacts,
        ),
        verification=BeamTensionVerificationAttempt(
            passed=verification.passed,
            feedback="synthetic",
            artifacts=artifacts,
            measurements=measurements,
            verification=verification,
            failure_stage=None if verification.passed else "verification",
        ),
        abaqus_runtime_seconds=1.0,
    )


def test_beam_evaluator_exposes_geometry_topology_and_force_measurements() -> None:
    async def task(_inputs: BeamTensionEvalInput) -> BeamTensionObservation:
        return _observation(passed=True)

    dataset = Dataset(
        name="synthetic-beam-metrics",
        cases=[
            Case(
                name="beam",
                inputs=BeamTensionEvalInput(),
                metadata=BeamTensionEvalMetadata(),
            )
        ],
        evaluators=[BeamTensionClosedLoopEvaluator()],
    )
    report = asyncio.run(dataset.evaluate(task, progress=False))
    result = report.cases[0]

    assert result.assertions["closed_loop_pass"].value is True
    assert result.scores["input_contract_score"].value == 1.0
    assert result.scores["physics_score"].value == 1.0
    assert result.scores["geometry_score"].value == 1.0
    assert result.scores["topology_score"].value == 1.0
    assert result.scores["model_contract_score"].value == 1.0
    assert result.scores["measured_node_count"].value == 21.0
    assert result.scores["measured_element_count"].value == 20.0
    assert result.scores["measured_step_count"].value == 1.0
    assert result.scores["measured_connectivity_score"].value == 1.0
    assert result.scores["measured_x_span"].value == 100.0
    assert result.scores["measured_right_mean_u1"].value == 0.1
    assert result.scores["measured_left_sum_rf1"].value == -21_000.0
    assert result.scores["measured_right_sum_rf1"].value == 21_000.0
    assert result.scores["measured_mean_sf1"].value == 21_000.0
    assert result.scores["measured_sf1_value_count"].value == 20.0
    assert result.scores["measured_elastic_e"].value == 210_000.0
    assert result.scores["measured_profile_a"].value == 10.0


def test_beam_physics_score_includes_section_force() -> None:
    async def task(_inputs: BeamTensionEvalInput) -> BeamTensionObservation:
        return _observation(passed=True, mean_sf1=10_000.0)

    dataset = Dataset(
        name="synthetic-beam-sf1",
        cases=[
            Case(
                name="beam",
                inputs=BeamTensionEvalInput(),
                metadata=BeamTensionEvalMetadata(),
            )
        ],
        evaluators=[BeamTensionClosedLoopEvaluator()],
    )
    report = asyncio.run(dataset.evaluate(task, progress=False))
    result = report.cases[0]

    assert result.assertions["closed_loop_pass"].value is False
    assert result.scores["physics_score"].value == pytest.approx(7.0 / 8.0)


def test_beam_gate_requires_two_of_three_and_persists_metadata(
    tmp_path: Path,
) -> None:
    outputs = iter(
        [
            _observation(passed=True),
            _observation(passed=False),
            _observation(passed=True),
        ]
    )

    async def task(_inputs: BeamTensionEvalInput) -> BeamTensionObservation:
        return next(outputs)

    dataset = Dataset(
        name="synthetic-beam-gate",
        cases=[
            Case(
                name="beam",
                inputs=BeamTensionEvalInput(),
                metadata=BeamTensionEvalMetadata(),
            )
        ],
        evaluators=[BeamTensionClosedLoopEvaluator()],
    )
    report = asyncio.run(
        dataset.evaluate(
            task,
            repeat=3,
            max_concurrency=1,
            progress=False,
            metadata={
                "api_catalog_mode": "automatic_verified_only",
                "concept_retrieval_mode": "off",
                "planning_enabled": False,
                "harness_windows_newline_hash_patch": "hash-v1",
                "harness_windows_shell_tree_patch": "shell-v1",
            },
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
    assert summary["experiment_metadata"] == {
        "api_catalog_mode": "automatic_verified_only",
        "concept_retrieval_mode": "off",
        "planning_enabled": False,
        "harness_windows_newline_hash_patch": "hash-v1",
        "harness_windows_shell_tree_patch": "shell-v1",
    }


def test_beam_gate_counts_task_failure_as_failed_attempt() -> None:
    outputs: Iterator[BeamTensionObservation | RuntimeError] = iter(
        [
            _observation(passed=True),
            RuntimeError("licensed runtime unavailable"),
            _observation(passed=False),
        ]
    )

    async def task(_inputs: BeamTensionEvalInput) -> BeamTensionObservation:
        output = next(outputs)
        if isinstance(output, RuntimeError):
            raise output
        return output

    dataset = Dataset(
        name="synthetic-beam-task-failure",
        cases=[
            Case(
                name="beam",
                inputs=BeamTensionEvalInput(),
                metadata=BeamTensionEvalMetadata(),
            )
        ],
        evaluators=[BeamTensionClosedLoopEvaluator()],
    )
    report = asyncio.run(
        dataset.evaluate(task, repeat=3, max_concurrency=1, progress=False)
    )
    gate = gate_report(report)

    assert len(report.failures) == 1
    assert gate.attempts == 3
    assert gate.passes == 1
    assert not gate.passed
