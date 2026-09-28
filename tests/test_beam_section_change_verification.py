from abaqus_sim_agent.verification.beam_reference import BeamTensionMeasurements
from abaqus_sim_agent.verification.beam_section_change import (
    BeamSectionChangeOracleSpec,
    verify_beam_section_change,
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
        "left_sum_rf1": -42_000.0,
        "right_sum_rf1": 42_000.0,
        "mean_sf1": 42_000.0,
        "sf1_min": 42_000.0,
        "sf1_max": 42_000.0,
        "sf1_value_count": 20,
        "sf1_element_coverage": True,
        "sf1_has_mixed_signs": False,
        "max_abs_transverse_sf": 1.0e-25,
        "material_name": "STEEL",
        "elastic_e": 210_000.0,
        "elastic_nu": 0.3,
        "profile_name": "PROFILE",
        "profile_a": 20.0,
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
        "extract_results.py": False,
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
        "right_mean_u1": values.right_mean_u1,
        "left_sum_rf1": values.left_sum_rf1,
        "mean_sf1": values.mean_sf1,
    }


def test_beam_section_change_passes_new_profile_and_preserved_contract() -> None:
    verification = verify_beam_section_change(
        _measurements(),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        build_script_changed=True,
        extractor_preserved=True,
        execution_provenance_passed=True,
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert verification.passed
    assert verification.new_requirement_passed
    assert verification.regression_passed
    assert verification.beam_verification.profile_a.actual == 20.0
    assert verification.beam_verification.left_rf1.actual == -42_000.0
    assert verification.findings == ()


def test_beam_section_change_rejects_old_profile_and_old_force() -> None:
    measurements = _measurements(
        profile_a=10.0,
        left_sum_rf1=-21_000.0,
        right_sum_rf1=21_000.0,
        mean_sf1=21_000.0,
        sf1_min=21_000.0,
        sf1_max=21_000.0,
    )
    verification = verify_beam_section_change(
        measurements,
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=True,
        build_script_changed=True,
        extractor_preserved=True,
        execution_provenance_passed=True,
        agent_results=_agent_results(measurements),
        input_contract_passed=True,
    )

    assert not verification.passed
    assert not verification.new_requirement_passed
    assert not verification.beam_verification.profile_passed
    assert not verification.beam_verification.left_rf1.passed
    assert not verification.beam_verification.sf1.passed


def test_beam_section_change_rejects_seed_and_provenance_regressions() -> None:
    verification = verify_beam_section_change(
        _measurements(),
        job_completed=True,
        artifact_freshness=_freshness(),
        source_run_verified=False,
        build_script_changed=False,
        extractor_preserved=False,
        execution_provenance_passed=False,
        agent_results=_agent_results(),
        input_contract_passed=True,
    )

    assert not verification.passed
    assert not verification.regression_passed
    assert not verification.beam_verification.artifacts_fresh
    assert {
        "source B31 CodingRun was not independently verified",
        "build_model.py was not changed from the verified seed",
        "extract_results.py changed despite an unchanged result contract",
        "current artifacts do not satisfy the three-stage provenance",
    } <= set(verification.findings)


def test_beam_section_change_spec_encodes_axial_stiffness_prediction() -> None:
    spec = BeamSectionChangeOracleSpec()

    area = spec.profile_a * spec.profile_b
    predicted_force = spec.elastic_e * area * spec.right_mean_u1 / spec.x_span

    assert area == 200.0
    assert predicted_force == 42_000.0
    assert spec.left_rf1 == -predicted_force
    assert spec.right_rf1 == predicted_force
    assert spec.mean_sf1 == predicted_force
