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
from abaqus_sim_agent.verification.plate_tension import (
    PlateTensionMeasurements,
    PlateTensionOracleSpec,
    PlateTensionVerification,
    verify_plate_tension,
)


class PlateTensionVerificationAttempt(VerificationAttempt):
    measurements: PlateTensionMeasurements | None = None
    verification: PlateTensionVerification | None = None


class PlateTensionRunVerifier(CodingRunVerifier[PlateTensionVerificationAttempt]):
    def __init__(
        self,
        oracle_script: Path,
        spec: PlateTensionOracleSpec | None = None,
    ) -> None:
        self._oracle_script = oracle_script.resolve(strict=True)
        self._spec = spec

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> PlateTensionVerificationAttempt:
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
            "plate_tension.cae",
            "plate_tension.inp",
            "plate_tension.odb",
            "results.json",
        }
        missing = sorted(required - freshness.keys())
        if missing:
            return PlateTensionVerificationAttempt(
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
                _build_oracle_request(request.workspace / "plate_tension.odb"),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        oracle_result = await runner.run_trusted_abaqus_python(
            self._oracle_script,
            args=(str(oracle_request_path), str(oracle_output_path)),
            workspace_artifacts=("plate_tension.odb",),
            timeout_seconds=120.0,
        )
        if not oracle_result.succeeded or not oracle_output_path.is_file():
            feedback = (
                "Independent ODB oracle failed. "
                f"exit_code={oracle_result.exit_code}; "
                f"stderr={oracle_result.stderr[-4000:]}"
            )
            return PlateTensionVerificationAttempt(
                passed=False,
                feedback=feedback,
                artifacts=artifacts,
                failure_stage="oracle",
            )

        measurements = PlateTensionMeasurements.model_validate_json(
            oracle_output_path.read_text(encoding="utf-8")
        )
        job_completed = _job_completed(request.workspace, executions)
        verification = verify_plate_tension(
            measurements,
            job_completed=job_completed,
            artifact_freshness=freshness,
            agent_results=agent_results,
            spec=self._spec,
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
        return PlateTensionVerificationAttempt(
            passed=verification.passed,
            feedback=feedback,
            artifacts=artifacts,
            measurements=measurements,
            verification=verification,
            failure_stage=None if verification.passed else "verification",
        )


def _build_oracle_request(odb_path: Path) -> dict[str, str]:
    return {"odb_path": str(odb_path)}


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
    status_path = workspace / "plate_tension.sta"
    if not status_path.is_file():
        return False
    raw = status_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    normalized = text.casefold()
    return solver_succeeded and "analysis has completed successfully" in normalized
