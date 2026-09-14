from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PlateHoleChangeOracleSpec(_FrozenModel):
    width: float = 100.0
    height: float = 20.0
    hole_center_x: float = 50.0
    hole_center_y: float = 10.0
    hole_radius: float = 3.0
    thickness: float = 1.0
    elastic_modulus: float = 210_000.0
    poisson_ratio: float = 0.3
    right_u1: float = 0.1
    element_type: str = "CPS4R"
    min_element_count: int = Field(default=80, gt=0)
    max_element_count: int = Field(default=5_000, gt=0)
    min_hole_boundary_edges: int = Field(default=24, gt=0)
    geometry_tolerance: float = Field(default=0.05, gt=0)
    hole_radius_tolerance: float = Field(default=0.15, gt=0)
    area_relative_tolerance: float = Field(default=0.02, gt=0, lt=1)
    result_relative_tolerance: float = Field(default=0.02, gt=0, lt=1)
    equilibrium_relative_tolerance: float = Field(default=0.02, gt=0, lt=1)
    stress_concentration_ratio: float = Field(default=1.5, gt=1)

    @property
    def expected_mesh_area(self) -> float:
        return self.width * self.height - math.pi * self.hole_radius**2


class PlateHoleInputContract(_FrozenModel):
    elastic_modulus: float | None = None
    poisson_ratio: float | None = None
    thickness: float | None = None
    material_name: str | None = None
    section_material_name: str | None = None
    element_types: tuple[str, ...] = ()
    step_count: int = Field(ge=0)
    static_step_count: int = Field(ge=0)
    nlgeom_off: bool
    field_output_variables: tuple[str, ...] = ()
    history_output_variables: tuple[str, ...] = ()
    boundary_records: tuple[tuple[int, int, float], ...] = ()
    mechanical_load_keywords: tuple[str, ...] = ()


class PlateHoleChangeMeasurements(_FrozenModel):
    odb_path: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    instance_name: str = Field(min_length=1)
    node_count: int = Field(gt=0)
    element_type_counts: dict[str, int]
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    mesh_area: float = Field(gt=0)
    hole_min_node_radius: float = Field(ge=0)
    boundary_loop_count: int = Field(ge=0)
    hole_boundary_edge_count: int = Field(ge=0)
    hole_center_x: float
    hole_center_y: float
    hole_max_radius_error: float = Field(ge=0)
    field_outputs: tuple[str, ...]
    right_mean_u1: float
    left_mean_u1: float
    pinned_u2: float
    left_sum_rf1: float
    right_sum_rf1: float
    mean_s11: float
    far_field_mean_s11: float
    hole_boundary_max_s11: float


class NumericCheck(_FrozenModel):
    passed: bool
    expected: float
    actual: float
    error: float = Field(ge=0)
    tolerance: float = Field(ge=0)


class PlateHoleChangeVerification(_FrozenModel):
    passed: bool
    job_completed: bool
    artifacts_fresh: bool
    source_run_verified: bool
    baseline_preserved: bool
    build_script_changed: bool
    extractor_preserved: bool
    new_requirement_passed: bool
    regression_passed: bool
    geometry_passed: bool
    hole_passed: bool
    mesh_passed: bool
    material_passed: bool
    analysis_passed: bool
    boundary_conditions_passed: bool
    output_fields_passed: bool
    equilibrium_passed: bool
    stress_concentration_passed: bool
    results_json_valid: bool
    results_match_oracle: bool
    right_u1: NumericCheck
    mesh_area: NumericCheck
    hole_radius: NumericCheck
    findings: tuple[str, ...] = ()


