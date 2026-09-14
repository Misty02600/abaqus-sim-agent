from __future__ import annotations

from pathlib import Path

from abaqus_sim_agent.verification.material_change import (
    MaterialElasticContract,
    SolidSectionContract,
    TwoMaterialPlateInputContract,
    TwoMaterialPlateMeasurements,
    parse_two_material_plate_input,
    verify_two_material_plate_change,
)


def _measurements(**overrides: object) -> TwoMaterialPlateMeasurements:
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
        "max_abs_z": 0.0,
        "x_layer_count": 21,
        "y_layer_count": 5,
        "interface_node_count": 5,
        "interface_is_shared": True,
        "connectivity_is_single_component": True,
        "left_element_count": 40,
        "right_element_count": 40,
        "assignments_cover_each_element_once": True,
        "left_properties": ((210000.0, 0.3, 1.0),),
        "right_properties": ((70000.0, 0.3, 1.0),),
        "field_outputs": ("E", "RF", "S", "U"),
        "u_node_coverage": True,
        "s_element_coverage": True,
        "right_mean_u1": 0.10000000149011612,
        "interface_mean_u1": 0.02526366673409939,
        "max_abs_left_u1": 0.0,
        "pinned_u2": 0.0,
        "left_sum_rf1": -2101.8351440429688,
        "right_sum_rf1": 2101.8351440429688,
        "mean_s11": 105.09175720214844,
        "left_mean_s11": 105.09175720214844,
        "right_mean_s11": 105.09175720214844,
        "max_abs_s22": 11.786,
        "max_abs_s12": 2.748,
    }
    values.update(overrides)
    return TwoMaterialPlateMeasurements.model_validate(values)


def _contract(**overrides: object) -> TwoMaterialPlateInputContract:
    values: dict[str, object] = {
        "materials": (
            MaterialElasticContract(
                name="SoftAlloy", elastic_modulus=70000.0, poisson_ratio=0.3
            ),
            MaterialElasticContract(
                name="Steel", elastic_modulus=210000.0, poisson_ratio=0.3
            ),
        ),
        "solid_sections": (
            SolidSectionContract(material_name="SoftAlloy", thickness=1.0),
            SolidSectionContract(material_name="Steel", thickness=1.0),
        ),
        "element_types": ("CPS4R",),
        "step_count": 1,
        "static_step_count": 1,
        "nlgeom_off": True,
        "field_output_variables": ("RF", "S", "U"),
        "boundary_records": ((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.1)),
        "forbidden_keywords": (),
    }
    values.update(overrides)
    return TwoMaterialPlateInputContract.model_validate(values)


def _freshness() -> dict[str, bool]:
    return {
        "build_model.py": True,
        "extract_results.py": True,
        "plate_tension.cae": True,
        "plate_tension.inp": True,
        "plate_tension.odb": True,
        "results.json": True,
    }


def _agent_results(
    measurements: TwoMaterialPlateMeasurements | None = None,
) -> dict[str, float]:
    values = measurements or _measurements()
    return {
        "right_mean_u1": values.right_mean_u1,
        "interface_mean_u1": values.interface_mean_u1,
        "mean_s11": values.mean_s11,
        "left_mean_s11": values.left_mean_s11,
        "right_mean_s11": values.right_mean_s11,
        "left_sum_rf1": values.left_sum_rf1,
    }


def test_two_material_verification_passes_reference_contract() -> None:
    verification = verify_two_material_plate_change(
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


def test_two_material_verification_rejects_wrong_assignment_and_interface() -> None:
    measurements = _measurements(
        right_properties=((210000.0, 0.3, 1.0),),
        interface_is_shared=False,
    )
    verification = verify_two_material_plate_change(
        measurements,
        _contract(),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        agent_results=_agent_results(measurements),
    )

    assert not verification.passed
    assert not verification.new_requirement_passed
    assert not verification.material_assignment_passed
    assert not verification.mesh_and_interface_passed


def test_two_material_verification_rejects_baseline_regressions() -> None:
    verification = verify_two_material_plate_change(
        _measurements(right_mean_u1=0.08),
        _contract(
            boundary_records=((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.08)),
            forbidden_keywords=("*Tie",),
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.regression_passed
    assert not verification.analysis_passed
    assert not verification.boundary_conditions_passed
    assert not verification.right_u1.passed


def test_parse_two_material_input_extracts_both_materials(tmp_path: Path) -> None:
    inp = tmp_path / "plate_tension.inp"
    inp.write_text(
        """
*Heading
*Element, type=CPS4R
1, 1, 2, 3, 4
*Solid Section, elset=LEFT, material=Steel
1.0,
*Solid Section, elset=RIGHT, material=Soft
1.0,
*Material, name=Soft
*Elastic
70000.0, 0.3
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
*End Step
""".strip(),
        encoding="utf-8",
    )

    contract = parse_two_material_plate_input(inp)

    assert sorted(material.elastic_modulus for material in contract.materials) == [
        70000.0,
        210000.0,
    ]
    assert len(contract.solid_sections) == 2
    assert contract.element_types == ("CPS4R",)
    assert contract.boundary_records == (
        (1, 1, 0.0),
        (2, 2, 0.0),
        (1, 1, 0.1),
    )
    assert contract.forbidden_keywords == ()
