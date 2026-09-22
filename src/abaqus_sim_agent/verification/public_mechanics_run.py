"""公开力学 Case 的宿主验收：出处、输入含义、独立 ODB 与数值对照。"""

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Literal
from uuid import uuid4

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ReadyForVerificationDecision,
)
from abaqus_sim_agent.coding.verification_port import VerificationAttempt
from abaqus_sim_agent.coding.workspace import (
    WorkspaceResolver,
    WorkspaceSnapshot,
    artifact_evidence_from_snapshots,
)
from abaqus_sim_agent.evaluation.public_mechanics_cases import MechanicsCase
from abaqus_sim_agent.evaluation.public_mechanics_runner import (
    execution_key,
    read_observations,
)
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
)
from abaqus_sim_agent.verification.model_change_evidence import status_completed
from abaqus_sim_agent.verification.public_mechanics_input import check_input


class PublicMechanicsAttempt(VerificationAttempt):
    measured: dict[str, float] | None = None
    findings: tuple[str, ...] = ()
    advisories: tuple[str, ...] = ()
    verification_policy: str = "results_first_v1"
    script_review_status: Literal["not_reviewed"] = "not_reviewed"


def _produced(result, names, current, observations):
    changed = {
        c.path: c.after.sha256 for c in result.artifact_changes if c.after is not None
    }
    witnessed = observations.get(execution_key(result), {})
    before = witnessed.get("before", {})
    after = witnessed.get("after", {})

    def produced(name):
        if name not in current:
            return False
        if changed.get(name) == current[name].sha256:
            return True
        return (
            name in after
            and after[name]["sha256"] == current[name].sha256
            and after[name] != before.get(name)
        )

    return result.stage_succeeded and all(produced(name) for name in names)


def _read_input(result, name, current):
    return name in current and any(
        f.path == name and f.sha256 == current[name].sha256
        for f in result.input_fingerprints
    )


def provenance_errors(
    case: MechanicsCase,
    current: WorkspaceSnapshot,
    executions: Sequence[AbaqusExecutionResult],
    observations: dict | None = None,
    *,
    require_current_scripts: bool = True,
) -> list[str]:
    files = current.by_path()
    observations = observations or {}
    jobs = ("model_coarse", "model") if case.refinement else ("model",)
    cae = [
        i
        for i, r in enumerate(executions)
        if r.invocation == AbaqusInvocation.CAE_NOGUI
        and _produced(
            r, ("model.cae", *(j + ".inp" for j in jobs)), files, observations
        )
        and (not require_current_scripts or _read_input(r, "build_model.py", files))
    ]
    if not cae:
        return [
            "No CAE invocation produced the current CAE/INPs from current build_model.py"
        ]
    if any(
        c.path.lower().endswith((".odb", ".sta", ".msg", ".dat"))
        for r in executions
        if r.invocation == AbaqusInvocation.CAE_NOGUI
        for c in r.artifact_changes
    ):
        return ["CAE stage produced solver artifacts"]
    solver_indices = []
    for job in jobs:
        matches = [
            i
            for i, r in enumerate(executions)
            if i > (max(cae) if require_current_scripts else min(cae))
            and r.invocation == AbaqusInvocation.SOLVER_JOB
            and f"job={job}" in r.command
            and _produced(r, (job + ".odb",), files, observations)
            and _read_input(r, job + ".inp", files)
        ]
        if not matches:
            return [f"No solver provenance for current {job}.odb/INP"]
        solver_indices.append(max(matches))
    if not any(
        i > max(solver_indices)
        and r.invocation == AbaqusInvocation.ABAQUS_PYTHON
        and _produced(r, ("results.json",), files, observations)
        and (not require_current_scripts or _read_input(r, "extract_results.py", files))
        for i, r in enumerate(executions)
    ):
        return ["No extraction provenance after current solver outputs"]
    return []


