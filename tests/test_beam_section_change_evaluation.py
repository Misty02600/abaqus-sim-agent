from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.contracts import (
    ArtifactKind,
    ArtifactRef,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    RuntimeProfile,
)
from abaqus_sim_agent.evaluation.beam_section_change import (
    CHANGE_REQUEST,
    PROMPT_VERSION,
    TASK_INSTRUCTIONS,
    BeamSectionChangeEvalInput,
    BeamSectionChangeObservation,
    build_dataset,
    gate_report,
)
from abaqus_sim_agent.evaluation.beam_tension import GOLD_REQUEST
from abaqus_sim_agent.evaluation.verified_beam_seed import (
    BEAM_AGENT_VISIBLE_SEED_FILES,
    load_verified_beam_seed,
)
from abaqus_sim_agent.evaluation.verified_plate_seed import (
    create_seeded_workspace,
)
from abaqus_sim_agent.verification.beam_section_change import (
    verify_beam_section_change,
)
from abaqus_sim_agent.verification.beam_section_change_run import (
    BeamSectionChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.beam_tension import (
    BeamTensionMeasurements,
    verify_beam_tension,
)
from abaqus_sim_agent.verification.model_change_evidence import (
    WorkspaceChangeReport,
)


def test_checked_in_beam_section_change_request_matches_evaluator() -> None:
    request_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "beam_section_change_request.json"
    )
    checked_in = json.loads(request_path.read_text(encoding="utf-8"))

    assert "".join(checked_in["goal"].split()) == "".join(CHANGE_REQUEST.split())
    assert checked_in["prompt_version"] == PROMPT_VERSION
    assert tuple(checked_in["agent_visible_seed_files"]) == (
        BEAM_AGENT_VISIBLE_SEED_FILES
    )
    assert "Modify\nonly build_model.py" in TASK_INSTRUCTIONS
    assert "byte-for-byte unchanged" in TASK_INSTRUCTIONS
    assert "42" not in CHANGE_REQUEST


