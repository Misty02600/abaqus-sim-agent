from __future__ import annotations

import asyncio
import json
from pathlib import Path

from abaqus_sim_agent.coding.contracts import CodingRunOutcome, CodingRunStatus
from abaqus_sim_agent.evaluation.material_change import (
    CHANGE_REQUEST,
    PROMPT_VERSION,
    MaterialChangeEvalInput,
    MaterialChangeObservation,
    build_dataset,
    gate_report,
)
from abaqus_sim_agent.verification.material_change import (
    MaterialElasticContract,
    SolidSectionContract,
    TwoMaterialPlateInputContract,
    TwoMaterialPlateMeasurements,
    verify_two_material_plate_change,
)
from abaqus_sim_agent.verification.material_change_run import (
    TwoMaterialPlateChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.model_change_evidence import (
    WorkspaceChangeReport,
)


def test_checked_in_dual_material_request_matches_evaluator() -> None:
    path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "plate_dual_material_change_request.json"
    )
    checked_in = json.loads(path.read_text(encoding="utf-8"))

    assert "".join(checked_in["goal"].split()) == "".join(CHANGE_REQUEST.split())
    assert checked_in["prompt_version"] == PROMPT_VERSION


def test_dual_material_gate_requires_two_of_three() -> None:
    outputs = iter(
        [
            _observation(passed=True),
            _observation(passed=False),
            _observation(passed=True),
        ]
    )

    async def task(_inputs: MaterialChangeEvalInput) -> MaterialChangeObservation:
        return next(outputs)

    dataset = build_dataset("verified-source")
    report = asyncio.run(
        dataset.evaluate(task, repeat=3, max_concurrency=1, progress=False)
    )
    gate = gate_report(report, repeat=3, required_passes=2)

    assert gate.attempts == 3
    assert gate.passes == 2
    assert gate.passed


def _observation(*, passed: bool) -> MaterialChangeObservation:
    measurements = TwoMaterialPlateMeasurements(
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
        max_abs_z=0.0,
        x_layer_count=21,
        y_layer_count=5,
        interface_node_count=5,
        interface_is_shared=True,
        connectivity_is_single_component=True,
        left_element_count=40,
        right_element_count=40,
        assignments_cover_each_element_once=True,
        left_properties=((210000.0, 0.3, 1.0),),
        right_properties=((70000.0, 0.3, 1.0),),
        field_outputs=("RF", "S", "U"),
        u_node_coverage=True,
        s_element_coverage=True,
        right_mean_u1=0.1 if passed else 0.08,
        interface_mean_u1=0.02526366673409939,
        max_abs_left_u1=0.0,
        pinned_u2=0.0,
        left_sum_rf1=-2101.8351440429688,
        right_sum_rf1=2101.8351440429688,
        mean_s11=105.09175720214844,
        left_mean_s11=105.09175720214844,
        right_mean_s11=105.09175720214844,
        max_abs_s22=11.786,
        max_abs_s12=2.748,
    )
    contract = TwoMaterialPlateInputContract(
        materials=(
            MaterialElasticContract(
                name="Steel", elastic_modulus=210000.0, poisson_ratio=0.3
            ),
            MaterialElasticContract(
                name="Soft", elastic_modulus=70000.0, poisson_ratio=0.3
            ),
        ),
        solid_sections=(
            SolidSectionContract(material_name="Steel", thickness=1.0),
            SolidSectionContract(material_name="Soft", thickness=1.0),
        ),
        element_types=("CPS4R",),
        step_count=1,
        static_step_count=1,
        nlgeom_off=True,
        field_output_variables=("RF", "S", "U"),
        boundary_records=((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.1)),
        forbidden_keywords=(),
    )
    freshness: dict[str, bool] = dict.fromkeys(
        (
            "build_model.py",
            "extract_results.py",
            "plate_tension.cae",
            "plate_tension.inp",
            "plate_tension.odb",
            "results.json",
        ),
        True,
    )
    verification = verify_two_material_plate_change(
        measurements,
        contract,
        job_completed=True,
        artifact_freshness=freshness,
        source_run_verified=True,
        agent_results={
            "right_mean_u1": measurements.right_mean_u1,
            "interface_mean_u1": measurements.interface_mean_u1,
            "mean_s11": measurements.mean_s11,
            "left_mean_s11": measurements.left_mean_s11,
            "right_mean_s11": measurements.right_mean_s11,
            "left_sum_rf1": measurements.left_sum_rf1,
        },
    )
    attempt = TwoMaterialPlateChangeVerificationAttempt(
        passed=verification.passed,
        feedback="synthetic",
        measurements=measurements,
        input_contract=contract,
        verification=verification,
        change_report=WorkspaceChangeReport(
            source_run_id="verified-source",
            changes=(),
            build_model_patch="diff build",
            extract_results_patch="diff extract",
        ),
        failure_stage=None if verification.passed else "verification",
    )
    return MaterialChangeObservation(
        run_id="plate-material-test",
        source_run_id="verified-source",
        outcome=CodingRunOutcome(
            run_id="plate-material-test",
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
