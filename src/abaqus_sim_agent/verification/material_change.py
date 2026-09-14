from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MaterialElasticContract(_FrozenModel):
    name: str = Field(min_length=1)
    elastic_modulus: float
    poisson_ratio: float


class SolidSectionContract(_FrozenModel):
    material_name: str = Field(min_length=1)
    thickness: float


class TwoMaterialPlateInputContract(_FrozenModel):
    materials: tuple[MaterialElasticContract, ...]
    solid_sections: tuple[SolidSectionContract, ...]
    element_types: tuple[str, ...]
    step_count: int = Field(ge=0)
    static_step_count: int = Field(ge=0)
    nlgeom_off: bool
    field_output_variables: tuple[str, ...]
    boundary_records: tuple[tuple[int, int, float], ...]
    forbidden_keywords: tuple[str, ...]


class TwoMaterialPlateOracleSpec(_FrozenModel):
    width: float = 100.0
    height: float = 20.0
    interface_x: float = 50.0
    thickness: float = 1.0
    left_elastic_modulus: float = 210_000.0
    right_elastic_modulus: float = 70_000.0
    poisson_ratio: float = 0.3
    element_type: str = "CPS4R"
    element_count: int = 80
    node_count: int = 105
    x_layer_count: int = 21
    y_layer_count: int = 5
    interface_node_count: int = 5
    right_mean_u1: float = 0.10000000149011612
    interface_mean_u1: float = 0.02526366673409939
    mean_s11: float = 105.09175720214844
    left_mean_s11: float = 105.09175720214844
    right_mean_s11: float = 105.09175720214844
    reaction_magnitude: float = 2101.8351440429688
    relative_tolerance: float = Field(default=0.02, gt=0, lt=1)
    geometry_tolerance: float = Field(default=1.0e-6, gt=0)


class TwoMaterialPlateMeasurements(_FrozenModel):
    odb_path: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    analysis_step_count: int = Field(ge=0)
    step_procedure: str = Field(min_length=1)
    step_nlgeom: bool
    instance_name: str = Field(min_length=1)
    node_count: int = Field(gt=0)
    element_type_counts: dict[str, int]
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    max_abs_z: float = Field(ge=0)
    x_layer_count: int = Field(gt=0)
    y_layer_count: int = Field(gt=0)
    interface_node_count: int = Field(gt=0)
    interface_is_shared: bool
    connectivity_is_single_component: bool
    left_element_count: int = Field(gt=0)
    right_element_count: int = Field(gt=0)
    assignments_cover_each_element_once: bool
    left_properties: tuple[tuple[float, float, float], ...]
    right_properties: tuple[tuple[float, float, float], ...]
    field_outputs: tuple[str, ...]
    u_node_coverage: bool
    s_element_coverage: bool
    right_mean_u1: float
    interface_mean_u1: float
    max_abs_left_u1: float = Field(ge=0)
    pinned_u2: float
    left_sum_rf1: float
    right_sum_rf1: float
    mean_s11: float
    left_mean_s11: float
    right_mean_s11: float
    max_abs_s22: float = Field(ge=0)
    max_abs_s12: float = Field(ge=0)


class NumericCheck(_FrozenModel):
    passed: bool
    expected: float
    actual: float
    relative_error: float = Field(ge=0)
    tolerance: float = Field(ge=0)


class TwoMaterialPlateVerification(_FrozenModel):
    passed: bool
    job_completed: bool
    artifacts_fresh: bool
    source_run_verified: bool
    build_script_changed: bool
    extract_script_changed: bool
    new_requirement_passed: bool
    regression_passed: bool
    geometry_passed: bool
    mesh_and_interface_passed: bool
    material_assignment_passed: bool
    analysis_passed: bool
    boundary_conditions_passed: bool
    output_fields_passed: bool
    equilibrium_passed: bool
    results_json_valid: bool
    results_match_oracle: bool
    right_u1: NumericCheck
    interface_u1: NumericCheck
    mean_s11: NumericCheck
    left_s11: NumericCheck
    right_s11: NumericCheck
    reaction: NumericCheck
    findings: tuple[str, ...] = ()


