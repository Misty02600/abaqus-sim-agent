from __future__ import annotations

import asyncio
import json
from pathlib import Path

from abaqus_sim_agent.coding.contracts import CodingRunOutcome, CodingRunStatus
from abaqus_sim_agent.evaluation.load_change import (
    CHANGE_REQUEST,
    PROMPT_VERSION,
    LoadChangeEvalInput,
    LoadChangeObservation,
    build_dataset,
    gate_report,
)
from abaqus_sim_agent.verification.load_change import (
    BoundaryRecord,
    LoadModeInputContract,
    LoadModeMeasurements,
    TractionRecord,
    verify_load_mode_change,
)
from abaqus_sim_agent.verification.load_change_run import (
    LoadModeChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.model_change_evidence import (
    WorkspaceChangeReport,
)


def test_checked_in_load_mode_request_matches_evaluator() -> None:
    path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "plate_load_mode_change_request.json"
    )
    checked_in = json.loads(path.read_text(encoding="utf-8"))

    assert "".join(checked_in["goal"].split()) == "".join(CHANGE_REQUEST.split())
    assert checked_in["prompt_version"] == PROMPT_VERSION


def test_load_mode_gate_requires_two_of_three() -> None:
    outputs = iter(
        [
            _observation(passed=True),
            _observation(passed=False),
            _observation(passed=True),
        ]
    )

    async def task(_inputs: LoadChangeEvalInput) -> LoadChangeObservation:
        return next(outputs)

    report = asyncio.run(
        build_dataset("verified-source").evaluate(
            task, repeat=3, max_concurrency=1, progress=False
        )
    )
    gate = gate_report(report, repeat=3, required_passes=2)

    assert gate.attempts == 3
    assert gate.passes == 2
    assert gate.passed


def _observation(*, passed: bool) -> LoadChangeObservation:
    measurements = LoadModeMeasurements(
        odb_path="plate_tension.odb",
        step_name="Tension",
        frame_index=1,
        analysis_step_count=1,
        step_procedure="*STATIC",
        step_nlgeom=False,
        instance_name="PLATE-1",
        node_count=105,
        element_type_counts={"CPS4R": 80},
        x_min=0.0,
        x_max=100.0,
        y_min=0.0,
        y_max=20.0,
        surface_name="RIGHT_TRACTION",
        surface_is_full_right_edge=True,
        surface_unique_node_count=5,
        surface_element_count=4,
        elastic_modulus=210000.0,
        poisson_ratio=0.3,
        section_thickness=1.0,
        section_material_name="Steel",
        material_name="Steel",
        section_assignment_count=1,
        section_assignment_covers_all_elements=True,
        field_outputs=("RF", "S", "U"),
        right_mean_u1=0.1 if passed else 0.08,
        max_abs_left_u1=0.0,
        pinned_u2=0.0,
        left_sum_rf1=-4200.0,
        right_sum_rf1=0.0,
        mean_s11=210.0,
    )
    contract = LoadModeInputContract(
        elastic_modulus=210000.0,
        poisson_ratio=0.3,
        thickness=1.0,
        material_name="Steel",
        section_material_name="Steel",
        element_types=("CPS4R",),
        step_count=1,
        static_step_count=1,
        nlgeom_off=True,
        field_output_variables=("RF", "S", "U"),
        boundary_records=(
            BoundaryRecord(region="LEFT", first_dof=1, last_dof=1, magnitude=0.0),
            BoundaryRecord(region="PIN", first_dof=2, last_dof=2, magnitude=0.0),
        ),
        traction_records=(
            TractionRecord(
                surface_name="RIGHT_TRACTION",
                load_type="TRVEC",
                magnitude=210.0,
                direction=(1.0, 0.0, 0.0),
                follower=False,
            ),
        ),
        surface_names=("RIGHT_TRACTION",),
        forbidden_keywords=(),
    )
    freshness = {
        "build_model.py": True,
        "extract_results.py": False,
        "plate_tension.cae": True,
        "plate_tension.inp": True,
        "plate_tension.odb": True,
        "results.json": True,
    }
    verification = verify_load_mode_change(
        measurements,
        contract,
        job_completed=True,
        artifact_freshness=freshness,
        source_run_verified=True,
        agent_results={
            "right_mean_u1": measurements.right_mean_u1,
            "mean_s11": measurements.mean_s11,
            "left_sum_rf1": measurements.left_sum_rf1,
        },
    )
    attempt = LoadModeChangeVerificationAttempt(
        passed=verification.passed,
        feedback="synthetic",
        measurements=measurements,
        input_contract=contract,
        verification=verification,
        change_report=WorkspaceChangeReport(
            source_run_id="verified-source",
            changes=(),
            build_model_patch="diff build",
            extract_results_patch="",
        ),
        failure_stage=None if verification.passed else "verification",
    )
    return LoadChangeObservation(
        run_id="plate-load-test",
        source_run_id="verified-source",
        outcome=CodingRunOutcome(
            run_id="plate-load-test",
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
