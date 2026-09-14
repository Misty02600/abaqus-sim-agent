from __future__ import annotations

import math
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.verification.plate_tension import NumericCheck

_EXPECTED_X_COORDINATES = tuple(float(index * 5) for index in range(21))


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BeamTensionOracleSpec(_FrozenModel):
    element_type: str = "B31"
    element_count: int = Field(default=20, gt=0)
    node_count: int = Field(default=21, gt=0)
    coordinate_layer_counts: tuple[int, int, int] = (21, 1, 1)
    x_coordinates: tuple[float, ...] = _EXPECTED_X_COORDINATES
    x_span: float = 100.0
    analysis_step_count: int = Field(default=1, gt=0)
    elastic_e: float = 210000.0
    elastic_nu: float = 0.3
    profile_a: float = 10.0
    profile_b: float = 10.0
    right_mean_u1: float = 0.1
    left_rf1: float = -21000.0
    right_rf1: float = 21000.0
    mean_sf1: float = 21000.0
    sf1_value_count: int = Field(default=20, gt=0)
    geometry_relative_tolerance: float = Field(default=1.0e-8, gt=0, lt=1)
    zero_coordinate_tolerance: float = Field(default=1.0e-9, gt=0)
    zero_displacement_tolerance: float = Field(default=1.0e-9, gt=0)
    zero_force_tolerance: float = Field(default=1.0e-6, gt=0)
    relative_tolerance: float = Field(default=0.02, gt=0, lt=1)


class BeamTensionMeasurements(_FrozenModel):
    odb_path: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    analysis_step_count: int = Field(gt=0)
    step_procedure: str = Field(min_length=1)
    step_nlgeom: bool
    instance_name: str = Field(min_length=1)
    element_type_counts: dict[str, int]
    node_count: int = Field(gt=0)
    connectivity_is_x_chain: bool
    coordinate_layer_counts: tuple[int, int, int]
    x_coordinates: tuple[float, ...]
    x_span: float
    max_abs_transverse_coordinate: float = Field(ge=0)
    right_mean_u1: float
    max_abs_transverse_u: float = Field(ge=0)
    left_sum_rf1: float
    right_sum_rf1: float
    mean_sf1: float
    sf1_min: float
    sf1_max: float
    sf1_value_count: int = Field(gt=0)
    sf1_element_coverage: bool
    sf1_has_mixed_signs: bool
    max_abs_transverse_sf: float = Field(ge=0)
    material_name: str = Field(min_length=1)
    elastic_e: float
    elastic_nu: float
    profile_name: str = Field(min_length=1)
    profile_a: float
    profile_b: float
    section_name: str = Field(min_length=1)
    section_material_name: str = Field(min_length=1)
    section_profile_name: str = Field(min_length=1)
    section_integration: str = Field(min_length=1)
    section_assignment_name: str = Field(min_length=1)
    section_assignment_covers_all_elements: bool
    orientation_method: str = Field(min_length=1)
    orientation_vector: tuple[float, float, float]
    orientation_covers_all_elements: bool
    right_mean_u2: float = 0.0
    left_sum_rf2: float = 0.0
    right_sum_rf2: float = 0.0
    left_sum_rm3: float = 0.0
    right_sum_rm3: float = 0.0
    max_section_shear: float = 0.0
    max_section_moment: float = 0.0
    sm_value_count: int = Field(default=0, ge=0)
    sm_element_coverage: bool = False


class BeamTensionVerification(_FrozenModel):
    passed: bool
    job_completed: bool
    artifacts_fresh: bool
    input_contract_passed: bool
    analysis_step_count_passed: bool
    step_definition_passed: bool
    element_type_passed: bool
    element_count_passed: bool
    node_count_passed: bool
    connectivity_passed: bool
    coordinate_layers_passed: bool
    coordinates_passed: bool
    material_passed: bool
    profile_passed: bool
    section_linkage_passed: bool
    orientation_passed: bool
    sf1_value_count_passed: bool
    sf1_element_coverage_passed: bool
    sf1_sign_passed: bool
    results_json_valid: bool
    results_match_oracle: bool
    x_span: NumericCheck
    transverse_coordinate: NumericCheck
    elastic_e: NumericCheck
    elastic_nu: NumericCheck
    profile_a: NumericCheck
    profile_b: NumericCheck
    u1: NumericCheck
    transverse_u: NumericCheck
    left_rf1: NumericCheck
    right_rf1: NumericCheck
    rf1_balance: NumericCheck
    sf1: NumericCheck
    sf1_uniformity: NumericCheck
    transverse_sf: NumericCheck
    findings: tuple[str, ...] = ()