def compare_metrics(
    case: MechanicsCase, measured: dict[str, float], reported: dict
) -> list[str]:
    errors = []
    for key, spec in case.metrics.items():
        value = measured.get(key)
        if value is None or not math.isfinite(value):
            errors.append(f"missing/nonfinite oracle {key}")
            continue
        if not math.isclose(
            value,
            spec.target,
            rel_tol=spec.relative_tolerance,
            abs_tol=spec.absolute_tolerance,
        ):
            errors.append(
                f"{key}: actual {value:g}, reference {spec.target:g}, relative tolerance {spec.relative_tolerance:g}"
            )
        claimed = reported.get(key)
        if (
            isinstance(claimed, bool)
            or not isinstance(claimed, (float, int))
            or not math.isfinite(claimed)
            or not math.isclose(
                claimed, value, rel_tol=1e-5, abs_tol=spec.absolute_tolerance
            )
        ):
            errors.append(
                f"results.json {key} does not match independent ODB measurement"
            )
    return errors


def refinement_errors(
    case: MechanicsCase, coarse: dict, fine: dict, *, require_more_nodes: bool = True
) -> list[str]:
    if require_more_nodes and len(fine["nodes"]) <= len(coarse["nodes"]):
        return ["refined model must have more nodes"]
    keys = [k for k in case.metrics if k.startswith("tip_") or k == "hole_max_s11"]
    return [
        f"refinement change exceeds 1% for {k}"
        for k in keys
        if abs(fine["metrics"][k] - coarse["metrics"][k])
        > max(abs(fine["metrics"][k]) * 0.01, case.metrics[k].absolute_tolerance)
    ]


