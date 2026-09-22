from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from pydantic_evals import Dataset

from abaqus_sim_agent.coding.contracts import (
    ArtifactKind,
    ArtifactRef,
    ClarificationEvent,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    RuntimeProfile,
)
from abaqus_sim_agent.evaluation.plate_change import (
    CHANGE_REQUEST,
    PROMPT_VERSION,
    PlateChangeEvalInput,
    PlateChangeEvalMetadata,
    PlateChangeObservation,
    _task_instructions,
    build_dataset,
    gate_report,
)
from abaqus_sim_agent.evaluation.plate_tension import GOLD_REQUEST
from abaqus_sim_agent.evaluation.verified_plate_seed import (
    AGENT_VISIBLE_SEED_FILES,
    create_seeded_workspace,
    load_verified_plate_seed,
)
from abaqus_sim_agent.verification.model_change_evidence import WorkspaceChangeReport
from abaqus_sim_agent.verification.plate_change import (
    PlateHoleChangeMeasurements,
    PlateHoleInputContract,
    verify_plate_hole_change,
)
from abaqus_sim_agent.verification.plate_change_run import (
    PlateHoleChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.plate_tension import (
    PlateTensionMeasurements,
    verify_plate_tension,
)


def test_checked_in_plate_change_request_matches_evaluator() -> None:
    request_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "plate_hole_change_request_v2.json"
    )
    checked_in = json.loads(request_path.read_text(encoding="utf-8"))

    assert "".join(checked_in["goal"].split()) == "".join(CHANGE_REQUEST.split())
    assert checked_in["prompt_version"] == PROMPT_VERSION
    assert tuple(checked_in["agent_visible_seed_files"]) == AGENT_VISIBLE_SEED_FILES


def test_checked_in_plate_change_evidence_keeps_success_and_limits() -> None:
    project_root = Path(__file__).parents[1]
    evidence = json.loads(
        (
            project_root
            / "benchmarks"
            / "coding"
            / "plate_hole_change_evaluation_20260825.json"
        ).read_text(encoding="utf-8")
    )
    request_path = (
        project_root / "benchmarks" / "coding" / "plate_hole_change_request.json"
    )

    assert evidence["deterministic_reference"]["result"] == "1/1"
    assert evidence["agent_smokes"]["v2_complete"]["result"] == "1/1"
    assert evidence["formal_batches"]["v3_initial_budget"]["result"] == "0/3"
    assert evidence["formal_batches"]["v4_bounded_treatment"]["result"] == "0/3"
    assert not evidence["formal_batches"]["v4_bounded_treatment"]["gate_passed"]
    assert evidence["configuration"]["api_catalog_mode"] == "off"
    assert evidence["configuration"]["concept_retrieval_mode"] == "off"
    assert "known limitation" in evidence["interpretation"]["semantic_stop"]
    assert (
        evidence["frozen_request"]["sha256"]
        == hashlib.sha256(request_path.read_bytes()).hexdigest()
    )