def verify_two_material_plate_change(
    measurements: TwoMaterialPlateMeasurements,
    input_contract: TwoMaterialPlateInputContract,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    source_run_verified: bool,
    agent_results: Mapping[str, float] | None,
    spec: TwoMaterialPlateOracleSpec | None = None,
) -> TwoMaterialPlateVerification:
    expected = spec or TwoMaterialPlateOracleSpec()
    required_fresh = {
        "build_model.py",
        "extract_results.py",
        "plate_tension.cae",
        "plate_tension.inp",
        "plate_tension.odb",
        "results.json",
    }
    artifacts_fresh = required_fresh.issubset(artifact_freshness) and all(
        artifact_freshness[path] for path in required_fresh
    )
    build_script_changed = artifact_freshness.get("build_model.py", False)
    extract_script_changed = artifact_freshness.get("extract_results.py", False)
    geometry_passed = (
        abs(measurements.x_min) <= expected.geometry_tolerance
        and abs(measurements.x_max - expected.width) <= expected.geometry_tolerance
        and abs(measurements.y_min) <= expected.geometry_tolerance
        and abs(measurements.y_max - expected.height) <= expected.geometry_tolerance
        and measurements.max_abs_z <= expected.geometry_tolerance
    )
    mesh_and_interface_passed = (
        measurements.node_count == expected.node_count
        and measurements.element_type_counts
        == {expected.element_type: expected.element_count}
        and measurements.x_layer_count == expected.x_layer_count
        and measurements.y_layer_count == expected.y_layer_count
        and measurements.interface_node_count == expected.interface_node_count
        and measurements.interface_is_shared
        and measurements.connectivity_is_single_component
        and measurements.left_element_count == expected.element_count // 2
        and measurements.right_element_count == expected.element_count // 2
        and set(input_contract.element_types) == {expected.element_type}
    )
    expected_left = (
        expected.left_elastic_modulus,
        expected.poisson_ratio,
        expected.thickness,
    )
    expected_right = (
        expected.right_elastic_modulus,
        expected.poisson_ratio,
        expected.thickness,
    )
    inp_properties = sorted(
        (
            material.elastic_modulus,
            material.poisson_ratio,
            section.thickness,
        )
        for material in input_contract.materials
        for section in input_contract.solid_sections
        if section.material_name == material.name
    )
    material_assignment_passed = (
        measurements.assignments_cover_each_element_once
        and measurements.left_properties == (expected_left,)
        and measurements.right_properties == (expected_right,)
        and inp_properties == sorted((expected_left, expected_right))
        and len(input_contract.materials) == 2
        and len(input_contract.solid_sections) == 2
    )
    required_outputs = {"U", "S", "RF"}
    analysis_passed = (
        measurements.analysis_step_count == 1
        and measurements.step_procedure.upper() == "*STATIC"
        and not measurements.step_nlgeom
        and input_contract.step_count == 1
        and input_contract.static_step_count == 1
        and input_contract.nlgeom_off
        and not input_contract.forbidden_keywords
    )
    boundary_conditions_passed = (
        measurements.max_abs_left_u1 <= 1.0e-7
        and abs(measurements.pinned_u2) <= 1.0e-7
        and sorted(input_contract.boundary_records)
        == sorted(((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.1)))
    )
    output_fields_passed = (
        required_outputs.issubset(measurements.field_outputs)
        and required_outputs.issubset(input_contract.field_output_variables)
        and measurements.u_node_coverage
        and measurements.s_element_coverage
    )
    reaction_scale = max(
        abs(measurements.left_sum_rf1),
        abs(measurements.right_sum_rf1),
        1.0,
    )
    equilibrium_passed = (
        abs(measurements.left_sum_rf1 + measurements.right_sum_rf1) / reaction_scale
        <= expected.relative_tolerance
    )
    right_u1 = _numeric_check(
        expected.right_mean_u1,
        measurements.right_mean_u1,
        expected.relative_tolerance,
    )
    interface_u1 = _numeric_check(
        expected.interface_mean_u1,
        measurements.interface_mean_u1,
        expected.relative_tolerance,
    )
    mean_s11 = _numeric_check(
        expected.mean_s11,
        measurements.mean_s11,
        expected.relative_tolerance,
    )
    left_s11 = _numeric_check(
        expected.left_mean_s11,
        measurements.left_mean_s11,
        expected.relative_tolerance,
    )
    right_s11 = _numeric_check(
        expected.right_mean_s11,
        measurements.right_mean_s11,
        expected.relative_tolerance,
    )
    reaction = _numeric_check(
        expected.reaction_magnitude,
        abs(measurements.left_sum_rf1),
        expected.relative_tolerance,
    )
    results_json_valid, results_match_oracle = _check_agent_results(
        agent_results,
        measurements,
        expected.relative_tolerance,
    )
    new_requirement_passed = (
        mesh_and_interface_passed
        and material_assignment_passed
        and interface_u1.passed
        and left_s11.passed
        and right_s11.passed
    )
    regression_passed = (
        source_run_verified
        and geometry_passed
        and analysis_passed
        and boundary_conditions_passed
        and output_fields_passed
        and equilibrium_passed
        and right_u1.passed
        and mean_s11.passed
        and reaction.passed
    )
    checks = (
        (job_completed, "plate_tension Solver completion evidence is missing"),
        (artifacts_fresh, "one or more required change artifacts are missing or stale"),
        (build_script_changed, "build_model.py was not changed from the verified seed"),
        (
            extract_script_changed,
            "extract_results.py was not changed for phase results",
        ),
        (source_run_verified, "the source CodingRun was not independently verified"),
        (geometry_passed, "the original 100 mm x 20 mm planar geometry changed"),
        (
            mesh_and_interface_passed,
            "the 20x4 conforming interface mesh contract failed",
        ),
        (
            material_assignment_passed,
            "active left/right material assignment is incorrect",
        ),
        (analysis_passed, "the Static/NLGEOM/no-extra-coupling contract changed"),
        (boundary_conditions_passed, "the original displacement constraints changed"),
        (output_fields_passed, "U, S and RF output coverage is incomplete"),
        (equilibrium_passed, "left and right reactions are not in equilibrium"),
        (right_u1.passed, "right-edge U1 does not match the prescribed displacement"),
        (interface_u1.passed, "interface U1 does not match the fixed-grid reference"),
        (mean_s11.passed, "global mean S11 does not match the reference"),
        (left_s11.passed, "left-region mean S11 does not match the reference"),
        (right_s11.passed, "right-region mean S11 does not match the reference"),
        (reaction.passed, "boundary reaction does not match the reference"),
        (results_json_valid, "results.json is missing required numeric phase results"),
        (results_match_oracle, "results.json does not match the private ODB oracle"),
    )
    findings = tuple(message for passed, message in checks if not passed)
    return TwoMaterialPlateVerification(
        passed=all(passed for passed, _ in checks),
        job_completed=job_completed,
        artifacts_fresh=artifacts_fresh,
        source_run_verified=source_run_verified,
        build_script_changed=build_script_changed,
        extract_script_changed=extract_script_changed,
        new_requirement_passed=new_requirement_passed,
        regression_passed=regression_passed,
        geometry_passed=geometry_passed,
        mesh_and_interface_passed=mesh_and_interface_passed,
        material_assignment_passed=material_assignment_passed,
        analysis_passed=analysis_passed,
        boundary_conditions_passed=boundary_conditions_passed,
        output_fields_passed=output_fields_passed,
        equilibrium_passed=equilibrium_passed,
        results_json_valid=results_json_valid,
        results_match_oracle=results_match_oracle,
        right_u1=right_u1,
        interface_u1=interface_u1,
        mean_s11=mean_s11,
        left_s11=left_s11,
        right_s11=right_s11,
        reaction=reaction,
        findings=findings,
    )


