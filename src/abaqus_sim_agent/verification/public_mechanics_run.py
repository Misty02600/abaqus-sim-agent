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
        expected = (
            (0, case.length),
            (0, 0 if case.family in {"truss", "beam"} else case.width),
            (0, 0 if case.family in {"truss", "beam"} else case.height),
        )
        tolerance = max(case.length * 1e-7, 1e-8)
        errors = []
        if any(
            abs(actual - target) > tolerance
            for axis, ends in enumerate(expected)
            for actual, target in zip(
                (min(p[axis] for p in nodes), max(p[axis] for p in nodes)),
                ends,
                strict=True,
            )
        ):
            errors.append("independent geometry bounding box mismatch")
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