def verify_plate_hole_change(
    measurements: PlateHoleChangeMeasurements,
    input_contract: PlateHoleInputContract,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    source_run_verified: bool,
    agent_results: Mapping[str, float] | None,
    spec: PlateHoleChangeOracleSpec | None = None,
) -> PlateHoleChangeVerification:
    """联合验收中心孔新需求和基准板模型必须保留的合同。"""
    expected = spec or PlateHoleChangeOracleSpec()
    required_fresh = {
        "build_model.py",
        "plate_tension.cae",
        "plate_tension.inp",
        "plate_tension.odb",
        "results.json",
    }
    build_script_changed = artifact_freshness.get("build_model.py", False)
    extractor_preserved = (
        "extract_results.py" in artifact_freshness
        and not artifact_freshness["extract_results.py"]
    )
    artifacts_fresh = (
        required_fresh.issubset(artifact_freshness)
        and all(artifact_freshness[path] for path in required_fresh)
        and extractor_preserved
    )

    geometry_checks = (
        _absolute_check(0.0, measurements.x_min, expected.geometry_tolerance),
        _absolute_check(
            expected.width, measurements.x_max, expected.geometry_tolerance
        ),
        _absolute_check(0.0, measurements.y_min, expected.geometry_tolerance),
        _absolute_check(
            expected.height, measurements.y_max, expected.geometry_tolerance
        ),
    )
    geometry_passed = all(check.passed for check in geometry_checks)
    hole_radius = _absolute_check(
        expected.hole_radius,
        measurements.hole_min_node_radius,
        expected.hole_radius_tolerance,
    )
    mesh_area = _relative_check(
        expected.expected_mesh_area,
        measurements.mesh_area,
        expected.area_relative_tolerance,
    )
    hole_passed = (
        hole_radius.passed
        and mesh_area.passed
        and measurements.boundary_loop_count == 2
        and measurements.hole_boundary_edge_count >= expected.min_hole_boundary_edges
        and abs(measurements.hole_center_x - expected.hole_center_x)
        <= expected.geometry_tolerance
        and abs(measurements.hole_center_y - expected.hole_center_y)
        <= expected.geometry_tolerance
        and measurements.hole_max_radius_error <= expected.hole_radius_tolerance
    )

    element_count = measurements.element_type_counts.get(expected.element_type, 0)
    mesh_passed = (
        set(measurements.element_type_counts) == {expected.element_type}
        and set(input_contract.element_types) == {expected.element_type}
        and expected.min_element_count <= element_count <= expected.max_element_count
    )
    material_passed = (
        input_contract.elastic_modulus is not None
        and input_contract.poisson_ratio is not None
        and input_contract.thickness is not None
        and _relative_check(
            expected.elastic_modulus,
            input_contract.elastic_modulus,
            1.0e-9,
        ).passed
        and _absolute_check(
            expected.poisson_ratio,
            input_contract.poisson_ratio,
            1.0e-9,
        ).passed
        and _absolute_check(
            expected.thickness,
            input_contract.thickness,
            1.0e-9,
        ).passed
        and input_contract.material_name is not None
        and input_contract.material_name == input_contract.section_material_name
    )
    analysis_passed = (
        input_contract.step_count == 1
        and input_contract.static_step_count == 1
        and input_contract.nlgeom_off
        and not input_contract.mechanical_load_keywords
    )
    right_u1 = _relative_check(
        expected.right_u1,
        measurements.right_mean_u1,
        expected.result_relative_tolerance,
    )
    boundary_conditions_passed = (
        right_u1.passed
        and abs(measurements.left_mean_u1) <= 1.0e-7
        and abs(measurements.pinned_u2) <= 1.0e-7
        and sorted(input_contract.boundary_records)
        == sorted(((1, 1, 0.0), (2, 2, 0.0), (1, 1, expected.right_u1)))
    )
    required_outputs = {"U", "S", "RF"}
    output_fields_passed = required_outputs.issubset(measurements.field_outputs) and (
        required_outputs.issubset(input_contract.field_output_variables)
    )
    reaction_scale = max(
        abs(measurements.left_sum_rf1),
        abs(measurements.right_sum_rf1),
        1.0,
    )
    equilibrium_passed = (
        abs(measurements.left_sum_rf1 + measurements.right_sum_rf1) / reaction_scale
        <= expected.equilibrium_relative_tolerance
    )
    stress_concentration_passed = (
        measurements.far_field_mean_s11 > 0.0
        and measurements.hole_boundary_max_s11
        >= measurements.far_field_mean_s11 * expected.stress_concentration_ratio
    )
    results_json_valid, results_match_oracle = _check_agent_results(
        agent_results,
        measurements,
        expected.result_relative_tolerance,
    )

    new_requirement_passed = geometry_passed and hole_passed and mesh_passed
    regression_passed = (
        source_run_verified
        and material_passed
        and analysis_passed
        and boundary_conditions_passed
        and output_fields_passed
        and equilibrium_passed
    )
    findings: list[str] = []
    checks = (
        (
            job_completed,
            "Abaqus status evidence does not show plate_with_hole completed",
        ),
        (artifacts_fresh, "one or more change artifacts are missing or stale"),
        (build_script_changed, "build_model.py was not changed from the verified seed"),
        (extractor_preserved, "extract_results.py changed from the verified seed"),
        (source_run_verified, "the source CodingRun was not independently verified"),
        (geometry_passed, "outer 100 mm x 20 mm plate envelope was not preserved"),
        (hole_passed, "ODB mesh does not prove the requested centered radius-3 hole"),
        (mesh_passed, "mesh is not an all-CPS4R bounded-size mesh"),
        (material_passed, "material or 1 mm plane-stress thickness changed"),
        (
            analysis_passed,
            "single Static, General NLGEOM=OFF contract changed or a load was added",
        ),
        (boundary_conditions_passed, "prescribed displacement constraints changed"),
        (output_fields_passed, "U, S and RF are not present in both INP and ODB"),
        (equilibrium_passed, "left and right reactions are not in equilibrium"),
        (stress_concentration_passed, "ODB does not show a hole stress concentration"),
        (results_json_valid, "results.json is missing required numeric values"),
        (results_match_oracle, "results.json does not match the host ODB oracle"),
    )
    findings.extend(message for passed, message in checks if not passed)
    passed = all(passed for passed, _ in checks)
    return PlateHoleChangeVerification(
        passed=passed,
        job_completed=job_completed,
        artifacts_fresh=artifacts_fresh,
        source_run_verified=source_run_verified,
        baseline_preserved=regression_passed,
        build_script_changed=build_script_changed,
        extractor_preserved=extractor_preserved,
        new_requirement_passed=new_requirement_passed,
        regression_passed=regression_passed,
        geometry_passed=geometry_passed,
        hole_passed=hole_passed,
        mesh_passed=mesh_passed,
        material_passed=material_passed,
        analysis_passed=analysis_passed,
        boundary_conditions_passed=boundary_conditions_passed,
        output_fields_passed=output_fields_passed,
        equilibrium_passed=equilibrium_passed,
        stress_concentration_passed=stress_concentration_passed,
        results_json_valid=results_json_valid,
        results_match_oracle=results_match_oracle,
        right_u1=right_u1,
        mesh_area=mesh_area,
        hole_radius=hole_radius,
        findings=tuple(findings),
    )


