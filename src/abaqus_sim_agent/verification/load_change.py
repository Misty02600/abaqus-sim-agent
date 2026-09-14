from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BoundaryRecord(_FrozenModel):
    region: str = Field(min_length=1)
    first_dof: int = Field(ge=1)
    last_dof: int = Field(ge=1)
    magnitude: float


class TractionRecord(_FrozenModel):
    surface_name: str = Field(min_length=1)
    load_type: str = Field(min_length=1)
    magnitude: float
    direction: tuple[float, float, float]
    follower: bool


class LoadModeInputContract(_FrozenModel):
    elastic_modulus: float | None = None
    poisson_ratio: float | None = None
    thickness: float | None = None
    material_name: str | None = None
    section_material_name: str | None = None
    element_types: tuple[str, ...]
    step_count: int = Field(ge=0)
    static_step_count: int = Field(ge=0)
    nlgeom_off: bool
    field_output_variables: tuple[str, ...]
    boundary_records: tuple[BoundaryRecord, ...]
    traction_records: tuple[TractionRecord, ...]
    surface_names: tuple[str, ...]
    forbidden_keywords: tuple[str, ...]


class LoadModeOracleSpec(_FrozenModel):
    width: float = 100.0
    height: float = 20.0
    thickness: float = 1.0
    elastic_modulus: float = 210_000.0
    poisson_ratio: float = 0.3
    traction_magnitude: float = 210.0
    expected_force: float = 4_200.0
    right_mean_u1: float = 0.1
    mean_s11: float = 210.0
    element_type: str = "CPS4R"
    element_count: int = 80
    node_count: int = 105
    right_boundary_node_count: int = 5
    right_boundary_element_count: int = 4
    relative_tolerance: float = Field(default=0.02, gt=0, lt=1)
    geometry_tolerance: float = Field(default=1.0e-6, gt=0)


class LoadModeMeasurements(_FrozenModel):
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
    surface_name: str = Field(min_length=1)
    surface_is_full_right_edge: bool
    surface_unique_node_count: int = Field(gt=0)
    surface_element_count: int = Field(gt=0)
    elastic_modulus: float
    poisson_ratio: float
    section_thickness: float
    section_material_name: str = Field(min_length=1)
    material_name: str = Field(min_length=1)
    section_assignment_count: int = Field(gt=0)
    section_assignment_covers_all_elements: bool
    field_outputs: tuple[str, ...]
    right_mean_u1: float
    max_abs_left_u1: float = Field(ge=0)
    pinned_u2: float
    left_sum_rf1: float
    right_sum_rf1: float
    mean_s11: float


class NumericCheck(_FrozenModel):
    passed: bool
    expected: float
    actual: float
    relative_error: float = Field(ge=0)
    tolerance: float = Field(ge=0)


class TractionContractVerification(_FrozenModel):
    passed: bool
    record_count: int = Field(ge=0)
    record_count_passed: bool
    load_type_actual: str | None = None
    load_type_passed: bool
    magnitude_actual: float | None = None
    magnitude_passed: bool
    direction_actual: tuple[float, float, float] | None = None
    direction_normalized: tuple[float, float, float] | None = None
    direction_passed: bool
    follower_actual: bool | None = None
    follower_passed: bool
    surface_name_actual: str | None = None
    surface_declared: bool
    findings: tuple[str, ...] = ()


class LoadModeVerification(_FrozenModel):
    passed: bool
    job_completed: bool
    artifacts_fresh: bool
    source_run_verified: bool
    build_script_changed: bool
    extractor_preserved: bool
    new_requirement_passed: bool
    regression_passed: bool
    geometry_and_mesh_passed: bool
    material_passed: bool
    analysis_passed: bool
    displacement_mode_removed: bool
    traction_contract_passed: bool
    traction_contract: TractionContractVerification
    loaded_surface_passed: bool
    output_fields_passed: bool
    results_json_valid: bool
    results_match_oracle: bool
    right_u1: NumericCheck
    mean_s11: NumericCheck
    left_reaction: NumericCheck
    right_reaction: NumericCheck
    findings: tuple[str, ...] = ()


