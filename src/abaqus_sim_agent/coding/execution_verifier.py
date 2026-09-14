from __future__ import annotations

import ast
import tokenize
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

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
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
)

_MODEL_VISIBLE_INVOCATIONS = {
    AbaqusInvocation.CAE_NOGUI,
    AbaqusInvocation.SOLVER_JOB,
    AbaqusInvocation.ABAQUS_PYTHON,
}


class ExecutionEvidenceVerification(VerificationAttempt):
    declared_deliverable_paths: tuple[str, ...] = ()
    fresh_artifact_paths: tuple[str, ...] = ()
    checked_python_scripts: tuple[str, ...] = ()
    execution_attempts: int = Field(default=0, ge=0)
    latest_successful_invocations: tuple[AbaqusInvocation, ...] = ()


class ExecutionEvidenceVerifier:
    """校验开放用户任务的语法、工作区变化和 Abaqus 阶段证据。"""

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> ExecutionEvidenceVerification:
        del runner
        current_snapshot = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, _freshness = artifact_evidence_from_snapshots(
            initial_snapshot,
            current_snapshot,
        )
        fresh_artifacts = tuple(artifact for artifact in artifacts if artifact.fresh)
        artifact_by_path = {artifact.relative_path: artifact for artifact in artifacts}
        declared_paths = (
            tuple(item.relative_path for item in decision.deliverables)
            if decision is not None
            else ()
        )
        missing_paths = tuple(
            path for path in declared_paths if path not in artifact_by_path
        )
        stale_paths = tuple(
            path
            for path in declared_paths
            if path in artifact_by_path and not artifact_by_path[path].fresh
        )
        python_scripts = tuple(
            artifact.relative_path
            for artifact in fresh_artifacts
            if Path(artifact.relative_path).suffix.casefold() == ".py"
        )
        syntax_errors = tuple(
            error
            for relative_path in python_scripts
            if (
                error := _python_310_syntax_error(
                    request.workspace / relative_path,
                    relative_path,
                )
            )
            is not None
        )

        visible_executions = tuple(
            execution
            for execution in executions
            if execution.invocation in _MODEL_VISIBLE_INVOCATIONS
        )
        latest_by_invocation: dict[AbaqusInvocation, AbaqusExecutionResult] = {}
        for execution in visible_executions:
            latest_by_invocation[execution.invocation] = execution
        failed_latest = tuple(
            execution
            for execution in latest_by_invocation.values()
            if not execution.stage_succeeded
        )
        successful_invocations = tuple(
            invocation
            for invocation, execution in latest_by_invocation.items()
            if execution.stage_succeeded
        )

        common = {
            "artifacts": artifacts,
            "declared_deliverable_paths": declared_paths,
            "fresh_artifact_paths": tuple(
                artifact.relative_path for artifact in fresh_artifacts
            ),
            "checked_python_scripts": python_scripts,
            "execution_attempts": len(visible_executions),
            "latest_successful_invocations": successful_invocations,
        }
        if decision is None:
            return ExecutionEvidenceVerification(
                passed=False,
                scope="execution_evidence",
                feedback="Execution verification requires a typed deliverable manifest.",
                failure_stage="deliverables",
                **common,
            )
        if missing_paths or stale_paths:
            details: list[str] = []
            if missing_paths:
                details.append("missing: " + ", ".join(missing_paths))
            if stale_paths:
                details.append("not fresh: " + ", ".join(stale_paths))
            return ExecutionEvidenceVerification(
                passed=False,
                scope="execution_evidence",
                feedback="Declared deliverable verification failed: "
                + "; ".join(details),
                failure_stage="deliverables",
                **common,
            )
        if syntax_errors:
            return ExecutionEvidenceVerification(
                passed=False,
                scope="execution_evidence",
                feedback=(
                    "Python 3.10 syntax verification failed: "
                    + "; ".join(syntax_errors)
                ),
                failure_stage="syntax",
                **common,
            )
        if failed_latest:
            failed_names = ", ".join(
                execution.invocation.value for execution in failed_latest
            )
            return ExecutionEvidenceVerification(
                passed=False,
                scope="execution_evidence",
                feedback=(
                    "The latest execution for one or more used Abaqus stages did "
                    f"not succeed: {failed_names}. Repair and rerun only those stages."
                ),
                failure_stage="execution",
                **common,
            )
        return ExecutionEvidenceVerification(
            passed=True,
            scope="execution_evidence",
            feedback=(
                "Execution evidence accepted: declared fresh deliverables, Python 3.10 "
                "syntax, and the latest used Abaqus stages passed their deterministic "
                "checks. No task-specific physics oracle was run."
            ),
            **common,
        )


def _python_310_syntax_error(path: Path, relative_path: str) -> str | None:
    try:
        with tokenize.open(path) as stream:
            source = stream.read()
        ast.parse(
            source,
            filename=relative_path,
            mode="exec",
            feature_version=(3, 10),
        )
    except SyntaxError as exc:
        line = max(1, exc.lineno or 1)
        column = max(1, exc.offset or 1)
        return f"{relative_path}:{line}:{column}: {exc.msg}"
    except (OSError, UnicodeError) as exc:
        return f"{relative_path}: {type(exc).__name__}: {exc}"
    return None
