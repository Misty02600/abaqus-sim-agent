from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlateTensionOracleSpec(_FrozenModel):
    element_type: str = "CPS4R"
    allowed_element_types: tuple[str, ...] | None = None
    element_count: int = Field(default=80, gt=0)
    right_mean_u1: float = 0.1
    mean_s11: float = 210.0
    left_rf1_magnitude: float = 4200.0
    relative_tolerance: float = Field(default=0.02, gt=0, lt=1)


class PlateTensionMeasurements(_FrozenModel):
    odb_path: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    instance_name: str = Field(min_length=1)
    element_type_counts: dict[str, int]
    right_mean_u1: float
    mean_s11: float
    left_sum_rf1: float


class NumericCheck(_FrozenModel):
    passed: bool
    expected: float
    actual: float
    relative_error: float = Field(ge=0)
    tolerance: float = Field(ge=0)


class PlateTensionVerification(_FrozenModel):
    passed: bool
    job_completed: bool
    artifacts_fresh: bool
    element_type_passed: bool
    element_count_passed: bool
    results_json_valid: bool
    results_match_oracle: bool
    u1: NumericCheck
    s11: NumericCheck
    rf1: NumericCheck
    findings: tuple[str, ...] = ()


def verify_plate_tension(
    measurements: PlateTensionMeasurements,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    agent_results: Mapping[str, float] | None,
    spec: PlateTensionOracleSpec | None = None,
) -> PlateTensionVerification:
    """根据独立 ODB 测量和产物证据验收二维拉伸 Gold case。"""
    expected = spec or PlateTensionOracleSpec()
    allowed_element_types = expected.allowed_element_types or (expected.element_type,)
    required_artifacts = {
        "build_model.py",
        "extract_results.py",
        "plate_tension.cae",
        "plate_tension.inp",
        "plate_tension.odb",
        "results.json",
    }
    artifacts_fresh = required_artifacts.issubset(artifact_freshness) and all(
        artifact_freshness[path] for path in required_artifacts
    )
    actual_element_types = set(measurements.element_type_counts)
    element_type_passed = len(
        actual_element_types
    ) == 1 and actual_element_types.issubset(allowed_element_types)
    element_count = sum(
        measurements.element_type_counts.get(element_type, 0)
        for element_type in allowed_element_types
    )
    element_count_passed = (
        element_type_passed and element_count == expected.element_count
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
        findings.append(
            "ODB element types must contain exactly one allowed type from "
            f"{allowed_element_types}; got {tuple(sorted(actual_element_types))}"
        )
    if not element_count_passed:
        findings.append(
            f"expected {expected.element_count} elements across allowed types "
            f"{allowed_element_types}, got {element_count}"
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

    passed = (
        job_completed
        and artifacts_fresh
        and element_type_passed
        and element_count_passed
        and results_json_valid
        and results_match_oracle
        and u1.passed
        and s11.passed
        and rf1.passed
    )
    return PlateTensionVerification(
        passed=passed,
        job_completed=job_completed,
        artifacts_fresh=artifacts_fresh,
        element_type_passed=element_type_passed,
        element_count_passed=element_count_passed,
        results_json_valid=results_json_valid,
        results_match_oracle=results_match_oracle,
        u1=u1,
        s11=s11,
        rf1=rf1,
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
    measurements: PlateTensionMeasurements,
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