def test_verified_plate_seed_is_validated_and_only_scripts_are_copied(
    tmp_path: Path,
) -> None:
    source_run = _make_source_run(tmp_path / "source")
    seed = load_verified_plate_seed(source_run)
    expected_build = seed.seed_files["build_model.py"]
    (source_run / "workspace" / "build_model.py").write_text(
        "mutated after validation\n", encoding="utf-8"
    )

    resolved = create_seeded_workspace(
        tmp_path / "new-runs",
        "plate-hole-test",
        seed,
    )

    assert {path.name for path in resolved.workspace.iterdir()} == {
        "build_model.py",
        "extract_results.py",
    }
    assert {item.relative_path for item in resolved.initial_snapshot.files} == {
        "build_model.py",
        "extract_results.py",
    }
    assert (resolved.workspace / "build_model.py").read_bytes() == expected_build
    provenance = json.loads(
        (resolved.state_dir / "seed_provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["source_run_id"] == "verified-source"


def test_verified_plate_seed_rejects_tampered_artifact(tmp_path: Path) -> None:
    source_run = _make_source_run(tmp_path / "source")
    (source_run / "workspace" / "plate_tension.odb").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="evidence mismatch"):
        load_verified_plate_seed(source_run)


def test_verified_plate_seed_rejects_revised_outcome_with_old_verification(
    tmp_path: Path,
) -> None:
    source_run = _make_source_run(tmp_path / "source")
    outcome_path = source_run / "state" / "outcome.json"
    outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
    outcome["task_revision"] = 1
    outcome["amendment_sequence"] = 1
    outcome_path.write_text(json.dumps(outcome), encoding="utf-8")

    with pytest.raises(ValueError, match="original completed revision"):
        load_verified_plate_seed(source_run)


def test_verified_plate_seed_rejects_pre_policy_amendment_schema(
    tmp_path: Path,
) -> None:
    source_run = _make_source_run(tmp_path / "source")
    ledger = {
        "schema_version": "coding-run-amendments-v1",
        "run_id": "verified-source",
        "amendments": [
            {
                "sequence": 1,
                "kind": "operational",
                "reason": "Post-Gold model change",
                "config": {"budget": {"wall_time_seconds": 5400.0}},
                "task_revision": 0,
                "created_at": "2026-09-03T00:00:00Z",
            }
        ],
    }
    (source_run / "state" / "amendments.json").write_text(
        json.dumps(ledger),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"config\.budget"):
        load_verified_plate_seed(source_run)


def test_change_task_instructions_replace_from_scratch_clarification() -> None:
    instructions = _task_instructions()

    assert "plate_thickness" not in instructions
    assert "do not return needs_input" in instructions
    assert "three typed Abaqus tools" in instructions
    assert "CircleByCenterPerimeter" not in instructions
    assert "modify only build_model.py" in instructions
    assert "byte-for-byte unchanged" in instructions


def test_plate_change_gate_requires_two_of_three() -> None:
    outputs = iter(
        [
            _change_observation(passed=True),
            _change_observation(passed=False),
            _change_observation(passed=True),
        ]
    )

    async def task(_inputs: PlateChangeEvalInput) -> PlateChangeObservation:
        return next(outputs)

    dataset: Dataset[
        PlateChangeEvalInput,
        PlateChangeObservation,
        PlateChangeEvalMetadata,
    ] = build_dataset("verified-source")
    report = asyncio.run(
        dataset.evaluate(task, repeat=3, max_concurrency=1, progress=False)
    )

    gate = gate_report(report, repeat=3, required_passes=2)

    assert gate.attempts == 3
    assert gate.passes == 2
    assert gate.passed


def _make_source_run(run_dir: Path) -> Path:
    workspace = run_dir / "workspace"
    state_dir = run_dir / "state"
    workspace.mkdir(parents=True)
    state_dir.mkdir()
    content = {
        "build_model.py": b"print('build')\n",
        "extract_results.py": b"print('extract')\n",
        "plate_tension.cae": b"cae",
        "plate_tension.inp": b"inp",
        "plate_tension.odb": b"odb",
        "results.json": b"{}",
    }
    artifacts: list[ArtifactRef] = []
    for name, raw in content.items():
        (workspace / name).write_bytes(raw)
        artifacts.append(
            ArtifactRef(
                relative_path=name,
                size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                kind=ArtifactKind.OTHER,
                fresh=True,
            )
        )
    request = CodingRunRequest(
        run_id="verified-source",
        goal=GOLD_REQUEST,
        workspace=workspace.resolve(),
        state_dir=state_dir.resolve(),
        runtime=RuntimeProfile(launcher_path=(run_dir / "abq2024.bat").resolve()),
    )
    outcome = CodingRunOutcome(
        run_id="verified-source",
        status=CodingRunStatus.COMPLETED,
        summary="verified source",
        artifacts=tuple(artifacts),
        clarifications=(
            ClarificationEvent(
                field_id="plate_thickness",
                question="Thickness?",
                kind="missing_physical_input",
                impact="Thickness changes the plane-stress section response.",
                answer="1 mm",
                source="scripted",
                abaqus_calls_before_answer=0,
            ),
        ),
    )
    measurements = PlateTensionMeasurements(
        odb_path="plate_tension.odb",
        step_name="Tension",
        frame_index=1,
        instance_name="PLATE-1",
        element_type_counts={"CPS4R": 80},
        right_mean_u1=0.1,
        mean_s11=210.0,
        left_sum_rf1=-4200.0,
    )
    verification = verify_plate_tension(
        measurements,
        job_completed=True,
        artifact_freshness=dict.fromkeys(content, True),
        agent_results={
            "right_mean_u1": 0.1,
            "mean_s11": 210.0,
            "left_sum_rf1": -4200.0,
        },
    )
    (state_dir / "request.json").write_text(
        request.model_dump_json(indent=2), encoding="utf-8"
    )
    (state_dir / "outcome.json").write_text(
        outcome.model_dump_json(indent=2), encoding="utf-8"
    )
    (state_dir / "verification.json").write_text(
        verification.model_dump_json(indent=2), encoding="utf-8"
    )
    return run_dir


def _change_observation(*, passed: bool) -> PlateChangeObservation:
    measurements = PlateHoleChangeMeasurements(
        odb_path="plate_tension.odb",
        step_name="Tension",
        frame_index=1,
        instance_name="PLATE-1",
        node_count=292,
        element_type_counts={"CPS4R": 250},
        x_min=0.0,
        x_max=100.0,
        y_min=0.0,
        y_max=20.0,
        mesh_area=1972.05,
        hole_min_node_radius=3.0,
        boundary_loop_count=2,
        hole_boundary_edge_count=24,
        hole_center_x=50.0,
        hole_center_y=10.0,
        hole_max_radius_error=0.0,
        field_outputs=("RF", "S", "U"),
        right_mean_u1=0.1 if passed else 0.08,
        left_mean_u1=0.0,
        pinned_u2=0.0,
        left_sum_rf1=-4014.5,
        right_sum_rf1=4014.5,
        mean_s11=210.4,
        far_field_mean_s11=200.7,
        hole_boundary_max_s11=517.0,
    )
    contract = PlateHoleInputContract(
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
        boundary_records=((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.1)),
        mechanical_load_keywords=(),
    )
    freshness = {
        "build_model.py": True,
        "extract_results.py": False,
        "plate_tension.cae": True,
        "plate_tension.inp": True,
        "plate_tension.odb": True,
        "results.json": True,
    }
    verification = verify_plate_hole_change(
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
    attempt = PlateHoleChangeVerificationAttempt(
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
    return PlateChangeObservation(
        run_id="plate-hole-test",
        source_run_id="verified-source",
        outcome=CodingRunOutcome(
            run_id="plate-hole-test",
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