def verify_load_mode_change(
    measurements: LoadModeMeasurements,
    input_contract: LoadModeInputContract,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    source_run_verified: bool,
    agent_results: Mapping[str, float] | None,
    spec: LoadModeOracleSpec | None = None,
) -> LoadModeVerification:
    expected = spec or LoadModeOracleSpec()
    required = {
        "build_model.py",
        "extract_results.py",
        "plate_tension.cae",
        "plate_tension.inp",
        "plate_tension.odb",
        "results.json",
    }
    artifacts_fresh = required.issubset(artifact_freshness) and all(
        artifact_freshness[path] for path in required - {"extract_results.py"}
    )
    build_script_changed = artifact_freshness.get("build_model.py", False)
    extractor_preserved = artifact_freshness.get("extract_results.py") is False
    geometry_and_mesh_passed = (
        abs(measurements.x_min) <= expected.geometry_tolerance
        and abs(measurements.x_max - expected.width) <= expected.geometry_tolerance
        and abs(measurements.y_min) <= expected.geometry_tolerance
        and abs(measurements.y_max - expected.height) <= expected.geometry_tolerance
        and measurements.node_count == expected.node_count
        and measurements.element_type_counts
        == {expected.element_type: expected.element_count}
        and set(input_contract.element_types) == {expected.element_type}
    )
    material_passed = (
        input_contract.elastic_modulus is not None
        and input_contract.poisson_ratio is not None
        and input_contract.thickness is not None
        and _numeric_check(
            expected.elastic_modulus,
            input_contract.elastic_modulus,
            1.0e-9,
        ).passed
        and abs(input_contract.poisson_ratio - expected.poisson_ratio) <= 1.0e-9
        and abs(input_contract.thickness - expected.thickness) <= 1.0e-9
        and input_contract.material_name == input_contract.section_material_name
        and abs(measurements.elastic_modulus - expected.elastic_modulus) <= 1.0e-6
        and abs(measurements.poisson_ratio - expected.poisson_ratio) <= 1.0e-9
        and abs(measurements.section_thickness - expected.thickness) <= 1.0e-9
        and measurements.section_material_name == measurements.material_name
        and measurements.section_assignment_count == 1
        and measurements.section_assignment_covers_all_elements
    )
    analysis_passed = (
        measurements.analysis_step_count == 1
        and measurements.step_procedure.upper() == "*STATIC"
        and not measurements.step_nlgeom
        and input_contract.step_count == 1
        and input_contract.static_step_count == 1
        and input_contract.nlgeom_off
        and not input_contract.forbidden_keywords
    )
    displacement_mode_removed = (
        len(input_contract.boundary_records) == 2
        and all(
            abs(record.magnitude) <= 1.0e-12
            for record in input_contract.boundary_records
        )
        and sorted(
            (record.first_dof, record.last_dof, record.magnitude)
            for record in input_contract.boundary_records
        )
        == sorted(((1, 1, 0.0), (2, 2, 0.0)))
        and measurements.max_abs_left_u1 <= 1.0e-7
        and abs(measurements.pinned_u2) <= 1.0e-7
    )
    traction = (
        input_contract.traction_records[0]
        if len(input_contract.traction_records) == 1
        else None
    )
    traction_contract = _verify_traction_contract(
        input_contract,
        expected,
    )
    traction_contract_passed = traction_contract.passed
    loaded_surface_passed = (
        traction is not None
        and measurements.surface_name.casefold() == traction.surface_name.casefold()
        and measurements.surface_is_full_right_edge
        and measurements.surface_unique_node_count == expected.right_boundary_node_count
        and measurements.surface_element_count == expected.right_boundary_element_count
    )
    required_outputs = {"U", "S", "RF"}
    output_fields_passed = required_outputs.issubset(
        input_contract.field_output_variables
    ) and required_outputs.issubset(measurements.field_outputs)
    right_u1 = _numeric_check(
        expected.right_mean_u1,
        measurements.right_mean_u1,
        expected.relative_tolerance,
    )
    mean_s11 = _numeric_check(
        expected.mean_s11,
        measurements.mean_s11,
        expected.relative_tolerance,
    )
    left_reaction = _numeric_check(
        expected.expected_force,
        abs(measurements.left_sum_rf1),
        expected.relative_tolerance,
    )
    right_reaction = _numeric_check(
        0.0,
        abs(measurements.right_sum_rf1),
        1.0e-6,
    )
    results_json_valid, results_match_oracle = _check_agent_results(
        agent_results,
        measurements,
        expected.relative_tolerance,
    )
    new_requirement_passed = (
        displacement_mode_removed
        and traction_contract_passed
        and loaded_surface_passed
        and right_u1.passed
        and mean_s11.passed
        and left_reaction.passed
        and right_reaction.passed
    )
    regression_passed = (
        source_run_verified
        and geometry_and_mesh_passed
        and material_passed
        and analysis_passed
        and output_fields_passed
        and results_json_valid
        and results_match_oracle
    )
    checks_before_traction = (
        (job_completed, "plate_tension Solver completion evidence is missing"),
        (artifacts_fresh, "one or more required change artifacts are missing or stale"),
        (build_script_changed, "build_model.py was not changed from the verified seed"),
        (
            extractor_preserved,
            "extract_results.py changed despite an unchanged result contract",
        ),
        (source_run_verified, "the source CodingRun was not independently verified"),
        (geometry_and_mesh_passed, "the original geometry or 20x4 CPS4R mesh changed"),
        (material_passed, "the original material or 1 mm section changed"),
        (analysis_passed, "the Static/NLGEOM/no-extra-load contract changed"),
        (displacement_mode_removed, "the old right-edge displacement mode remains"),
    )
    checks_after_traction = (
        (loaded_surface_passed, "the loaded ODB surface is not the full right edge"),
        (output_fields_passed, "U, S and RF output is incomplete"),
        (right_u1.passed, "right-edge response does not match traction reference"),
        (mean_s11.passed, "mean S11 does not match traction reference"),
        (left_reaction.passed, "left reaction does not balance applied traction"),
        (right_reaction.passed, "right boundary still carries displacement reaction"),
        (results_json_valid, "results.json is missing the unchanged numeric fields"),
        (results_match_oracle, "results.json does not match the private ODB oracle"),
    )
    checks = (*checks_before_traction, *checks_after_traction)
    findings = (
        *(message for passed, message in checks_before_traction if not passed),
        *traction_contract.findings,
        *(message for passed, message in checks_after_traction if not passed),
    )
    return LoadModeVerification(
        passed=(all(passed for passed, _ in checks) and traction_contract_passed),
        job_completed=job_completed,
        artifacts_fresh=artifacts_fresh,
        source_run_verified=source_run_verified,
        build_script_changed=build_script_changed,
        extractor_preserved=extractor_preserved,
        new_requirement_passed=new_requirement_passed,
        regression_passed=regression_passed,
        geometry_and_mesh_passed=geometry_and_mesh_passed,
        material_passed=material_passed,
        analysis_passed=analysis_passed,
        displacement_mode_removed=displacement_mode_removed,
        traction_contract_passed=traction_contract_passed,
        traction_contract=traction_contract,
        loaded_surface_passed=loaded_surface_passed,
        output_fields_passed=output_fields_passed,
        results_json_valid=results_json_valid,
        results_match_oracle=results_match_oracle,
        right_u1=right_u1,
        mean_s11=mean_s11,
        left_reaction=left_reaction,
        right_reaction=right_reaction,
        findings=findings,
    )


