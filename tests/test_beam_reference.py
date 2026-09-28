from pathlib import Path

from abaqus_sim_agent.verification.beam_input import beam_input_contract_passed
from abaqus_sim_agent.verification.beam_reference import (
    BeamTensionMeasurements,
    verify_beam_tension,
)


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
        "max_abs_transverse_u": 1.0e-17,
        "left_sum_rf1": -21000.0,
        "right_sum_rf1": 21000.0,
        "mean_sf1": 21000.0,
        "sf1_min": 21000.0,
        "sf1_max": 21000.0,
        "sf1_value_count": 20,
        "sf1_element_coverage": True,
        "sf1_has_mixed_signs": False,
        "max_abs_transverse_sf": 1.0e-25,
        "material_name": "STEEL",
        "elastic_e": 210000.0,
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
    }
    values.update(overrides)
    return BeamTensionMeasurements.model_validate(values)


def _freshness() -> dict[str, bool]:
    return {
        "build_model.py": True,
        "extract_results.py": True,
        "beam_tension.cae": True,
        "beam_tension.inp": True,
        "beam_tension.odb": True,
        "results.json": True,
    }


def _agent_results() -> dict[str, float]:
    return {
        "right_mean_u1": 0.1,
        "left_sum_rf1": -21000.0,
        "mean_sf1": 21000.0,
    }


def test_beam_tension_verification_passes_reference_measurements() -> None:
    result = verify_beam_tension(
        _measurements(),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert result.passed
    assert result.findings == ()


def test_beam_tension_verification_accepts_before_analysis_section() -> None:
    result = verify_beam_tension(
        _measurements(section_integration="BEFORE_ANALYSIS"),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert result.passed
    assert result.section_linkage_passed


def test_beam_tension_verification_rejects_stale_artifact() -> None:
    freshness = _freshness()
    freshness["beam_tension.odb"] = False

    result = verify_beam_tension(
        _measurements(),
        job_completed=True,
        artifact_freshness=freshness,
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert not result.passed
    assert not result.artifacts_fresh


def test_beam_tension_verification_rejects_wrong_mesh_and_geometry() -> None:
    shifted_coordinates = tuple(float(index * 5 + 1) for index in range(21))
    result = verify_beam_tension(
        _measurements(
            element_type_counts={"T3D2": 20},
            node_count=20,
            connectivity_is_x_chain=False,
            coordinate_layer_counts=(20, 1, 1),
            x_coordinates=shifted_coordinates,
            x_span=95.0,
            max_abs_transverse_coordinate=2.0,
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert not result.passed
    assert not result.element_type_passed
    assert not result.element_count_passed
    assert not result.node_count_passed
    assert not result.connectivity_passed
    assert not result.coordinate_layers_passed
    assert not result.coordinates_passed
    assert not result.x_span.passed
    assert not result.transverse_coordinate.passed


def test_beam_tension_verification_rejects_wrong_section_and_material() -> None:
    result = verify_beam_tension(
        _measurements(
            elastic_e=70000.0,
            elastic_nu=0.25,
            profile_a=5.0,
            section_profile_name="OTHER_PROFILE",
            section_assignment_covers_all_elements=False,
            orientation_vector=(1.0, 0.0, 0.0),
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert not result.passed
    assert not result.material_passed
    assert not result.profile_passed
    assert not result.section_linkage_passed
    assert not result.orientation_passed


def test_beam_tension_verification_rejects_unbalanced_or_nonuniform_force() -> None:
    result = verify_beam_tension(
        _measurements(
            analysis_step_count=2,
            step_procedure="*DYNAMIC",
            step_nlgeom=True,
            right_sum_rf1=18000.0,
            sf1_min=-21000.0,
            sf1_max=21000.0,
            sf1_value_count=19,
            sf1_element_coverage=False,
            sf1_has_mixed_signs=True,
            max_abs_transverse_u=0.01,
            max_abs_transverse_sf=1000.0,
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert not result.passed
    assert not result.analysis_step_count_passed
    assert not result.step_definition_passed
    assert not result.right_rf1.passed
    assert not result.rf1_balance.passed
    assert not result.sf1_uniformity.passed
    assert not result.sf1_value_count_passed
    assert not result.sf1_element_coverage_passed
    assert not result.sf1_sign_passed
    assert not result.transverse_u.passed
    assert not result.transverse_sf.passed


def test_beam_tension_verification_rejects_fabricated_results_json() -> None:
    result = verify_beam_tension(
        _measurements(),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results={
            "right_mean_u1": 0.1,
            "left_sum_rf1": 0.0,
            "mean_sf1": 21000.0,
        },
        input_contract_passed=True,
    )

    assert not result.passed
    assert result.results_json_valid
    assert not result.results_match_oracle


def test_beam_input_contract_accepts_reference_and_rejects_extra_right_dof(
    tmp_path: Path,
) -> None:
    inp_text = """\
*Heading
*Boundary
LEFT, ENCASTRE
*Step, name=Tension, nlgeom=NO
*Static
1., 1.
*Boundary
RIGHT, 1, 1, 0.1
*End Step
"""
    valid_path = tmp_path / "valid.inp"
    valid_path.write_text(inp_text, encoding="ascii")
    assert beam_input_contract_passed(valid_path)

    invalid_path = tmp_path / "invalid.inp"
    invalid_path.write_text(
        inp_text.replace("RIGHT, 1, 1, 0.1", "RIGHT, 1, 1, 0.1\nRIGHT, 2, 2, 0.0"),
        encoding="ascii",
    )
    assert not beam_input_contract_passed(invalid_path)

    loaded_path = tmp_path / "loaded.inp"
    loaded_path.write_text(
        inp_text.replace("*End Step", "*Cload\nRIGHT, 4, 100.0\n*End Step"),
        encoding="ascii",
    )
    assert not beam_input_contract_passed(loaded_path)