def parse_plate_hole_input(path: Path) -> PlateHoleInputContract:
    """从 Agent 生成的 INP 中提取需要做回归验收的稳定合同。"""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    lines = [line.strip() for line in text.splitlines()]
    step_headers = [line for line in lines if line.casefold().startswith("*step")]
    static_step_count = sum(line.casefold().startswith("*static") for line in lines)
    element_types = sorted(
        {
            match.group(1).strip().upper()
            for line in lines
            if line.casefold().startswith("*element")
            for match in [re.search(r"\btype\s*=\s*([^,\s]+)", line, re.I)]
            if match is not None
        }
    )
    material_name = _keyword_parameter(lines, "*material", "name")
    section_material = _keyword_parameter(lines, "*solid section", "material")
    elastic = _numeric_data_after(lines, "*elastic", expected=2)
    thickness = _numeric_data_after(lines, "*solid section", expected=1)
    field_outputs = _field_output_variables(lines)
    return PlateHoleInputContract(
        elastic_modulus=elastic[0] if elastic is not None else None,
        poisson_ratio=elastic[1] if elastic is not None else None,
        thickness=thickness[0] if thickness is not None else None,
        material_name=material_name,
        section_material_name=section_material,
        element_types=tuple(element_types),
        step_count=len(step_headers),
        static_step_count=static_step_count,
        nlgeom_off=bool(step_headers)
        and all(
            "nlgeom=no" in header.replace(" ", "").casefold() for header in step_headers
        ),
        field_output_variables=tuple(sorted(field_outputs)),
        history_output_variables=tuple(sorted(_history_output_variables(lines))),
        boundary_records=_boundary_records(lines),
        mechanical_load_keywords=_mechanical_load_keywords(lines),
    )


def _keyword_parameter(lines: list[str], keyword: str, parameter: str) -> str | None:
    for line in lines:
        if not line.casefold().startswith(keyword.casefold()):
            continue
        match = re.search(rf"\b{re.escape(parameter)}\s*=\s*([^,]+)", line, re.I)
        if match is not None:
            return match.group(1).strip()
    return None