def _verify_traction_contract(
    input_contract: LoadModeInputContract,
    expected: LoadModeOracleSpec,
) -> TractionContractVerification:
    record_count = len(input_contract.traction_records)
    if record_count != 1:
        finding = (
            "traction record count mismatch: expected exactly one *DSLOAD "
            f"traction record, got {record_count}"
        )
        return TractionContractVerification(
            passed=False,
            record_count=record_count,
            record_count_passed=False,
            load_type_passed=False,
            magnitude_passed=False,
            direction_passed=False,
            follower_passed=False,
            surface_declared=False,
            findings=(finding,),
        )

    traction = input_contract.traction_records[0]
    norm = math.sqrt(sum(component * component for component in traction.direction))
    normalized = (
        (
            traction.direction[0] / norm,
            traction.direction[1] / norm,
            traction.direction[2] / norm,
        )
        if norm > 0.0
        else (0.0, 0.0, 0.0)
    )
    load_type_passed = traction.load_type.upper() == "TRVEC"
    magnitude_passed = abs(traction.magnitude - expected.traction_magnitude) <= 1.0e-9
    direction_passed = all(
        abs(actual - target) <= 1.0e-9
        for actual, target in zip(
            normalized,
            (1.0, 0.0, 0.0),
            strict=True,
        )
    )
    follower_passed = not traction.follower
    surface_declared = traction.surface_name in input_contract.surface_names
    checks = (
        (
            load_type_passed,
            f"traction type mismatch: expected TRVEC, got {traction.load_type!r}",
        ),
        (
            magnitude_passed,
            "traction magnitude mismatch: expected "
            f"{expected.traction_magnitude:g} MPa, got {traction.magnitude:g}",
        ),
        (
            direction_passed,
            "traction direction mismatch: expected normalized global +X "
            f"(1, 0, 0), got raw={traction.direction!r}, "
            f"normalized={normalized!r}",
        ),
        (
            follower_passed,
            "traction follower mismatch: expected false (non-follower), got "
            "true; set follower=OFF so the INP emits *DSLOAD, follower=NO",
        ),
        (
            surface_declared,
            "traction surface mismatch: "
            f"{traction.surface_name!r} is not declared by *SURFACE",
        ),
    )
    findings = tuple(message for passed, message in checks if not passed)
    return TractionContractVerification(
        passed=all(passed for passed, _ in checks),
        record_count=record_count,
        record_count_passed=True,
        load_type_actual=traction.load_type,
        load_type_passed=load_type_passed,
        magnitude_actual=traction.magnitude,
        magnitude_passed=magnitude_passed,
        direction_actual=traction.direction,
        direction_normalized=normalized,
        direction_passed=direction_passed,
        follower_actual=traction.follower,
        follower_passed=follower_passed,
        surface_name_actual=traction.surface_name,
        surface_declared=surface_declared,
        findings=findings,
    )