def model_identity_errors(case: MechanicsCase, raw: dict) -> list[str]:
    """从独立测量核对结果所属的问题尺度；不要求固定 INP 写法。

    Note:
        不声称验证全部边界条件或载荷分布；这些由结果、反力及补充 INP 建议共同说明。
    """
    try:
        nodes = list(raw["nodes"].values())
        if not nodes or not raw["elements"]:
            return ["empty independent mesh measurement"]
        if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in nodes):
            return ["invalid independent node coordinates"]
        x_min = case.inner_radius if case.family == "axisymmetric_pressure" else 0.0
        if case.family == "cylindrical_shell_patch":
            angle = math.radians(case.arc_angle_degrees)
            expected = (
                (case.radius * math.cos(angle), case.radius),
                (0, case.radius * math.sin(angle)),
                (0, case.length),
            )
        elif case.family == "axisymmetric_shell":
            if case.axisymmetric_shell_profile == "hyperbolic":
                expected = (
                    (case.radius, case.width),
                    (0, case.length),
                    (0, 0),
                )
            elif case.axisymmetric_shell_profile == "sphere_cylinder":
                expected = (
                    (0, case.radius),
                    (0, case.length),
                    (0, 0),
                )
            elif case.axisymmetric_shell_profile == "crown_knuckle_cylinder":
                expected = (
                    (0, case.radius),
                    (0, case.length),
                    (0, 0),
                )
            elif case.axisymmetric_shell_profile == "branched_sphere_cylinder":
                expected = (
                    (0, case.radius),
                    (0, case.length),
                    (0, 0),
                )
            else:
                return ["unsupported axisymmetric-shell profile"]
        elif case.family == "z_section_shell":
            expected = ((0, case.length), (-1, 1), (-1, 1))
        elif case.family == "hemisphere_shell":
            expected = ((0, case.radius), (0, case.radius), (0, case.radius))
        elif case.family == "skew_plate":
            angle = math.radians(case.skew_angle_degrees)
            expected = (
                (0, case.length + case.width * math.cos(angle)),
                (0, case.width * math.sin(angle)),
                (0, 0),
            )
        elif case.family == "thermal_solid":
            expected = ((0, case.length), (0, case.width), (0, case.height))
        else:
            expected = (
                (x_min, case.length),
                (0, 0 if case.family in {"truss", "beam", "shaft"} else case.width),
                (
                    0,
                    0
                    if case.family
                    in {
                        "truss",
                        "beam",
                        "shaft",
                        "plane_stress",
                        "elliptic_membrane",
                        "axisymmetric_pressure",
                    }
                    else case.height,
                ),
            )
        tolerance = max(case.length * 1e-7, 1e-8)
        errors = []
        bounds_tolerance = (
            2e-4
            if case.family == "axisymmetric_shell"
            and case.axisymmetric_shell_profile == "crown_knuckle_cylinder"
            else tolerance
        )
        if any(
            abs(actual - target) > bounds_tolerance
            for axis, ends in enumerate(expected)
            for actual, target in zip(
                (min(p[axis] for p in nodes), max(p[axis] for p in nodes)),
                ends,
                strict=True,
            )
        ):
            errors.append("independent geometry bounding box mismatch")
        if case.family == "cylindrical_shell_patch":
            surface_tol = max(case.radius * 2e-5, tolerance)
            max_angle = math.radians(case.arc_angle_degrees)
            target_angle = math.radians(case.sample_angle_degrees)
            target = (
                case.radius * math.cos(target_angle),
                case.radius * math.sin(target_angle),
                case.sample_axial_position,
            )
            target_nodes = 0
            for point in nodes:
                radius = math.hypot(point[0], point[1])
                theta = math.atan2(point[1], point[0])
                if (
                    not math.isclose(
                        radius, case.radius, rel_tol=0.0, abs_tol=surface_tol
                    )
                    or theta < -surface_tol
                    or theta > max_angle + surface_tol
                    or point[2] < -tolerance
                    or point[2] > case.length + tolerance
                ):
                    errors.append("independent cylindrical midsurface mismatch")
                    break
                if math.dist(point, target) < surface_tol:
                    target_nodes += 1
            if target_nodes != 1:
                errors.append("independent cylindrical E-node mismatch")
        elif case.family == "axisymmetric_shell":
            surface_tol = 2e-4
            if case.axisymmetric_shell_profile == "hyperbolic":
                k_nodes = 0
                for point in nodes:
                    radius, axial, out_of_plane = point
                    if (
                        abs(out_of_plane) > tolerance
                        or radius < case.radius - tolerance
                        or radius > case.width + tolerance
                        or axial < -tolerance
                        or axial > case.length + tolerance
                        or abs(
                            radius * radius - axial * axial - case.radius * case.radius
                        )
                        > surface_tol
                    ):
                        errors.append(
                            "independent axisymmetric hyperbolic midsurface mismatch"
                        )
                        break
                    if abs(radius - case.radius) < tolerance and abs(axial) < tolerance:
                        k_nodes += 1
                if k_nodes != 1:
                    errors.append("independent axisymmetric K-node mismatch")
            elif case.axisymmetric_shell_profile == "sphere_cylinder":
                d_nodes = 0
                for point in nodes:
                    radius, axial, out_of_plane = point
                    valid_cylinder = (
                        axial <= case.width + tolerance
                        and abs(radius - case.radius) <= surface_tol
                    )
                    valid_sphere = (
                        axial >= case.width - tolerance
                        and abs(
                            radius * radius
                            + (axial - case.width) * (axial - case.width)
                            - case.radius * case.radius
                        )
                        <= surface_tol
                    )
                    if (
                        abs(out_of_plane) > tolerance
                        or radius < -tolerance
                        or radius > case.radius + tolerance
                        or axial < -tolerance
                        or axial > case.length + tolerance
                        or not (valid_cylinder or valid_sphere)
                    ):
                        errors.append(
                            "independent axisymmetric sphere-cylinder midsurface mismatch"
                        )
                        break
                    if (
                        abs(radius - case.radius) < tolerance
                        and abs(axial - case.sample_axial_position) < tolerance
                    ):
                        d_nodes += 1
                if d_nodes != 1:
                    errors.append("independent axisymmetric D-node mismatch")
            elif case.axisymmetric_shell_profile == "crown_knuckle_cylinder":
                d_nodes = 0
                crown_center_z = case.width + case.crown_radius
                knuckle_center_r = case.radius - case.knuckle_radius
                for point in nodes:
                    radius, axial, out_of_plane = point
                    valid_cylinder = (
                        axial <= case.width + surface_tol
                        and abs(radius - case.radius) <= surface_tol
                    )
                    valid_crown = (
                        radius <= case.sample_radius + surface_tol
                        and abs(
                            radius * radius
                            + (axial - crown_center_z) * (axial - crown_center_z)
                            - case.crown_radius * case.crown_radius
                        )
                        <= surface_tol
                    )
                    valid_knuckle = (
                        radius >= case.sample_radius - surface_tol
                        and axial >= case.width - surface_tol
                        and abs(
                            (radius - knuckle_center_r) * (radius - knuckle_center_r)
                            + (axial - case.width) * (axial - case.width)
                            - case.knuckle_radius * case.knuckle_radius
                        )
                        <= surface_tol
                    )
                    if (
                        abs(out_of_plane) > tolerance
                        or radius < -tolerance
                        or radius > case.radius + tolerance
                        or axial < -tolerance
                        or axial > case.length + surface_tol
                        or not (valid_cylinder or valid_crown or valid_knuckle)
                    ):
                        errors.append(
                            "independent axisymmetric crown-knuckle-cylinder midsurface mismatch"
                        )
                        break
                    if (
                        abs(radius - case.sample_radius) < tolerance
                        and abs(axial - case.sample_axial_position) < tolerance
                    ):
                        d_nodes += 1
                if d_nodes != 1:
                    errors.append("independent axisymmetric D-node mismatch")
            elif case.axisymmetric_shell_profile == "branched_sphere_cylinder":
                junction_nodes = 0
                pole_nodes = 0
                sphere_pole_z = (
                    case.branch_center_axial_position - case.branch_sphere_radius
                )
                for point in nodes:
                    radius, axial, out_of_plane = point
                    valid_cylinder = (
                        abs(radius - case.radius) <= surface_tol
                        and -tolerance <= axial <= case.length + tolerance
                    )
                    valid_sphere = (
                        -tolerance <= radius <= case.radius + surface_tol
                        and sphere_pole_z - surface_tol
                        <= axial
                        <= case.sample_axial_position + surface_tol
                        and abs(
                            radius * radius
                            + (axial - case.branch_center_axial_position) ** 2
                            - case.branch_sphere_radius**2
                        )
                        <= surface_tol
                    )
                    if abs(out_of_plane) > tolerance or not (
                        valid_cylinder or valid_sphere
                    ):
                        errors.append(
                            "independent branched axisymmetric midsurface mismatch"
                        )
                        break
                    if (
                        abs(radius - case.sample_radius) < tolerance
                        and abs(axial - case.sample_axial_position) < tolerance
                    ):
                        junction_nodes += 1
                    if (
                        abs(radius) < tolerance
                        and abs(axial - sphere_pole_z) < tolerance
                    ):
                        pole_nodes += 1
                if junction_nodes != 2:
                    errors.append("independent branched C-node pair mismatch")
                if pole_nodes != 1:
                    errors.append("independent branched sphere-pole mismatch")

                measured_nodes = {
                    int(label): point for label, point in raw["nodes"].items()
                }
                cylinder_elements = 0
                sphere_elements = 0
                for element in raw["elements"].values():
                    points = [measured_nodes[int(n)] for n in element["nodes"]]
                    if all(
                        abs(point[0] - case.radius) <= surface_tol for point in points
                    ):
                        cylinder_elements += 1
                    elif all(
                        abs(
                            point[0] * point[0]
                            + (point[1] - case.branch_center_axial_position) ** 2
                            - case.branch_sphere_radius**2
                        )
                        <= surface_tol
                        for point in points
                    ):
                        sphere_elements += 1
                    else:
                        errors.append(
                            "independent branched element crosses declared branches"
                        )
                        break
                if not cylinder_elements or not sphere_elements:
                    errors.append("independent branched shell requires both branches")
            else:
                errors.append("unsupported axisymmetric-shell profile")
        elif case.family == "thermal_solid":
            surface_tol = 3e-4
            landmarks = (
                (1.0, 0.0, 0.0),
                (0.7071, 0.0, 0.7071),
                (0.7071, 0.0, 1.79),
                (1.4, 0.0, 0.0),
                (1.2124, 0.0, 0.7),
                (1.1062, 0.0, 1.045),
                (1.0, 0.0, 1.39),
                (1.0, 0.0, 1.79),
            )
            if any(
                sum(math.dist(point, target) <= surface_tol for point in nodes) != 1
                for target in landmarks
            ):
                errors.append("independent thermal-solid profile landmark mismatch")
            a_nodes = sum(
                math.dist(point, (1.0, 0.0, 0.0)) <= surface_tol for point in nodes
            )
            if a_nodes != 1:
                errors.append("independent thermal-solid A-node mismatch")
            for point in nodes:
                x, y, z = point
                radius = math.hypot(x, y)
                if (
                    x < -surface_tol
                    or y < -surface_tol
                    or z < -surface_tol
                    or z > case.height + surface_tol
                ):
                    errors.append("independent thermal-solid quarter-sector mismatch")
                    break
                inner = math.sqrt(max(0.0, 1.0 - z * z)) if z <= 0.7071 else 0.7071
                if z <= 0.7:
                    outer = math.sqrt(max(0.0, 1.4**2 - z * z))
                elif z <= 1.39:
                    outer = 1.2124 + (z - 0.7) * (1.0 - 1.2124) / (1.39 - 0.7)
                else:
                    outer = 1.0
                if radius < inner - surface_tol or radius > outer + surface_tol:
                    errors.append("independent thermal-solid radial profile mismatch")
                    break
        rows = raw["elastic"]
        if not rows or any(
            not row
            or not math.isfinite(row[0])
            or not math.isclose(row[0], case.young, rel_tol=1e-7)
            or not (
                (len(row) == 1 and case.poisson == 0)
                or (
                    len(row) == 2
                    and math.isfinite(row[1])
                    and math.isclose(row[1], case.poisson, abs_tol=1e-7)
                )
            )
            for row in rows
        ):
            errors.append("independent elastic material mismatch")
        if case.family == "thermal_solid":
            expansion = raw.get("expansion", [])
            if (
                len(expansion) != 1
                or not expansion[0]
                or not math.isfinite(expansion[0][0])
                or not math.isclose(
                    expansion[0][0], case.thermal_expansion, rel_tol=1e-7
                )
            ):
                errors.append("independent thermal expansion mismatch")
        if len(raw["step_names"]) != (3 if case.family == "solid_beam" else 1):
            errors.append("independent load case count mismatch")
        if case.family in {"hole", "solid_beam"}:
            volume = raw.get("volume")
            expected_volume = case.length * case.width * case.height - (
                math.pi * 0.5**2 * case.height if case.family == "hole" else 0
            )
            if (
                isinstance(volume, bool)
                or not isinstance(volume, (float, int))
                or not math.isfinite(volume)
                or not math.isclose(volume, expected_volume, rel_tol=0.005)
            ):
                errors.append("independent solid volume mismatch or missing EVOL")
        return errors
    except (ValueError, KeyError, TypeError, IndexError):
        return ["invalid independent model identity measurement"]


