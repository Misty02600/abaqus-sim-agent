from pathlib import Path

from abaqus_sim_agent.verification.beam_bending_change import (
    BeamBendingInputContract,
    ConcentratedLoadRecord,
    parse_beam_bending_input,
    verify_beam_bending_change,
)
from abaqus_sim_agent.verification.beam_tension import BeamTensionMeasurements


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
        "right_mean_u1": 3.9e-18,
        "max_abs_transverse_u": 1.9190475940704346,
        "left_sum_rf1": 1.1e-12,
        "right_sum_rf1": 0.0,
        "mean_sf1": -2.1e-13,
        "sf1_min": -2.2e-12,
        "sf1_max": 7.3e-13,
        "sf1_value_count": 20,
        "sf1_element_coverage": True,
        "sf1_has_mixed_signs": True,
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


def _contract(**overrides: object) -> BeamBendingInputContract:
    values: dict[str, object] = {
        "step_count": 1,
        "static_step_count": 1,
        "nlgeom_off": True,
        "boundary_records": (("LEFT", "ENCASTRE"),),
        "concentrated_loads": (
            ConcentratedLoadRecord(region="RIGHT", dof=2, magnitude=1000.0),
        ),
        "field_output_variables": ("RF", "SF", "U"),
        "forbidden_keywords": (),
    }
    values.update(overrides)
    return BeamBendingInputContract.model_validate(values)


def _freshness() -> dict[str, bool]:
    return {
        "build_model.py": True,
        "extract_results.py": True,
        "beam_tension.cae": True,
        "beam_tension.inp": True,
        "beam_tension.odb": True,
        "results.json": True,
    }


def _agent_results(
    measurements: BeamTensionMeasurements | None = None,
) -> dict[str, float]:
    values = measurements or _measurements()
    return {
        "right_mean_u2": values.right_mean_u2,
        "left_sum_rf2": values.left_sum_rf2,
        "left_sum_rm3": values.left_sum_rm3,
        "max_section_moment": values.max_section_moment,
    }


def test_beam_bending_change_passes_tip_load_and_regression_contract() -> None:
    verification = verify_beam_bending_change(
        _measurements(),
        _contract(),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        build_script_changed=True,
        extractor_changed=True,
        execution_provenance_passed=True,
        agent_results=_agent_results(),
    )

    assert verification.passed
    assert verification.new_requirement_passed
    assert verification.regression_passed
    assert verification.right_u2.actual == 1.9190475940704346
    assert verification.left_rm3.actual == -100_000.0
    assert verification.findings == ()


def test_beam_bending_change_rejects_old_axial_displacement_mode() -> None:
    verification = verify_beam_bending_change(
        _measurements(),
        _contract(
            boundary_records=(
                ("LEFT", "ENCASTRE"),
                ("RIGHT", "1", "1", "0.1"),
            ),
            concentrated_loads=(),
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        build_script_changed=True,
        extractor_changed=True,
        execution_provenance_passed=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.input_contract_passed
    assert not verification.new_requirement_passed


def test_beam_bending_change_rejects_extra_output_variables() -> None:
    verification = verify_beam_bending_change(
        _measurements(),
        _contract(field_output_variables=("RF", "RM", "SF", "SM", "U")),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        build_script_changed=True,
        extractor_changed=True,
        execution_provenance_passed=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.input_contract_passed
    assert not verification.new_requirement_passed
    assert any("exactly U/RF/SF" in item for item in verification.findings)


def test_beam_bending_change_rejects_wrong_response_and_result_contract() -> None:
    measurements = _measurements(
        right_mean_u2=1.0,
        left_sum_rf2=-500.0,
        left_sum_rm3=-50_000.0,
        max_section_moment=50_000.0,
    )
    verification = verify_beam_bending_change(
        measurements,
        _contract(),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        build_script_changed=True,
        extractor_changed=True,
        execution_provenance_passed=True,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.right_u2.passed
    assert not verification.left_rf2.passed
    assert not verification.left_rm3.passed
    assert not verification.section_moment.passed
    assert not verification.results_match_oracle


def test_beam_bending_change_rejects_source_and_diff_regressions() -> None:
    verification = verify_beam_bending_change(
        _measurements(),
        _contract(),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=False,
        build_script_changed=False,
        extractor_changed=False,
        execution_provenance_passed=False,
        agent_results=_agent_results(),
    )

    assert not verification.passed
    assert not verification.regression_passed
    assert {
        "source B31 CodingRun was not independently verified",
        "build_model.py was not changed from the seed",
        "extract_results.py was not changed for bending results",
        "three-stage execution provenance failed",
    } <= set(verification.findings)


def test_parse_beam_bending_input_accepts_one_tip_cload(tmp_path: Path) -> None:
    inp = tmp_path / "beam_tension.inp"
    inp.write_text(
        """
*Heading
*Boundary
LEFT, ENCASTRE
*Step, name=Tension, nlgeom=NO
*Static
1., 1.
*Cload
RIGHT, 2, 1000.
*Output, field
*Node Output
RF, U
*Element Output
SF
*End Step
""".strip(),
        encoding="ascii",
    )

    contract = parse_beam_bending_input(inp)

    assert contract.step_count == 1
    assert contract.static_step_count == 1
    assert contract.nlgeom_off
    assert contract.boundary_records == (("LEFT", "ENCASTRE"),)
    assert contract.concentrated_loads == (
        ConcentratedLoadRecord(region="RIGHT", dof=2, magnitude=1000.0),
    )
    assert contract.field_output_variables == ("RF", "SF", "U")
    assert contract.forbidden_keywords == ()