def parse_two_material_plate_input(path: Path) -> TwoMaterialPlateInputContract:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    lines = [line.strip() for line in text.splitlines()]
    materials = _materials(lines)
    sections = _solid_sections(lines)
    step_headers = [line for line in lines if line.casefold().startswith("*step")]
    element_types = sorted(
        {
            match.group(1).strip().upper()
            for line in lines
            if line.casefold().startswith("*element")
            for match in [re.search(r"\btype\s*=\s*([^,\s]+)", line, re.I)]
            if match is not None
        }
    )
    return TwoMaterialPlateInputContract(
        materials=materials,
        solid_sections=sections,
        element_types=tuple(element_types),
        step_count=len(step_headers),
        static_step_count=sum(line.casefold().startswith("*static") for line in lines),
        nlgeom_off=bool(step_headers)
        and all(
            "nlgeom=no" in line.replace(" ", "").casefold() for line in step_headers
        ),
        field_output_variables=tuple(sorted(_field_output_variables(lines))),
        boundary_records=_boundary_records(lines),
        forbidden_keywords=_forbidden_keywords(lines),
    )


def _materials(lines: list[str]) -> tuple[MaterialElasticContract, ...]:
    materials: list[MaterialElasticContract] = []
    current_name: str | None = None
    for index, line in enumerate(lines):
        if line.casefold().startswith("*material"):
            match = re.search(r"\bname\s*=\s*([^,]+)", line, re.I)
            current_name = match.group(1).strip() if match is not None else None
        elif line.casefold().startswith("*elastic") and current_name is not None:
            values = _next_numeric_row(lines, index)
            if values is not None and len(values) >= 2:
                materials.append(
                    MaterialElasticContract(
                        name=current_name,
                        elastic_modulus=values[0],
                        poisson_ratio=values[1],
                    )
                )
    return tuple(materials)


