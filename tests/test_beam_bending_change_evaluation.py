from __future__ import annotations

import asyncio
import json
from pathlib import Path

from abaqus_sim_agent.coding.contracts import (
    CodingRunOutcome,
    CodingRunStatus,
)
from abaqus_sim_agent.evaluation.beam_bending_change import (
    ABAQUS_CALL_BUDGET,
    CHANGE_REQUEST,
    DECLARED_CONTRACT_CHANGE_REQUEST,
    DECLARED_CONTRACT_PROMPT_VERSION,
    MODEL_REQUEST_BUDGET,
    PRODUCTION_FIRST_ODB_INSTRUCTIONS,
    PROMPT_VERSION,
    SOLVER_CALL_BUDGET,
    TASK_INSTRUCTIONS,
    VERIFIED_B31_ODB_CONTRACT_INSTRUCTIONS,
    BeamBendingChangeEvalInput,
    BeamBendingChangeObservation,
    BeamBendingConvergenceMode,
    build_dataset,
    change_request,
    gate_report,
    prompt_version,
    task_instructions,
)
from abaqus_sim_agent.verification.beam_bending_change import (
    BeamBendingInputContract,
    ConcentratedLoadRecord,
    verify_beam_bending_change,
)
from abaqus_sim_agent.verification.beam_bending_change_run import (
    BeamBendingChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.beam_tension import BeamTensionMeasurements
from abaqus_sim_agent.verification.model_change_evidence import WorkspaceChangeReport


def test_checked_in_beam_bending_request_matches_evaluator() -> None:
    path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "beam_bending_change_request.json"
    )
    checked_in = json.loads(path.read_text(encoding="utf-8"))

    assert "".join(checked_in["goal"].split()) == "".join(CHANGE_REQUEST.split())
    assert checked_in["prompt_version"] == PROMPT_VERSION
    assert checked_in["agent_visible_seed_files"] == [
        "build_model.py",
        "extract_results.py",
    ]
    assert "both build_model.py and\nextract_results.py" in TASK_INSTRUCTIONS
    assert "1.919" not in CHANGE_REQUEST
    assert "100000" not in CHANGE_REQUEST
    assert "97500" not in CHANGE_REQUEST
    assert MODEL_REQUEST_BUDGET == 36
    assert ABAQUS_CALL_BUDGET == 12
    assert SOLVER_CALL_BUDGET == 3


def test_beam_bending_convergence_treatment_is_explicit_and_answer_free() -> None:
    baseline = task_instructions(BeamBendingConvergenceMode.OFF)
    treatment = task_instructions(BeamBendingConvergenceMode.PRODUCTION_FIRST_ODB_V1)

    assert baseline == TASK_INSTRUCTIONS
    assert treatment.startswith(TASK_INSTRUCTIONS)
    assert PRODUCTION_FIRST_ODB_INSTRUCTIONS in treatment
    assert "first ODB-reading script" in treatment
    assert "componentLabels" in treatment
    assert "do not\n  slice" in treatment
    assert "built-in float" in treatment
    assert "Do not run a duplicate ODB cross-check" in treatment
    for hidden_result in ("1.919", "97500", "100000"):
        assert hidden_result not in treatment


def test_checked_in_declared_bending_contract_matches_v2_prompt() -> None:
    path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "beam_bending_change_request_v2.json"
    )
    checked_in = json.loads(path.read_text(encoding="utf-8"))
    mode = BeamBendingConvergenceMode.DECLARED_ODB_CONTRACT_V2
    declared = change_request(mode)

    assert "".join(checked_in["goal"].split()) == "".join(declared.split())
    assert checked_in["prompt_version"] == DECLARED_CONTRACT_PROMPT_VERSION
    assert prompt_version(mode) == DECLARED_CONTRACT_PROMPT_VERSION
    assert declared == DECLARED_CONTRACT_CHANGE_REQUEST
    assert "精确保持 U、RF、SF" in declared
    assert "不得把 RM 或 SM" in declared
    assert "sqrt(SM1^2 + SM2^2 + SM3^2)" in declared
    assert "componentLabels" in declared
    assert build_dataset("source", goal=declared).cases[0].inputs.goal == declared
    for hidden_result in ("1.919", "97500", "100000"):
        assert hidden_result not in declared


def test_verified_b31_odb_contract_is_bounded_and_answer_free() -> None:
    treatment = task_instructions(
        BeamBendingConvergenceMode.VERIFIED_B31_ODB_CONTRACT_V1
    )

    assert treatment.startswith(TASK_INSTRUCTIONS)
    assert PRODUCTION_FIRST_ODB_INSTRUCTIONS in treatment
    assert VERIFIED_B31_ODB_CONTRACT_INSTRUCTIONS in treatment
    assert "RF contains RF1/RF2/RF3" in treatment
    assert "RM contains RM1/RM2/RM3" in treatment
    assert "FieldValue.componentLabels" in treatment
    assert "without an ODB schema probe" in treatment
    for hidden_result in ("1.919", "97500", "100000"):
        assert hidden_result not in treatment