def verify_beam_tension(
    measurements: BeamTensionMeasurements,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    agent_results: Mapping[str, float] | None,
    input_contract_passed: bool,
    spec: BeamTensionOracleSpec | None = None,
) -> BeamTensionVerification:
    """根据独立 ODB 测量和产物证据验收 B31 轴向拉伸 Gold case。"""
    expected = spec or BeamTensionOracleSpec()
    required_artifacts = {
        "build_model.py",
        "extract_results.py",
        "beam_tension.cae",
        "beam_tension.inp",
        "beam_tension.odb",
        "results.json",
    }
    artifacts_fresh = required_artifacts.issubset(artifact_freshness) and all(
        artifact_freshness[path] for path in required_artifacts
    )
    analysis_step_count_passed = (
        measurements.analysis_step_count == expected.analysis_step_count
    )
    step_definition_passed = (
        measurements.step_procedure == "*STATIC" and not measurements.step_nlgeom
    )
    element_count = measurements.element_type_counts.get(expected.element_type, 0)
    element_type_passed = set(measurements.element_type_counts) == {
        expected.element_type
    }
    element_count_passed = element_count == expected.element_count
    node_count_passed = measurements.node_count == expected.node_count
    connectivity_passed = measurements.connectivity_is_x_chain
    coordinate_layers_passed = (
        measurements.coordinate_layer_counts == expected.coordinate_layer_counts
    )
    coordinates_passed = _coordinates_match(
        expected.x_coordinates,
        measurements.x_coordinates,
        expected.geometry_relative_tolerance,
    )
    x_span = _numeric_check(
        expected.x_span,
        measurements.x_span,
        expected.geometry_relative_tolerance,
    )
    transverse_coordinate = _zero_check(
        measurements.max_abs_transverse_coordinate,
        expected.zero_coordinate_tolerance,
    )

    elastic_e = _numeric_check(
        expected.elastic_e,
        measurements.elastic_e,
        expected.geometry_relative_tolerance,
    )
    elastic_nu = _numeric_check(
        expected.elastic_nu,
        measurements.elastic_nu,
        expected.geometry_relative_tolerance,
    )
    material_passed = elastic_e.passed and elastic_nu.passed
    profile_a = _numeric_check(
        expected.profile_a,
        measurements.profile_a,
        expected.geometry_relative_tolerance,
    )
    profile_b = _numeric_check(
        expected.profile_b,
        measurements.profile_b,
        expected.geometry_relative_tolerance,
    )
    profile_passed = profile_a.passed and profile_b.passed
    section_linkage_passed = (
        measurements.section_material_name == measurements.material_name
        and measurements.section_profile_name == measurements.profile_name
        and measurements.section_assignment_name == measurements.section_name
        and measurements.section_integration in {"BEFORE_ANALYSIS", "DURING_ANALYSIS"}
        and measurements.section_assignment_covers_all_elements
    )
    orientation_norm = math.sqrt(
        sum(component * component for component in measurements.orientation_vector)
    )
    orientation_passed = (
        measurements.orientation_method == "N1_COSINES"
        and measurements.orientation_covers_all_elements
        and abs(measurements.orientation_vector[0])
        <= expected.geometry_relative_tolerance
        and abs(orientation_norm - 1.0) <= expected.geometry_relative_tolerance
    )

    u1 = _numeric_check(
        expected.right_mean_u1,
        measurements.right_mean_u1,
        expected.relative_tolerance,
    )
    transverse_u = _zero_check(
        measurements.max_abs_transverse_u,
        expected.zero_displacement_tolerance,
    )
    left_rf1 = _numeric_check(
        expected.left_rf1,
        measurements.left_sum_rf1,
        expected.relative_tolerance,
    )
    right_rf1 = _numeric_check(
        expected.right_rf1,
        measurements.right_sum_rf1,
        expected.relative_tolerance,
    )
    rf1_balance = _zero_check(
        abs(measurements.left_sum_rf1 + measurements.right_sum_rf1)
        / abs(expected.right_rf1),
        expected.relative_tolerance,
    )
    sf1 = _numeric_check(
        expected.mean_sf1,
        measurements.mean_sf1,
        expected.relative_tolerance,
    )
    sf1_uniformity = _zero_check(
        abs(measurements.sf1_max - measurements.sf1_min) / abs(expected.mean_sf1),
        expected.relative_tolerance,
    )
    sf1_value_count_passed = measurements.sf1_value_count == expected.sf1_value_count
    sf1_element_coverage_passed = measurements.sf1_element_coverage
    sf1_sign_passed = not measurements.sf1_has_mixed_signs
    transverse_sf = _zero_check(
        measurements.max_abs_transverse_sf,
        expected.zero_force_tolerance,
    )
    results_json_valid, results_match_oracle = _check_agent_results(
        agent_results,
        measurements,
        expected.relative_tolerance,
    )

    findings: list[str] = []
    if not job_completed:
        findings.append("Abaqus status evidence does not show a completed analysis")
    if not artifacts_fresh:
        findings.append("one or more required artifacts are missing or stale")
    if not input_contract_passed:
        findings.append("INP does not contain the required Static/BC contract")
    if not analysis_step_count_passed:
        findings.append(
            f"expected {expected.analysis_step_count} analysis step, "
            f"got {measurements.analysis_step_count}"
        )
    if not step_definition_passed:
        findings.append("ODB step is not Static, General with NLGEOM disabled")
    if not element_type_passed:
        findings.append("ODB contains an unexpected element type")
    if not element_count_passed:
        findings.append(
            f"expected {expected.element_count} {expected.element_type} elements, "
            f"got {element_count}"
        )
    if not node_count_passed:
        findings.append(
            f"expected {expected.node_count} nodes, got {measurements.node_count}"
        )
    if not connectivity_passed:
        findings.append("B31 connectivity is not one 20-element axial chain")
    if not coordinate_layers_passed or not coordinates_passed:
        findings.append("beam nodes are not the expected 0..100 mm, 5 mm-spaced line")
    if not material_passed:
        findings.append("ODB material is not E=210000 MPa, nu=0.3")
    if not profile_passed:
        findings.append("ODB beam profile is not rectangular 10 x 10 mm")
    if not section_linkage_passed:
        findings.append("beam section/material/profile assignment is incomplete")
    if not orientation_passed:
        findings.append("beam orientation is missing, incomplete, or not transverse")
    if not sf1_value_count_passed or not sf1_element_coverage_passed:
        findings.append("SF1 does not cover all 20 beam elements exactly once")
    if not sf1_sign_passed:
        findings.append("SF1 contains mixed axial-force signs")
    if not results_json_valid:
        findings.append("results.json is missing required numeric measurements")
    elif not results_match_oracle:
        findings.append("results.json does not match the independent ODB oracle")
    for name, check in (
        ("X span", x_span),
        ("transverse coordinate", transverse_coordinate),
        ("E", elastic_e),
        ("nu", elastic_nu),
        ("profile a", profile_a),
        ("profile b", profile_b),
        ("U1", u1),
        ("transverse U", transverse_u),
        ("left RF1", left_rf1),
        ("right RF1", right_rf1),
        ("RF1 balance", rf1_balance),
        ("SF1", sf1),
        ("SF1 uniformity", sf1_uniformity),
        ("transverse SF", transverse_sf),
    ):
        if not check.passed:
            findings.append(
                f"{name} error {check.relative_error:.6g} exceeds {check.tolerance:.6g}"
            )

    passed = (
        job_completed
        and artifacts_fresh
        and input_contract_passed
        and analysis_step_count_passed
        and step_definition_passed
        and element_type_passed
        and element_count_passed
        and node_count_passed
        and connectivity_passed
        and coordinate_layers_passed
        and coordinates_passed
        and x_span.passed
        and transverse_coordinate.passed
        and material_passed
        and profile_passed
        and section_linkage_passed
        and orientation_passed
        and u1.passed
        and transverse_u.passed
        and left_rf1.passed
        and right_rf1.passed
        and rf1_balance.passed
        and sf1.passed
        and sf1_uniformity.passed
        and sf1_value_count_passed
        and sf1_element_coverage_passed
        and sf1_sign_passed
        and transverse_sf.passed
        and results_json_valid
        and results_match_oracle
    )
    return BeamTensionVerification(
        passed=passed,
        job_completed=job_completed,
        artifacts_fresh=artifacts_fresh,
        input_contract_passed=input_contract_passed,
        analysis_step_count_passed=analysis_step_count_passed,
        step_definition_passed=step_definition_passed,
        element_type_passed=element_type_passed,
        element_count_passed=element_count_passed,
        node_count_passed=node_count_passed,
        connectivity_passed=connectivity_passed,
        coordinate_layers_passed=coordinate_layers_passed,
        coordinates_passed=coordinates_passed,
        material_passed=material_passed,
        profile_passed=profile_passed,
        section_linkage_passed=section_linkage_passed,
        orientation_passed=orientation_passed,
        sf1_value_count_passed=sf1_value_count_passed,
        sf1_element_coverage_passed=sf1_element_coverage_passed,
        sf1_sign_passed=sf1_sign_passed,
        results_json_valid=results_json_valid,
        results_match_oracle=results_match_oracle,
        x_span=x_span,
        transverse_coordinate=transverse_coordinate,
        elastic_e=elastic_e,
        elastic_nu=elastic_nu,
        profile_a=profile_a,
        profile_b=profile_b,
        u1=u1,
        transverse_u=transverse_u,
        left_rf1=left_rf1,
        right_rf1=right_rf1,
        rf1_balance=rf1_balance,
        sf1=sf1,
        sf1_uniformity=sf1_uniformity,
        transverse_sf=transverse_sf,
        findings=tuple(findings),
    )