def parse_load_mode_input(path: Path) -> LoadModeInputContract:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    lines = [line.strip() for line in text.splitlines()]
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
    elastic = _numeric_data_after(lines, "*elastic", expected=2)
    thickness = _numeric_data_after(lines, "*solid section", expected=1)
    return LoadModeInputContract(
        elastic_modulus=elastic[0] if elastic is not None else None,
        poisson_ratio=elastic[1] if elastic is not None else None,
        thickness=thickness[0] if thickness is not None else None,
        material_name=_keyword_parameter(lines, "*material", "name"),
        section_material_name=_keyword_parameter(lines, "*solid section", "material"),
        element_types=tuple(element_types),
        step_count=len(step_headers),
        static_step_count=sum(line.casefold().startswith("*static") for line in lines),
        nlgeom_off=bool(step_headers)
        and all(
            "nlgeom=no" in line.replace(" ", "").casefold() for line in step_headers
        ),
        field_output_variables=tuple(sorted(_field_output_variables(lines))),
        boundary_records=_boundary_records(lines),
        traction_records=_traction_records(lines),
        surface_names=_surface_names(lines),
        forbidden_keywords=_forbidden_keywords(lines),
    )


def _keyword_parameter(lines: list[str], keyword: str, parameter: str) -> str | None:
    for line in lines:
        if line.casefold().startswith(keyword.casefold()):
            match = re.search(rf"\b{re.escape(parameter)}\s*=\s*([^,]+)", line, re.I)
            if match is not None:
                return match.group(1).strip()
    return None


def _numeric_data_after(
    lines: list[str], keyword: str, *, expected: int
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


def _boundary_records(lines: list[str]) -> tuple[BoundaryRecord, ...]:
    records: list[BoundaryRecord] = []
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
                    BoundaryRecord(
                        region=values[0],
                        first_dof=int(values[1]),
                        last_dof=int(values[2]),
                        magnitude=(
                            float(values[3]) if len(values) > 3 and values[3] else 0.0
                        ),
                    )
                )
            except ValueError:
                continue
    return tuple(records)


def _traction_records(lines: list[str]) -> tuple[TractionRecord, ...]:
    records: list[TractionRecord] = []
    for index, line in enumerate(lines):
        if not line.casefold().startswith("*dsload"):
            continue
        follower_match = re.search(r"\bfollower\s*=\s*([^,]+)", line, re.I)
        follower = follower_match is None or follower_match.group(
            1
        ).strip().casefold() not in {"no", "off"}
        for candidate in lines[index + 1 :]:
            if not candidate or candidate.startswith("**"):
                continue
            if candidate.startswith("*"):
                break
            values = [value.strip() for value in candidate.split(",") if value.strip()]
            if len(values) < 5:
                continue
            try:
                direction_values = [float(value) for value in values[3:6]]
            except ValueError:
                continue
            while len(direction_values) < 3:
                direction_values.append(0.0)
            records.append(
                TractionRecord(
                    surface_name=values[0],
                    load_type=values[1],
                    magnitude=float(values[2]),
                    direction=(
                        direction_values[0],
                        direction_values[1],
                        direction_values[2],
                    ),
                    follower=follower,
                )
            )
    return tuple(records)


def _surface_names(lines: list[str]) -> tuple[str, ...]:
    names: list[str] = []
    for line in lines:
        if line.casefold().startswith("*surface"):
            match = re.search(r"\bname\s*=\s*([^,]+)", line, re.I)
            if match is not None:
                names.append(match.group(1).strip())
    return tuple(names)


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


def _forbidden_keywords(lines: list[str]) -> tuple[str, ...]:
    forbidden = (
        "*body force",
        "*cload",
        "*contact",
        "*dload",
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
    measurements: LoadModeMeasurements,
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
        _numeric_check(expected, values[key], tolerance).passed
        for key, expected in expected_values.items()
    )


def _numeric_check(expected: float, actual: float, tolerance: float) -> NumericCheck:
    if expected == 0.0:
        relative_error = abs(actual)
    else:
        relative_error = abs(actual - expected) / abs(expected)
    return NumericCheck(
        passed=math.isfinite(actual) and relative_error <= tolerance,
        expected=expected,
        actual=actual,
        relative_error=relative_error,
        tolerance=tolerance,
    )