def test_verified_beam_seed_is_validated_and_only_scripts_are_copied(
    tmp_path: Path,
) -> None:
    source_run = _make_source_run(tmp_path / "source")
    seed = load_verified_beam_seed(source_run)
    expected_build = seed.seed_files["build_model.py"]
    (source_run / "workspace" / "build_model.py").write_text(
        "mutated after validation\n",
        encoding="utf-8",
    )

    resolved = create_seeded_workspace(
        tmp_path / "new-runs",
        "beam-section-test",
        seed,
    )

    assert {path.name for path in resolved.workspace.iterdir()} == {
        "build_model.py",
        "extract_results.py",
    }
    assert (resolved.workspace / "build_model.py").read_bytes() == expected_build
    provenance = json.loads(
        (resolved.state_dir / "seed_provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["source_run_id"] == "verified-beam-source"


def test_verified_beam_seed_rejects_tampered_artifact(tmp_path: Path) -> None:
    source_run = _make_source_run(tmp_path / "source")
    (source_run / "workspace" / "beam_tension.odb").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="evidence mismatch"):
        load_verified_beam_seed(source_run)


def test_verified_beam_seed_rejects_revised_outcome_with_old_verification(
    tmp_path: Path,
) -> None:
    source_run = _make_source_run(tmp_path / "source")
    outcome_path = source_run / "state" / "outcome.json"
    outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
    outcome["task_revision"] = 1
    outcome["amendment_sequence"] = 1
    outcome_path.write_text(json.dumps(outcome), encoding="utf-8")

    with pytest.raises(ValueError, match="original completed revision"):
        load_verified_beam_seed(source_run)


def test_beam_section_change_gate_requires_two_of_three() -> None:
    outputs = iter(
        [
            _observation(passed=True),
            _observation(passed=False),
            _observation(passed=True),
        ]
    )

    async def task(
        _inputs: BeamSectionChangeEvalInput,
    ) -> BeamSectionChangeObservation:
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


def _make_source_run(run_dir: Path) -> Path:
    workspace = run_dir / "workspace"
    state_dir = run_dir / "state"
    workspace.mkdir(parents=True)
    state_dir.mkdir()
    content = {
        "build_model.py": b"print('build')\n",
        "extract_results.py": b"print('extract')\n",
        "beam_tension.cae": b"cae",
        "beam_tension.inp": b"inp",
        "beam_tension.odb": b"odb",
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
        run_id="verified-beam-source",
        goal=GOLD_REQUEST,
        workspace=workspace.resolve(),
        state_dir=state_dir.resolve(),
        runtime=RuntimeProfile(launcher_path=(run_dir / "abq2024.bat").resolve()),
    )
    outcome = CodingRunOutcome(
        run_id="verified-beam-source",
        status=CodingRunStatus.COMPLETED,
        summary="verified B31 source",
        artifacts=tuple(artifacts),
    )
    measurements = _measurements(
        profile_a=10.0,
        left_sum_rf1=-21_000.0,
        right_sum_rf1=21_000.0,
        mean_sf1=21_000.0,
        sf1_min=21_000.0,
        sf1_max=21_000.0,
    )
    source_verification = verify_beam_tension(
        measurements,
        job_completed=True,
        artifact_freshness=dict.fromkeys(content, True),
        agent_results={
            "right_mean_u1": 0.1,
            "left_sum_rf1": -21_000.0,
            "mean_sf1": 21_000.0,
        },
        input_contract_passed=True,
    )
    (state_dir / "request.json").write_text(
        request.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (state_dir / "outcome.json").write_text(
        outcome.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (state_dir / "verification.json").write_text(
        source_verification.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return run_dir


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
        "right_mean_u1": 0.1,
        "max_abs_transverse_u": 0.0,
        "left_sum_rf1": -42_000.0,
        "right_sum_rf1": 42_000.0,
        "mean_sf1": 42_000.0,
        "sf1_min": 42_000.0,
        "sf1_max": 42_000.0,
        "sf1_value_count": 20,
        "sf1_element_coverage": True,
        "sf1_has_mixed_signs": False,
        "max_abs_transverse_sf": 0.0,
        "material_name": "Steel",
        "elastic_e": 210_000.0,
        "elastic_nu": 0.3,
        "profile_name": "Profile",
        "profile_a": 20.0,
        "profile_b": 10.0,
        "section_name": "Section",
        "section_material_name": "Steel",
        "section_profile_name": "Profile",
        "section_integration": "DURING_ANALYSIS",
        "section_assignment_name": "Section",
        "section_assignment_covers_all_elements": True,
        "orientation_method": "N1_COSINES",
        "orientation_vector": (0.0, 0.0, 1.0),
        "orientation_covers_all_elements": True,
    }
    values.update(overrides)
    return BeamTensionMeasurements.model_validate(values)


def _observation(*, passed: bool) -> BeamSectionChangeObservation:
    measurements = _measurements(
        right_mean_u1=0.1 if passed else 0.08,
    )
    freshness = {
        "build_model.py": True,
        "extract_results.py": False,
        "beam_tension.cae": True,
        "beam_tension.inp": True,
        "beam_tension.odb": True,
        "results.json": True,
    }
    verification = verify_beam_section_change(
        measurements,
        job_completed=True,
        artifact_freshness=freshness,
        source_run_verified=True,
        build_script_changed=True,
        extractor_preserved=True,
        execution_provenance_passed=True,
        agent_results={
            "right_mean_u1": measurements.right_mean_u1,
            "left_sum_rf1": measurements.left_sum_rf1,
            "mean_sf1": measurements.mean_sf1,
        },
        input_contract_passed=True,
    )
    attempt = BeamSectionChangeVerificationAttempt(
        passed=verification.passed,
        feedback="synthetic",
        measurements=measurements,
        verification=verification,
        change_report=WorkspaceChangeReport(
            source_run_id="verified-beam-source",
            changes=(),
            build_model_patch="diff build",
            extract_results_patch="",
        ),
        failure_stage=None if verification.passed else "verification",
    )
    return BeamSectionChangeObservation(
        run_id="beam-section-test",
        source_run_id="verified-beam-source",
        outcome=CodingRunOutcome(
            run_id="beam-section-test",
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