def _numeric_check(expected: float, actual: float, tolerance: float) -> NumericCheck:
    denominator = abs(expected)
    relative_error = (
        abs(actual - expected) / denominator if denominator else abs(actual)
    )
    return NumericCheck(
        passed=relative_error <= tolerance,
        expected=expected,
        actual=actual,
        relative_error=relative_error,
        tolerance=tolerance,
    )


def _zero_check(actual: float, tolerance: float) -> NumericCheck:
    return NumericCheck(
        passed=abs(actual) <= tolerance,
        expected=0.0,
        actual=actual,
        relative_error=abs(actual),
        tolerance=tolerance,
    )


def _coordinates_match(
    expected: tuple[float, ...],
    actual: tuple[float, ...],
    tolerance: float,
) -> bool:
    return len(actual) == len(expected) and all(
        _numeric_check(expected_value, actual_value, tolerance).passed
        for expected_value, actual_value in zip(expected, actual, strict=True)
    )


def _check_agent_results(
    agent_results: Mapping[str, float] | None,
    measurements: BeamTensionMeasurements,
    tolerance: float,
) -> tuple[bool, bool]:
    if agent_results is None:
        return False, False
    expected_values = {
        "right_mean_u1": measurements.right_mean_u1,
        "left_sum_rf1": measurements.left_sum_rf1,
        "mean_sf1": measurements.mean_sf1,
    }
    try:
        values = {key: float(agent_results[key]) for key in expected_values}
    except (KeyError, TypeError, ValueError):
        return False, False
    matches = all(
        _numeric_check(expected, values[key], tolerance).passed
        for key, expected in expected_values.items()
    )
    return True, matches