def assess_measurements(case, paths, measurements, reported, *, results_first=True):
    errors, advisories = [], []
    for job, raw in measurements.items():
        if results_first:
            errors += [f"{job}: {e}" for e in model_identity_errors(case, raw)]
        input_findings = [f"{job}: {e}" for e in check_input(paths[job], case, raw)]
        (advisories if results_first else errors).extend(input_findings)
    errors += compare_metrics(case, measurements["model"]["metrics"], reported)
    if case.refinement:
        coarse, fine = measurements["model_coarse"], measurements["model"]
        errors += refinement_errors(
            case, coarse, fine, require_more_nodes=not results_first
        )
        if results_first and len(fine["nodes"]) <= len(coarse["nodes"]):
            advisories.append(
                "refinement has no node-count increase; inspect the declared mesh change"
            )
    return errors, advisories


class PublicMechanicsRunVerifier:
    def __init__(
        self,
        case: MechanicsCase,
        *,
        policy: Literal["results_first_v1", "strict_contract_v1"] = "results_first_v1",
    ):
        self.case = case
        if policy not in {"results_first_v1", "strict_contract_v1"}:
            raise ValueError("unknown public mechanics verification policy")
        self.policy = policy
        self.oracle = Path(__file__).with_name("assets") / "public_mechanics_oracle.py"

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> PublicMechanicsAttempt:
        current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, freshness = artifact_evidence_from_snapshots(
            initial_snapshot, current
        )
        required = {
            "build_model.py",
            "extract_results.py",
            "model.cae",
            "model.inp",
            "model.odb",
            "results.json",
            "report.md",
        }
        if self.case.refinement:
            required.update({"model_coarse.inp", "model_coarse.odb"})
        errors = [
            f"missing/stale artifact {p}"
            for p in required
            if not freshness.get(p, False)
        ]
        advisories = []
        results_first = self.policy == "results_first_v1"
        errors += provenance_errors(
            self.case,
            current,
            executions,
            read_observations(request.state_dir),
            require_current_scripts=not results_first,
        )
        if results_first:
            advisories += provenance_errors(
                self.case, current, executions, read_observations(request.state_dir)
            )
        jobs = ("model_coarse", "model") if self.case.refinement else ("model",)
        errors += [
            f"{j}.sta does not confirm completion"
            for j in jobs
            if not status_completed(request.workspace / (j + ".sta"))
        ]
        if errors:
            return PublicMechanicsAttempt(
                passed=False,
                feedback="; ".join(errors),
                artifacts=artifacts,
                failure_stage="provenance",
                findings=tuple(errors),
                advisories=tuple(advisories),
                verification_policy=self.policy,
            )
        try:
            reported = json.loads((request.workspace / "results.json").read_bytes())
            if not isinstance(reported, dict):
                raise ValueError("results.json must be an object")
        except (ValueError, OSError) as exc:
            return PublicMechanicsAttempt(
                passed=False,
                feedback=str(exc),
                artifacts=artifacts,
                failure_stage="results",
                verification_policy=self.policy,
            )
        measurements = {}
        for job in jobs:
            folder = request.state_dir / ("public-oracle-" + uuid4().hex[:10])
            folder.mkdir()
            inp = folder / "request.json"
            out = folder / "measurement.json"
            inp.write_text(
                json.dumps(
                    {
                        "odb_path": str(request.workspace / (job + ".odb")),
                        "case": self.case.model_dump(mode="json"),
                    }
                ),
                encoding="utf-8",
            )
            result = await runner.run_trusted_abaqus_python(
                self.oracle,
                args=(str(inp), str(out)),
                workspace_artifacts=(job + ".odb",),
                timeout_seconds=180,
            )
            (folder / "execution.json").write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )
            if not result.succeeded or not out.is_file():
                return PublicMechanicsAttempt(
                    passed=False,
                    feedback="Independent ODB reader failed: " + result.stderr[-1500:],
                    artifacts=artifacts,
                    failure_stage="oracle",
                    verification_policy=self.policy,
                )
            raw = json.loads(out.read_bytes())
            measurements[job] = raw
        measurement_errors, input_advisories = assess_measurements(
            self.case,
            {job: request.workspace / (job + ".inp") for job in jobs},
            measurements,
            reported,
            results_first=results_first,
        )
        errors += measurement_errors
        advisories += input_advisories
        after = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        if any(
            after.by_path()[j + ".odb"].sha256 != current.by_path()[j + ".odb"].sha256
            for j in jobs
        ):
            errors.append("ODB changed during oracle reading")
        return PublicMechanicsAttempt(
            passed=not errors,
            feedback=(
                "Independent benchmark results passed; INP implementation checks are advisory."
                if results_first
                else "Public mechanics task oracle passed."
            )
            if not errors
            else "; ".join(errors),
            artifacts=artifacts,
            measured=measurements["model"]["metrics"],
            findings=tuple(errors),
            advisories=tuple(advisories),
            verification_policy=self.policy,
            failure_stage="task_oracle" if errors else None,
        )
