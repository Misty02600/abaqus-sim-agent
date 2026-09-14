from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ReadyForVerificationDecision,
)
from abaqus_sim_agent.coding.verification_port import (
    CodingRunVerifier,
    VerificationAttempt,
)
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
from abaqus_sim_agent.verification.bar_tension_3d import (
    BarTension3DMeasurements,
    BarTension3DVerification,
    verify_bar_tension_3d,
)


def bar_tension_3d_oracle_script() -> Path:
    return Path(__file__).resolve().parent / "assets" / "bar_tension_3d_oracle.py"


class BarTension3DVerificationAttempt(VerificationAttempt):
    measurements: BarTension3DMeasurements | None = None
    verification: BarTension3DVerification | None = None


class BarTension3DRunVerifier(CodingRunVerifier[BarTension3DVerificationAttempt]):
    def __init__(self, oracle_script: Path | None = None) -> None:
        path = oracle_script or bar_tension_3d_oracle_script()
        self._oracle_script = path.resolve(strict=True)

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> BarTension3DVerificationAttempt:
        del decision
        current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, freshness = artifact_evidence_from_snapshots(
            initial_snapshot, current
        )
        required = {
            "build_model.py",
            "extract_results.py",
            "bar_tension.cae",
            "bar_tension.inp",
            "bar_tension.odb",
            "results.json",
        }
        missing = sorted(required - freshness.keys())
        if missing:
            return BarTension3DVerificationAttempt(
                passed=False,
                feedback=f"Required artifacts are missing: {', '.join(missing)}",
                artifacts=artifacts,
                failure_stage="artifacts",
            )

        agent_results = _load_agent_results(request.workspace / "results.json")
        oracle_request_path = request.state_dir / "oracle_request.json"
        oracle_output_path = request.state_dir / "oracle_results.json"
        oracle_request_path.write_text(
            json.dumps(
                {"odb_path": str(request.workspace / "bar_tension.odb")},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        oracle_result = await runner.run_trusted_abaqus_python(
            self._oracle_script,
            args=(str(oracle_request_path), str(oracle_output_path)),
            workspace_artifacts=("bar_tension.odb",),
            timeout_seconds=120.0,
        )
        if not oracle_result.succeeded or not oracle_output_path.is_file():
            feedback = (
                "Independent ODB oracle failed. "
                f"exit_code={oracle_result.exit_code}; "
                f"stderr={oracle_result.stderr[-4000:]}"
            )
            return BarTension3DVerificationAttempt(
                passed=False,
                feedback=feedback,
                artifacts=artifacts,
                failure_stage="oracle",
            )

        measurements = BarTension3DMeasurements.model_validate_json(
            oracle_output_path.read_text(encoding="utf-8")
        )
        job_completed = _job_completed(request.workspace, executions)
        verification = verify_bar_tension_3d(
            measurements,
            job_completed=job_completed,
            artifact_freshness=freshness,
            agent_results=agent_results,
        )
        verification_path = request.state_dir / "verification.json"
        verification_path.write_text(
            verification.model_dump_json(indent=2),
            encoding="utf-8",
        )
        feedback = (
            "Independent physical verification passed."
            if verification.passed
            else "Independent verification failed: " + "; ".join(verification.findings)
        )
        return BarTension3DVerificationAttempt(
            passed=verification.passed,
            feedback=feedback,
            artifacts=artifacts,
            measurements=measurements,
            verification=verification,
            failure_stage=None if verification.passed else "verification",
        )


def _load_agent_results(path: Path) -> dict[str, float] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    keys = ("right_mean_u1", "mean_s11", "left_sum_rf1")
    try:
        return {key: float(value[key]) for key in keys}
    except (KeyError, TypeError, ValueError):
        return None


def _job_completed(
    workspace: Path,
    executions: Sequence[AbaqusExecutionResult],
) -> bool:
    solver_succeeded = any(
        result.invocation is AbaqusInvocation.SOLVER_JOB and result.succeeded
        for result in executions
    )
    status_path = workspace / "bar_tension.sta"
    if not status_path.is_file():
        return False
    raw = status_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    normalized = text.casefold()
    return solver_succeeded and "analysis has completed successfully" in normalized