def _solid_sections(lines: list[str]) -> tuple[SolidSectionContract, ...]:
    sections: list[SolidSectionContract] = []
    for index, line in enumerate(lines):
        if not line.casefold().startswith("*solid section"):
            continue
        match = re.search(r"\bmaterial\s*=\s*([^,]+)", line, re.I)
        values = _next_numeric_row(lines, index)
        if match is not None and values:
            sections.append(
                SolidSectionContract(
                    material_name=match.group(1).strip(),
                    thickness=values[0],
                )
            )
    return tuple(sections)


def _next_numeric_row(lines: list[str], index: int) -> tuple[float, ...] | None:
    for candidate in lines[index + 1 :]:
        if not candidate or candidate.startswith("**"):
            continue
        if candidate.startswith("*"):
            return None
        try:
            return tuple(
                float(value.strip()) for value in candidate.split(",") if value.strip()
            )
        except ValueError:
            return None
    return None


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
                records.append(
                    (
                        int(values[1]),
                        int(values[2]),
                        float(values[3]) if len(values) > 3 and values[3] else 0.0,
                    )
                )
            except ValueError:
                continue
    return tuple(records)


def _forbidden_keywords(lines: list[str]) -> tuple[str, ...]:
    forbidden = (
        "*body force",
        "*cload",
        "*contact",
        "*dload",
        "*dsload",
        "*equation",
        "*tie",
    )
    return tuple(
        line.split(",", maxsplit=1)[0]
        for line in lines
        if any(line.casefold().startswith(keyword) for keyword in forbidden)
    )


def _check_agent_results(
    agent_results: Mapping[str, float] | None,
    measurements: TwoMaterialPlateMeasurements,
    tolerance: float,
) -> tuple[bool, bool]:
    expected_values = {
        "right_mean_u1": measurements.right_mean_u1,
        "interface_mean_u1": measurements.interface_mean_u1,
        "mean_s11": measurements.mean_s11,
        "left_mean_s11": measurements.left_mean_s11,
        "right_mean_s11": measurements.right_mean_s11,
        "left_sum_rf1": measurements.left_sum_rf1,
    }
    if agent_results is None:
        return False, False
    try:
        values = {key: float(agent_results[key]) for key in expected_values}
    except (KeyError, TypeError, ValueError):
        return False, False
    return True, all(
        _numeric_check(expected, values[key], tolerance).passed
        for key, expected in expected_values.items()
    )


def _numeric_check(expected: float, actual: float, tolerance: float) -> NumericCheck:
    denominator = max(abs(expected), 1.0e-12)
    relative_error = abs(actual - expected) / denominator
    return NumericCheck(
        passed=math.isfinite(actual) and relative_error <= tolerance,
        expected=expected,
        actual=actual,
        relative_error=relative_error,
        tolerance=tolerance,
    )
