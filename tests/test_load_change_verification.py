from __future__ import annotations

from pathlib import Path

from abaqus_sim_agent.verification.load_change import (
    BoundaryRecord,
    LoadModeInputContract,
    LoadModeMeasurements,
    TractionRecord,
    parse_load_mode_input,
    verify_load_mode_change,
)


def _measurements(**overrides: object) -> LoadModeMeasurements:
    values: dict[str, object] = {
        "odb_path": "plate_tension.odb",
        "step_name": "Tension",
        "frame_index": 1,
        "analysis_step_count": 1,
        "step_procedure": "*STATIC",
        "step_nlgeom": False,
        "instance_name": "PLATE-1",
        "node_count": 105,
        "element_type_counts": {"CPS4R": 80},
        "x_min": 0.0,
        "x_max": 100.0,
        "y_min": 0.0,
        "y_max": 20.0,
        "surface_name": "RIGHT_TRACTION",
        "surface_is_full_right_edge": True,
        "surface_unique_node_count": 5,
        "surface_element_count": 4,
        "elastic_modulus": 210000.0,
        "poisson_ratio": 0.3,
        "section_thickness": 1.0,
        "section_material_name": "Steel",
        "material_name": "Steel",
        "section_assignment_count": 1,
        "section_assignment_covers_all_elements": True,
        "field_outputs": ("E", "RF", "S", "U"),
        "right_mean_u1": 0.1,
        "max_abs_left_u1": 0.0,
        "pinned_u2": 0.0,
        "left_sum_rf1": -4200.0,
        "right_sum_rf1": 0.0,
        "mean_s11": 210.0,
    }
    values.update(overrides)
    return LoadModeMeasurements.model_validate(values)


def _contract(**overrides: object) -> LoadModeInputContract:
    values: dict[str, object] = {
        "elastic_modulus": 210000.0,
        "poisson_ratio": 0.3,
        "thickness": 1.0,
        "material_name": "Steel",
        "section_material_name": "Steel",
        "element_types": ("CPS4R",),
        "step_count": 1,
        "static_step_count": 1,
        "nlgeom_off": True,
        "field_output_variables": ("RF", "S", "U"),
        "boundary_records": (
            BoundaryRecord(region="LEFT", first_dof=1, last_dof=1, magnitude=0.0),
            BoundaryRecord(region="PIN", first_dof=2, last_dof=2, magnitude=0.0),
        ),
        "traction_records": (
            TractionRecord(
                surface_name="RIGHT_TRACTION",
                load_type="TRVEC",
                magnitude=210.0,
                direction=(1.0, 0.0, 0.0),
                follower=False,
            ),
        ),
        "surface_names": ("RIGHT_TRACTION",),
        "forbidden_keywords": (),
    }
    values.update(overrides)
    return LoadModeInputContract.model_validate(values)


def _freshness() -> dict[str, bool]:
    return {
        "build_model.py": True,
        "extract_results.py": False,
        "plate_tension.cae": True,
        "plate_tension.inp": True,
        "plate_tension.odb": True,
        "results.json": True,
    }


def _agent_results() -> dict[str, float]:
    return {
        "right_mean_u1": 0.1,
        "mean_s11": 210.0,
        "left_sum_rf1": -4200.0,
    }


def test_load_mode_verification_passes_reference_contract() -> None:
    verification = verify_load_mode_change(
        _measurements(),
        _contract(),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert verification.passed
    assert verification.new_requirement_passed
    assert verification.regression_passed
    assert verification.extractor_preserved


def test_load_mode_rejects_old_right_displacement_and_wrong_direction() -> None:
    verification = verify_load_mode_change(
        _measurements(),
        _contract(
            boundary_records=(
                BoundaryRecord(region="LEFT", first_dof=1, last_dof=1, magnitude=0.0),
                BoundaryRecord(region="PIN", first_dof=2, last_dof=2, magnitude=0.0),
                BoundaryRecord(region="RIGHT", first_dof=1, last_dof=1, magnitude=0.1),
            ),
            traction_records=(
                TractionRecord(
                    surface_name="RIGHT_TRACTION",
                    load_type="TRVEC",
                    magnitude=210.0,
                    direction=(-1.0, 0.0, 0.0),
                    follower=False,
                ),
            ),
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.displacement_mode_removed
    assert not verification.traction_contract_passed
    assert not verification.traction_contract.direction_passed
    assert any(
        finding.startswith("traction direction mismatch:")
        for finding in verification.findings
    )


def test_load_mode_rejects_changed_extractor_and_wrong_surface() -> None:
    freshness = _freshness()
    freshness["extract_results.py"] = True
    verification = verify_load_mode_change(
        _measurements(surface_is_full_right_edge=False),
        _contract(),
        job_completed=True,
        artifact_freshness=freshness,
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.extractor_preserved
    assert not verification.loaded_surface_passed


def test_load_mode_reports_non_follower_mismatch_as_a_field_level_difference() -> None:
    verification = verify_load_mode_change(
        _measurements(),
        _contract(
            traction_records=(
                TractionRecord(
                    surface_name="RIGHT_TRACTION",
                    load_type="TRVEC",
                    magnitude=210.0,
                    direction=(1.0, 0.0, 0.0),
                    follower=True,
                ),
            ),
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.traction_contract_passed
    assert verification.traction_contract.record_count_passed
    assert verification.traction_contract.load_type_passed
    assert verification.traction_contract.magnitude_passed
    assert verification.traction_contract.direction_passed
    assert not verification.traction_contract.follower_passed
    assert verification.traction_contract.follower_actual is True
    assert verification.traction_contract.surface_declared
    assert verification.traction_contract.findings == (
        "traction follower mismatch: expected false (non-follower), got true; "
        "set follower=OFF so the INP emits *DSLOAD, follower=NO",
    )
    assert verification.findings == verification.traction_contract.findings


def test_load_mode_reports_traction_record_count_without_guessing_fields() -> None:
    verification = verify_load_mode_change(
        _measurements(),
        _contract(traction_records=()),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert not verification.traction_contract.passed
    assert verification.traction_contract.record_count == 0
    assert verification.traction_contract.load_type_actual is None
    assert verification.traction_contract.findings == (
        "traction record count mismatch: expected exactly one *DSLOAD traction "
        "record, got 0",
    )


def test_parse_load_mode_input_extracts_dsload_and_two_zero_bcs(
    tmp_path: Path,
) -> None:
    inp = tmp_path / "plate_tension.inp"
    inp.write_text(
        """
*Heading
*Element, type=CPS4R
1, 1, 2, 3, 4
*Solid Section, elset=PLATE, material=Steel
1.0,
*Surface, type=ELEMENT, name=RIGHT_TRACTION
RIGHT_FACE, S2
*Material, name=Steel
*Elastic
210000.0, 0.3
*Boundary
LEFT, 1, 1
*Boundary
PIN, 2, 2
*Step, name=Tension, nlgeom=NO
*Static
1.0, 1.0
*Dsload, follower=NO
RIGHT_TRACTION, TRVEC, 210.0, 1.0, 0.0, 0.0
*Output, field
*Node Output
RF, U
*Element Output
S
*End Step
""".strip(),
        encoding="utf-8",
    )

    contract = parse_load_mode_input(inp)

    assert len(contract.boundary_records) == 2
    assert len(contract.traction_records) == 1
    [traction] = contract.traction_records
    assert traction.surface_name == "RIGHT_TRACTION"
    assert traction.load_type == "TRVEC"
    assert traction.magnitude == 210.0
    assert traction.direction == (1.0, 0.0, 0.0)
    assert not traction.follower
    assert contract.forbidden_keywords == ()
