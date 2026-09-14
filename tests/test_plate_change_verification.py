from __future__ import annotations

from pathlib import Path

from abaqus_sim_agent.verification.plate_change import (
    PlateHoleChangeMeasurements,
    PlateHoleInputContract,
    parse_plate_hole_input,
    verify_plate_hole_change,
)


def _measurements(**overrides: object) -> PlateHoleChangeMeasurements:
    values: dict[str, object] = {
        "odb_path": "plate_tension.odb",
        "step_name": "Tension",
        "frame_index": 1,
        "instance_name": "PLATE-1",
        "node_count": 292,
        "element_type_counts": {"CPS4R": 250},
        "x_min": 0.0,
        "x_max": 100.0,
        "y_min": 0.0,
        "y_max": 20.0,
        "mesh_area": 1972.05,
        "hole_min_node_radius": 3.0,
        "boundary_loop_count": 2,
        "hole_boundary_edge_count": 24,
        "hole_center_x": 50.0,
        "hole_center_y": 10.0,
        "hole_max_radius_error": 1.0e-6,
        "field_outputs": ("E", "RF", "S", "U"),
        "right_mean_u1": 0.1,
        "left_mean_u1": 0.0,
        "pinned_u2": 0.0,
        "left_sum_rf1": -4014.54,
        "right_sum_rf1": 4014.54,
        "mean_s11": 210.4,
        "far_field_mean_s11": 200.7,
        "hole_boundary_max_s11": 517.0,
    }
    values.update(overrides)
    return PlateHoleChangeMeasurements.model_validate(values)


def _contract(**overrides: object) -> PlateHoleInputContract:
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
        "boundary_records": ((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.1)),
        "mechanical_load_keywords": (),
    }
    values.update(overrides)
    return PlateHoleInputContract.model_validate(values)


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
    measurements = _measurements()
    return {
        "right_mean_u1": measurements.right_mean_u1,
        "mean_s11": measurements.mean_s11,
        "left_sum_rf1": measurements.left_sum_rf1,
    }


def test_plate_hole_change_verification_passes_reference_contract() -> None:
    verification = verify_plate_hole_change(
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
    assert verification.findings == ()


def test_plate_hole_change_rejects_missing_hole_and_wrong_script_changes() -> None:
    freshness = _freshness()
    freshness["build_model.py"] = False
    freshness["extract_results.py"] = True
    verification = verify_plate_hole_change(
        _measurements(
            mesh_area=2000.0,
            hole_min_node_radius=10.0,
            boundary_loop_count=1,
            hole_boundary_edge_count=0,
        ),
        _contract(),
        job_completed=True,
        artifact_freshness=freshness,
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.new_requirement_passed
    assert not verification.build_script_changed
    assert not verification.extractor_preserved


def test_plate_hole_change_rejects_material_load_and_boundary_regressions() -> None:
    verification = verify_plate_hole_change(
        _measurements(right_mean_u1=0.08),
        _contract(
            elastic_modulus=70_000.0,
            boundary_records=((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.08)),
            mechanical_load_keywords=("*Cload",),
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.regression_passed
    assert not verification.material_passed
    assert not verification.analysis_passed
    assert not verification.boundary_conditions_passed


def test_parse_plate_hole_input_extracts_regression_contract(tmp_path: Path) -> None:
    path = tmp_path / "plate_tension.inp"
    path.write_text(
        """
*Heading
*Element, type=CPS4R
1, 1, 2, 3, 4
*Solid Section, elset=Plate, material=Steel
1.0,
*Material, name=Steel
*Elastic
210000.0, 0.3
*Boundary
Left, 1, 1
*Boundary
Pin, 2, 2
*Step, name=Tension, nlgeom=NO
*Static
1.0, 1.0
*Boundary
Right, 1, 1, 0.1
*Output, field
*Node Output
RF, U
*Element Output
S
*Output, history
*Node Output
RF1, RF2
*End Step
""".strip(),
        encoding="utf-8",
    )

    contract = parse_plate_hole_input(path)

    assert contract.elastic_modulus == 210000.0
    assert contract.poisson_ratio == 0.3
    assert contract.thickness == 1.0
    assert contract.element_types == ("CPS4R",)
    assert contract.boundary_records == (
        (1, 1, 0.0),
        (2, 2, 0.0),
        (1, 1, 0.1),
    )
    assert contract.field_output_variables == ("RF", "S", "U")
    assert contract.history_output_variables == ("RF1", "RF2")
    assert contract.mechanical_load_keywords == ()
