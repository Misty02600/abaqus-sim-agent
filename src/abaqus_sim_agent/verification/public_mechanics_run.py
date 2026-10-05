"""公开力学 Case 的宿主验收：执行证据及提交值与隐藏参考值的对照。"""

import json
import math
from collections.abc import Sequence
from typing import TypeGuard

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ReadyForVerificationDecision,
)
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
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

VERIFICATION_POLICY = "submitted_results_v1"


class PublicMechanicsAttempt(VerificationAttempt):
    reported: dict[str, float] | None = None
    findings: tuple[str, ...] = ()
    advisories: tuple[str, ...] = ()
    verification_policy: str = VERIFICATION_POLICY


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


def _finite_number(value: object) -> TypeGuard[int | float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def compare_metrics(case: MechanicsCase, reported: dict) -> list[str]:
    """把本次提交的物理量与隐藏参考值比较，不要求另一套提取程序复现提交值。"""
    errors = []
    for key, spec in case.metrics.items():
        value = reported.get(key)
        if not _finite_number(value):
            errors.append(f"{key}: missing or nonfinite submitted value")
        elif not math.isclose(
            value,
            spec.target,
            rel_tol=spec.relative_tolerance,
            abs_tol=spec.absolute_tolerance,
        ):
            errors.append(
                f"{key}: submitted {value:g}, reference {spec.target:g}, relative tolerance {spec.relative_tolerance:g}"
            )
    return errors


def refinement_errors(case: MechanicsCase, coarse: dict, fine: dict) -> list[str]:
    errors = []
    for key in case.refinement_metrics:
        if not _finite_number(coarse.get(key)) or not _finite_number(fine.get(key)):
            errors.append(f"refinement values missing/nonfinite for {key}")
        elif abs(fine[key] - coarse[key]) > max(
            abs(fine[key]) * 0.01, case.metrics[key].absolute_tolerance
        ):
            errors.append(f"refinement change exceeds 1% for {key}")
    return errors


class PublicMechanicsRunVerifier:
    def __init__(self, case: MechanicsCase, *, policy: str = VERIFICATION_POLICY):
        if policy != VERIFICATION_POLICY:
            raise ValueError("historical verification requires its frozen worker")
        self.case = case
        self.policy = policy

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> PublicMechanicsAttempt:
        evidence = await ExecutionEvidenceVerifier().verify(
            request, runner, initial_snapshot, executions, decision
        )
        current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, freshness = artifact_evidence_from_snapshots(
            initial_snapshot, current
        )
        jobs = ("model_coarse", "model") if self.case.refinement else ("model",)
        required = {
            "build_model.py",
            "extract_results.py",
            "model.cae",
            "results.json",
            "report.md",
            *(j + suffix for j in jobs for suffix in (".inp", ".odb")),
        }
        errors = [
            f"missing/stale artifact {p}"
            for p in sorted(required)
            if not freshness.get(p, False)
        ]
        if not evidence.passed:
            errors.append(evidence.feedback)
        observations = read_observations(request.state_dir)
        errors += provenance_errors(
            self.case, current, executions, observations, require_current_scripts=False
        )
        advisories = provenance_errors(self.case, current, executions, observations)
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
            )
        errors = compare_metrics(self.case, reported)
        if self.case.refinement:
            coarse = reported.get("coarse_metrics")
            errors += refinement_errors(
                self.case, coarse if isinstance(coarse, dict) else {}, reported
            )
        return PublicMechanicsAttempt(
            passed=not errors,
            feedback="Execution, deliverables and submitted numerical targets passed; modeling details are not independently verified."
            if not errors
            else "; ".join(errors),
            artifacts=artifacts,
            reported={
                key: float(reported[key])
                for key in self.case.metrics
                if _finite_number(reported.get(key))
            },
            findings=tuple(errors),
            advisories=tuple(advisories),
            failure_stage="task_oracle" if errors else None,
        )