def _numeric_data_after(
    lines: list[str],
    keyword: str,
    *,
    expected: int,
) -> tuple[float, ...] | None:
    for index, line in enumerate(lines):
        if not line.casefold().startswith(keyword.casefold()):
            continue
        for candidate in lines[index + 1 :]:
            if not candidate or candidate.startswith("**"):
                continue
            if candidate.startswith("*"):
                break
            try:
                values = tuple(
                    float(value.strip())
                    for value in candidate.split(",")
                    if value.strip()
                )
            except ValueError:
                return None
            return values if len(values) >= expected else None
    return None


def _field_output_variables(lines: list[str]) -> set[str]:
    return _scoped_output_variables(lines, scope="field")


def _history_output_variables(lines: list[str]) -> set[str]:
    return _scoped_output_variables(lines, scope="history")


def _scoped_output_variables(
    lines: list[str],
    *,
    scope: Literal["field", "history"],
) -> set[str]:
    variables: set[str] = set()
    current_scope: Literal["field", "history"] | None = None
    for index, line in enumerate(lines):
        folded = line.casefold()
        if folded.startswith("*output"):
            if "field" in folded:
                current_scope = "field"
            elif "history" in folded:
                current_scope = "history"
            else:
                current_scope = None
            continue
        if (
            not (
                folded.startswith("*node output")
                or folded.startswith("*element output")
            )
            or current_scope != scope
        ):
            continue
        for candidate in lines[index + 1 :]:
            if not candidate or candidate.startswith("**"):
                continue
            if candidate.startswith("*"):
                break
            variables.update(
                value.strip().upper() for value in candidate.split(",") if value.strip()
            )
    return variables


def _boundary_records(lines: list[str]) -> tuple[tuple[int, int, float], ...]:
    records: list[tuple[int, int, float]] = []
    for index, line in enumerate(lines):
        if not line.casefold().startswith("*boundary"):
            continue
        for candidate in lines[index + 1 :]:
            if not candidate or candidate.startswith("**"):
                continue
            if candidate.startswith("*"):
                break
            values = [value.strip() for value in candidate.split(",")]
            if len(values) < 3:
                continue
            try:
                first_dof = int(values[1])
                last_dof = int(values[2])
                magnitude = float(values[3]) if len(values) > 3 and values[3] else 0.0
            except ValueError:
                continue
            records.append((first_dof, last_dof, magnitude))
    return tuple(records)


def _mechanical_load_keywords(lines: list[str]) -> tuple[str, ...]:
    forbidden = (
        "*cload",
        "*dload",
        "*dsload",
        "*body force",
        "*gravity",
        "*centrifugal",
    )
    return tuple(
        line.split(",", maxsplit=1)[0]
        for line in lines
        if any(line.casefold().startswith(keyword) for keyword in forbidden)
    )


def _check_agent_results(
    agent_results: Mapping[str, float] | None,
    measurements: PlateHoleChangeMeasurements,
    tolerance: float,
) -> tuple[bool, bool]:
    expected_values = {
        "right_mean_u1": measurements.right_mean_u1,
        "mean_s11": measurements.mean_s11,
        "left_sum_rf1": measurements.left_sum_rf1,
    }
    if agent_results is None:
        return False, False
    try:
        values = {key: float(agent_results[key]) for key in expected_values}
    except (KeyError, TypeError, ValueError):
        return False, False
    return True, all(
        _relative_check(expected, values[key], tolerance).passed
        for key, expected in expected_values.items()
    )


def _relative_check(expected: float, actual: float, tolerance: float) -> NumericCheck:
    denominator = max(abs(expected), 1.0e-12)
    error = abs(actual - expected) / denominator
    return NumericCheck(
        passed=math.isfinite(actual) and error <= tolerance,
        expected=expected,
        actual=actual,
        error=error,
        tolerance=tolerance,
    )


def _absolute_check(expected: float, actual: float, tolerance: float) -> NumericCheck:
    error = abs(actual - expected)
    return NumericCheck(
        passed=math.isfinite(actual) and error <= tolerance,
        expected=expected,
        actual=actual,
        error=error,
        tolerance=tolerance,
    )
