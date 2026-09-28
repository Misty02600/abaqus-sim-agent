from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.verification.beam_reference import (
    BeamTensionMeasurements,
    BeamTensionOracleSpec,
)
from abaqus_sim_agent.verification.numeric import NumericCheck

_FORBIDDEN_LOAD_KEYWORDS = (
    "*body force",
    "*cflux",
    "*connector load",
    "*dflux",
    "*dload",
    "*dsflux",
    "*dsload",
    "*film",
    "*foundation",
    "*radiate",
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ConcentratedLoadRecord(_FrozenModel):
    region: str = Field(min_length=1)
    dof: int = Field(ge=1)
    magnitude: float


class BeamBendingInputContract(_FrozenModel):
    step_count: int = Field(ge=0)
    static_step_count: int = Field(ge=0)
    nlgeom_off: bool
    boundary_records: tuple[tuple[str, ...], ...]
    concentrated_loads: tuple[ConcentratedLoadRecord, ...]
    field_output_variables: tuple[str, ...]
    forbidden_keywords: tuple[str, ...]


class BeamBendingChangeOracleSpec(BeamTensionOracleSpec):
    tip_load_y: float = 1_000.0
    right_mean_u2: float = 1.9190475940704346
    left_rf2: float = -1_000.0
    left_rm3: float = -100_000.0
    max_section_shear: float = 1_000.0
    max_section_moment: float = 97_500.0
    sm_value_count: int = Field(default=20, gt=0)


class BeamBendingChangeVerification(_FrozenModel):
    passed: bool
    source_run_verified: bool
    build_script_changed: bool
    extractor_changed: bool
    execution_provenance_passed: bool
    artifacts_fresh: bool
    input_contract_passed: bool
    geometry_and_mesh_passed: bool
    material_and_section_passed: bool
    analysis_passed: bool
    orientation_passed: bool
    output_coverage_passed: bool
    new_requirement_passed: bool
    regression_passed: bool
    results_json_valid: bool
    results_match_oracle: bool
    right_u2: NumericCheck
    left_rf2: NumericCheck
    left_rm3: NumericCheck
    right_rf2: NumericCheck
    right_rm3: NumericCheck
    section_shear: NumericCheck
    section_moment: NumericCheck
    axial_u1: NumericCheck
    axial_rf1: NumericCheck
    axial_sf1: NumericCheck
    findings: tuple[str, ...] = ()


def parse_beam_bending_input(path: Path) -> BeamBendingInputContract:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    lines = [line.strip() for line in text.splitlines()]
    step_headers = [line for line in lines if line.casefold().startswith("*step")]
    static_step_count = sum(line.casefold().startswith("*static") for line in lines)
    boundary_records: list[tuple[str, ...]] = []
    concentrated_loads: list[ConcentratedLoadRecord] = []
    keyword = ""
    for line in lines:
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            keyword = line.split(",", maxsplit=1)[0].casefold()
            continue
        values = tuple(value.strip() for value in line.split(",") if value.strip())
        if keyword == "*boundary":
            boundary_records.append(values)
        elif keyword == "*cload" and len(values) >= 3:
            try:
                concentrated_loads.append(
                    ConcentratedLoadRecord(
                        region=values[0],
                        dof=int(values[1]),
                        magnitude=float(values[2]),
                    )
                )
            except ValueError:
                continue
    forbidden_keywords = tuple(
        line.split(",", maxsplit=1)[0]
        for line in lines
        if any(line.casefold().startswith(item) for item in _FORBIDDEN_LOAD_KEYWORDS)
    )
    return BeamBendingInputContract(
        step_count=len(step_headers),
        static_step_count=static_step_count,
        nlgeom_off=bool(step_headers)
        and all(
            "nlgeom=no" in line.replace(" ", "").casefold() for line in step_headers
        ),
        boundary_records=tuple(boundary_records),
        concentrated_loads=tuple(concentrated_loads),
        field_output_variables=tuple(sorted(_field_output_variables(lines))),
        forbidden_keywords=forbidden_keywords,
    )


def verify_beam_bending_change(
    measurements: BeamTensionMeasurements,
    input_contract: BeamBendingInputContract,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    source_run_verified: bool,
    build_script_changed: bool,
    extractor_changed: bool,
    execution_provenance_passed: bool,
    agent_results: Mapping[str, float] | None,
    spec: BeamBendingChangeOracleSpec | None = None,
) -> BeamBendingChangeVerification:
    expected = spec or BeamBendingChangeOracleSpec()
    required = {
        "build_model.py",
        "extract_results.py",
        "beam_tension.cae",
        "beam_tension.inp",
        "beam_tension.odb",
        "results.json",
    }
    artifacts_fresh = required.issubset(artifact_freshness) and all(
        artifact_freshness[path] for path in required
    )
    load = (
        input_contract.concentrated_loads[0]
        if len(input_contract.concentrated_loads) == 1
        else None
    )
    input_contract_passed = (
        input_contract.step_count == 1
        and input_contract.static_step_count == 1
        and input_contract.nlgeom_off
        and len(input_contract.boundary_records) == 1
        and len(input_contract.boundary_records[0]) == 2
        and input_contract.boundary_records[0][1].upper() == "ENCASTRE"
        and load is not None
        and load.dof == 2
        and abs(load.magnitude - expected.tip_load_y) <= 1.0e-9
        and set(input_contract.field_output_variables) == {"RF", "SF", "U"}
        and not input_contract.forbidden_keywords
    )
    geometry_and_mesh_passed = (
        measurements.element_type_counts
        == {expected.element_type: expected.element_count}
        and measurements.node_count == expected.node_count
        and measurements.connectivity_is_x_chain
        and measurements.coordinate_layer_counts == expected.coordinate_layer_counts
        and _coordinates_match(
            expected.x_coordinates,
            measurements.x_coordinates,
            expected.geometry_relative_tolerance,
        )
        and abs(measurements.x_span - expected.x_span)
        <= expected.x_span * expected.geometry_relative_tolerance
        and measurements.max_abs_transverse_coordinate
        <= expected.zero_coordinate_tolerance
    )
    material_and_section_passed = (
        abs(measurements.elastic_e - expected.elastic_e)
        <= expected.elastic_e * expected.geometry_relative_tolerance
        and abs(measurements.elastic_nu - expected.elastic_nu)
        <= expected.geometry_relative_tolerance
        and abs(measurements.profile_a - expected.profile_a)
        <= expected.geometry_relative_tolerance
        and abs(measurements.profile_b - expected.profile_b)
        <= expected.geometry_relative_tolerance
        and measurements.section_material_name == measurements.material_name
        and measurements.section_profile_name == measurements.profile_name
        and measurements.section_assignment_name == measurements.section_name
        and measurements.section_integration in {"BEFORE_ANALYSIS", "DURING_ANALYSIS"}
        and measurements.section_assignment_covers_all_elements
    )
    analysis_passed = (
        measurements.analysis_step_count == expected.analysis_step_count
        and measurements.step_procedure == "*STATIC"
        and not measurements.step_nlgeom
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
    output_coverage_passed = (
        measurements.sf1_value_count == expected.sf1_value_count
        and measurements.sf1_element_coverage
        and measurements.sm_value_count == expected.sm_value_count
        and measurements.sm_element_coverage
    )
    right_u2 = _numeric_check(
        expected.right_mean_u2,
        measurements.right_mean_u2,
        expected.relative_tolerance,
    )
    left_rf2 = _numeric_check(
        expected.left_rf2,
        measurements.left_sum_rf2,
        expected.relative_tolerance,
    )
    left_rm3 = _numeric_check(
        expected.left_rm3,
        measurements.left_sum_rm3,
        expected.relative_tolerance,
    )
    right_rf2 = _zero_check(
        measurements.right_sum_rf2,
        expected.zero_force_tolerance,
    )
    right_rm3 = _zero_check(
        measurements.right_sum_rm3,
        expected.zero_force_tolerance,
    )
    section_shear = _numeric_check(
        expected.max_section_shear,
        measurements.max_section_shear,
        expected.relative_tolerance,
    )
    section_moment = _numeric_check(
        expected.max_section_moment,
        measurements.max_section_moment,
        expected.relative_tolerance,
    )
    axial_u1 = _zero_check(
        measurements.right_mean_u1,
        expected.zero_displacement_tolerance,
    )
    axial_rf1 = _zero_check(
        measurements.left_sum_rf1,
        expected.zero_force_tolerance,
    )
    axial_sf1 = _zero_check(
        measurements.mean_sf1,
        expected.zero_force_tolerance,
    )
    results_json_valid, results_match_oracle = _check_agent_results(
        agent_results,
        measurements,
        expected.relative_tolerance,
    )
    new_requirement_passed = all(
        (
            input_contract_passed,
            right_u2.passed,
            left_rf2.passed,
            left_rm3.passed,
            right_rf2.passed,
            right_rm3.passed,
            section_shear.passed,
            section_moment.passed,
        )
    )
    regression_passed = all(
        (
            source_run_verified,
            build_script_changed,
            extractor_changed,
            execution_provenance_passed,
            artifacts_fresh,
            geometry_and_mesh_passed,
            material_and_section_passed,
            analysis_passed,
            orientation_passed,
            output_coverage_passed,
            axial_u1.passed,
            axial_rf1.passed,
            axial_sf1.passed,
            results_json_valid,
            results_match_oracle,
            job_completed,
        )
    )
    findings: list[str] = []
    for passed, message in (
        (source_run_verified, "source B31 CodingRun was not independently verified"),
        (build_script_changed, "build_model.py was not changed from the seed"),
        (extractor_changed, "extract_results.py was not changed for bending results"),
        (execution_provenance_passed, "three-stage execution provenance failed"),
        (artifacts_fresh, "one or more bending artifacts are missing or stale"),
        (job_completed, "beam_tension Solver completion evidence is missing"),
        (
            input_contract_passed,
            "INP must contain one +Y 1000 N tip CLOAD, one Encastre BC, "
            "and exactly U/RF/SF field output",
        ),
        (geometry_and_mesh_passed, "wire geometry or 20-element B31 mesh changed"),
        (material_and_section_passed, "10 x 10 section or material linkage changed"),
        (analysis_passed, "Static/NLGEOM analysis contract changed"),
        (orientation_passed, "beam section orientation is invalid or incomplete"),
        (output_coverage_passed, "SF/SM output does not cover all 20 elements"),
        (results_json_valid, "results.json is missing bending numeric fields"),
        (
            results_match_oracle,
            "results.json does not match the private bending oracle",
        ),
    ):
        if not passed:
            findings.append(message)
    for name, check in (
        ("right U2", right_u2),
        ("left RF2", left_rf2),
        ("left RM3", left_rm3),
        ("right RF2", right_rf2),
        ("right RM3", right_rm3),
        ("section shear", section_shear),
        ("section moment", section_moment),
        ("axial U1", axial_u1),
        ("axial RF1", axial_rf1),
        ("axial SF1", axial_sf1),
    ):
        if not check.passed:
            findings.append(
                f"{name} error {check.relative_error:.6g} exceeds {check.tolerance:.6g}"
            )
    passed = new_requirement_passed and regression_passed
    return BeamBendingChangeVerification(
        passed=passed,
        source_run_verified=source_run_verified,
        build_script_changed=build_script_changed,
        extractor_changed=extractor_changed,
        execution_provenance_passed=execution_provenance_passed,
        artifacts_fresh=artifacts_fresh,
        input_contract_passed=input_contract_passed,
        geometry_and_mesh_passed=geometry_and_mesh_passed,
        material_and_section_passed=material_and_section_passed,
        analysis_passed=analysis_passed,
        orientation_passed=orientation_passed,
        output_coverage_passed=output_coverage_passed,
        new_requirement_passed=new_requirement_passed,
        regression_passed=regression_passed,
        results_json_valid=results_json_valid,
        results_match_oracle=results_match_oracle,
        right_u2=right_u2,
        left_rf2=left_rf2,
        left_rm3=left_rm3,
        right_rf2=right_rf2,
        right_rm3=right_rm3,
        section_shear=section_shear,
        section_moment=section_moment,
        axial_u1=axial_u1,
        axial_rf1=axial_rf1,
        axial_sf1=axial_sf1,
        findings=tuple(dict.fromkeys(findings)),
    )


def _field_output_variables(lines: list[str]) -> set[str]:
    variables: set[str] = set()
    for index, line in enumerate(lines):
        folded = line.casefold()
        if not (
            folded.startswith("*node output") or folded.startswith("*element output")
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


def _coordinates_match(
    expected: tuple[float, ...],
    actual: tuple[float, ...],
    tolerance: float,
) -> bool:
    return len(expected) == len(actual) and all(
        abs(expected_value - actual_value) <= max(abs(expected_value), 1.0) * tolerance
        for expected_value, actual_value in zip(expected, actual, strict=True)
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


def _check_agent_results(
    agent_results: Mapping[str, float] | None,
    measurements: BeamTensionMeasurements,
    tolerance: float,
) -> tuple[bool, bool]:
    if agent_results is None:
        return False, False
    expected_values = {
        "right_mean_u2": measurements.right_mean_u2,
        "left_sum_rf2": measurements.left_sum_rf2,
        "left_sum_rm3": measurements.left_sum_rm3,
        "max_section_moment": measurements.max_section_moment,
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