def test_beam_bending_change_gate_requires_two_of_three() -> None:
    outputs = iter(
        [
            _observation(passed=True),
            _observation(passed=False),
            _observation(passed=True),
        ]
    )

    async def task(
        _inputs: BeamBendingChangeEvalInput,
    ) -> BeamBendingChangeObservation:
        return next(outputs)

    report = asyncio.run(
        build_dataset("verified-beam-source").evaluate(
            task,
            repeat=3,
            max_concurrency=1,
            progress=False,
        )
    )
    gate = gate_report(report, repeat=3, required_passes=2)

    assert gate.attempts == 3
    assert gate.passes == 2
    assert gate.passed


def _measurements(**overrides: object) -> BeamTensionMeasurements:
    values: dict[str, object] = {
        "odb_path": "beam_tension.odb",
        "step_name": "Tension",
        "frame_index": 1,
        "analysis_step_count": 1,
        "step_procedure": "*STATIC",
        "step_nlgeom": False,
        "instance_name": "BEAM-1",
        "element_type_counts": {"B31": 20},
        "node_count": 21,
        "connectivity_is_x_chain": True,
        "coordinate_layer_counts": (21, 1, 1),
        "x_coordinates": tuple(float(index * 5) for index in range(21)),
        "x_span": 100.0,
        "max_abs_transverse_coordinate": 0.0,
        "right_mean_u1": 0.0,
        "max_abs_transverse_u": 1.9190475940704346,
        "left_sum_rf1": 0.0,
        "right_sum_rf1": 0.0,
        "mean_sf1": 0.0,
        "sf1_min": 0.0,
        "sf1_max": 0.0,
        "sf1_value_count": 20,
        "sf1_element_coverage": True,
        "sf1_has_mixed_signs": False,
        "max_abs_transverse_sf": 1000.0,
        "material_name": "STEEL",
        "elastic_e": 210_000.0,
        "elastic_nu": 0.3,
        "profile_name": "PROFILE",
        "profile_a": 10.0,
        "profile_b": 10.0,
        "section_name": "SECTION",
        "section_material_name": "STEEL",
        "section_profile_name": "PROFILE",
        "section_integration": "DURING_ANALYSIS",
        "section_assignment_name": "SECTION",
        "section_assignment_covers_all_elements": True,
        "orientation_method": "N1_COSINES",
        "orientation_vector": (0.0, 0.0, -1.0),
        "orientation_covers_all_elements": True,
        "right_mean_u2": 1.9190475940704346,
        "left_sum_rf2": -1000.0,
        "right_sum_rf2": 0.0,
        "left_sum_rm3": -100_000.0,
        "right_sum_rm3": 0.0,
        "max_section_shear": 1000.0,
        "max_section_moment": 97_500.0,
        "sm_value_count": 20,
        "sm_element_coverage": True,
    }
    values.update(overrides)
    return BeamTensionMeasurements.model_validate(values)


def _observation(*, passed: bool) -> BeamBendingChangeObservation:
    measurements = _measurements(
        right_mean_u2=1.9190475940704346 if passed else 1.0,
    )
    contract = BeamBendingInputContract(
        step_count=1,
        static_step_count=1,
        nlgeom_off=True,
        boundary_records=(("LEFT", "ENCASTRE"),),
        concentrated_loads=(
            ConcentratedLoadRecord(region="RIGHT", dof=2, magnitude=1000.0),
        ),
        field_output_variables=("RF", "SF", "U"),
        forbidden_keywords=(),
    )
    verification = verify_beam_bending_change(
        measurements,
        contract,
        job_completed=True,
        artifact_freshness={
            "build_model.py": True,
            "extract_results.py": True,
            "beam_tension.cae": True,
            "beam_tension.inp": True,
            "beam_tension.odb": True,
            "results.json": True,
        },
        source_run_verified=True,
        build_script_changed=True,
        extractor_changed=True,
        execution_provenance_passed=True,
        agent_results={
            "right_mean_u2": measurements.right_mean_u2,
            "left_sum_rf2": measurements.left_sum_rf2,
            "left_sum_rm3": measurements.left_sum_rm3,
            "max_section_moment": measurements.max_section_moment,
        },
    )
    attempt = BeamBendingChangeVerificationAttempt(
        passed=verification.passed,
        feedback="synthetic",
        measurements=measurements,
        input_contract=contract,
        verification=verification,
        change_report=WorkspaceChangeReport(
            source_run_id="verified-beam-source",
            changes=(),
            build_model_patch="diff build",
            extract_results_patch="diff extract",
        ),
        failure_stage=None if verification.passed else "verification",
    )
    return BeamBendingChangeObservation(
        run_id="beam-bending-test",
        source_run_id="verified-beam-source",
        outcome=CodingRunOutcome(
            run_id="beam-bending-test",
            status=(
                CodingRunStatus.COMPLETED
                if verification.passed
                else CodingRunStatus.FAILED
            ),
            summary="synthetic",
            failure_stage=None if verification.passed else "verification",
        ),
        verification=attempt,
        abaqus_runtime_seconds=1.0,
    )
