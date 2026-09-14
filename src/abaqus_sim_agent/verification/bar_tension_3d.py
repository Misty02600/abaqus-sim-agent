from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.verification.plate_tension import NumericCheck


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BarTension3DOracleSpec(_FrozenModel):
    element_type: str = "C3D8R"
    element_count: int = Field(default=80, gt=0)
    right_mean_u1: float = 0.1
    mean_s11: float = 210.0
    left_rf1_magnitude: float = 21000.0
    node_count: int = Field(default=189, gt=0)
    axis_spans: tuple[float, float, float] = (100.0, 10.0, 10.0)
    coordinate_layer_counts: tuple[int, int, int] = (21, 3, 3)
    transverse_contraction: float = -0.003
    geometry_relative_tolerance: float = Field(default=1.0e-8, gt=0, lt=1)
    relative_tolerance: float = Field(default=0.02, gt=0, lt=1)


class BarTension3DMeasurements(_FrozenModel):
    odb_path: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    instance_name: str = Field(min_length=1)
    element_type_counts: dict[str, int]
    node_count: int = Field(gt=0)
    axis_spans: tuple[float, float, float]
    coordinate_layer_counts: tuple[int, int, int]
    right_mean_u1: float
    mean_s11: float
    left_sum_rf1: float
    y_face_mean_u2_delta: float
    z_face_mean_u3_delta: float


class BarTension3DVerification(_FrozenModel):
    passed: bool
    job_completed: bool
    artifacts_fresh: bool
    element_type_passed: bool
    element_count_passed: bool
    node_count_passed: bool
    coordinate_layers_passed: bool
    results_json_valid: bool
    results_match_oracle: bool
    x_span: NumericCheck
    y_span: NumericCheck
    z_span: NumericCheck
    u1: NumericCheck
    s11: NumericCheck
    rf1: NumericCheck
    y_contraction: NumericCheck
    z_contraction: NumericCheck
    findings: tuple[str, ...] = ()


def verify_bar_tension_3d(
    measurements: BarTension3DMeasurements,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    agent_results: Mapping[str, float] | None,
    spec: BarTension3DOracleSpec | None = None,
) -> BarTension3DVerification:
    """根据独立 ODB 测量和产物证据验收三维矩形杆拉伸 Gold case。"""
    expected = spec or BarTension3DOracleSpec()
    required_artifacts = {
        "build_model.py",
        "extract_results.py",
        "bar_tension.cae",
        "bar_tension.inp",
        "bar_tension.odb",
        "results.json",
    }
    artifacts_fresh = required_artifacts.issubset(artifact_freshness) and all(
        artifact_freshness[path] for path in required_artifacts
    )
    element_count = measurements.element_type_counts.get(expected.element_type, 0)
    element_type_passed = set(measurements.element_type_counts) == {
        expected.element_type
    }
    element_count_passed = element_count == expected.element_count
    node_count_passed = measurements.node_count == expected.node_count
    coordinate_layers_passed = (
        measurements.coordinate_layer_counts == expected.coordinate_layer_counts
    )
    span_checks = tuple(
        _numeric_check(
            expected_value,
            actual_value,
            expected.geometry_relative_tolerance,
        )
        for expected_value, actual_value in zip(
            expected.axis_spans,
            measurements.axis_spans,
            strict=True,
        )
    )
    u1 = _numeric_check(
        expected.right_mean_u1,
        measurements.right_mean_u1,
        expected.relative_tolerance,
    )
    s11 = _numeric_check(
        expected.mean_s11,
        measurements.mean_s11,
        expected.relative_tolerance,
    )
    rf1 = _numeric_check(
        expected.left_rf1_magnitude,
        abs(measurements.left_sum_rf1),
        expected.relative_tolerance,
    )
    y_contraction = _numeric_check(
        expected.transverse_contraction,
        measurements.y_face_mean_u2_delta,
        expected.relative_tolerance,
    )
    z_contraction = _numeric_check(
        expected.transverse_contraction,
        measurements.z_face_mean_u3_delta,
        expected.relative_tolerance,
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
    if not coordinate_layers_passed:
        findings.append(
            "expected coordinate layers "
            f"{expected.coordinate_layer_counts}, got "
            f"{measurements.coordinate_layer_counts}"
        )
    if not results_json_valid:
        findings.append("results.json is missing required numeric measurements")
    elif not results_match_oracle:
        findings.append("results.json does not match the independent ODB oracle")
    for name, check in (("U1", u1), ("S11", s11), ("RF1", rf1)):
        if not check.passed:
            findings.append(
                f"{name} relative error {check.relative_error:.6g} exceeds "
                f"{check.tolerance:.6g}"
            )
    for name, check in (
        ("X span", span_checks[0]),
        ("Y span", span_checks[1]),
        ("Z span", span_checks[2]),
        ("Y contraction", y_contraction),
        ("Z contraction", z_contraction),
    ):
        if not check.passed:
            findings.append(
                f"{name} relative error {check.relative_error:.6g} exceeds "
                f"{check.tolerance:.6g}"
            )

    passed = (
        job_completed
        and artifacts_fresh
        and element_type_passed
        and element_count_passed
        and node_count_passed
        and coordinate_layers_passed
        and all(check.passed for check in span_checks)
        and results_json_valid
        and results_match_oracle
        and u1.passed
        and s11.passed
        and rf1.passed
        and y_contraction.passed
        and z_contraction.passed
    )
    return BarTension3DVerification(
        passed=passed,
        job_completed=job_completed,
        artifacts_fresh=artifacts_fresh,
        element_type_passed=element_type_passed,
        element_count_passed=element_count_passed,
        node_count_passed=node_count_passed,
        coordinate_layers_passed=coordinate_layers_passed,
        results_json_valid=results_json_valid,
        results_match_oracle=results_match_oracle,
        x_span=span_checks[0],
        y_span=span_checks[1],
        z_span=span_checks[2],
        u1=u1,
        s11=s11,
        rf1=rf1,
        y_contraction=y_contraction,
        z_contraction=z_contraction,
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


def _check_agent_results(
    agent_results: Mapping[str, float] | None,
    measurements: BarTension3DMeasurements,
    tolerance: float,
) -> tuple[bool, bool]:
    if agent_results is None:
        return False, False
    expected_values = {
        "right_mean_u1": measurements.right_mean_u1,
        "mean_s11": measurements.mean_s11,
        "left_sum_rf1": measurements.left_sum_rf1,
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
