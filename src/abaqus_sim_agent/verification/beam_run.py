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
from abaqus_sim_agent.verification.beam_tension import (
    BeamTensionMeasurements,
    BeamTensionVerification,
    verify_beam_tension,
)

_FORBIDDEN_LOAD_KEYWORDS = {
    "*body force",
    "*cflux",
    "*cload",
    "*connector load",
    "*dflux",
    "*dload",
    "*dsflux",
    "*dsload",
    "*film",
    "*foundation",
    "*radiate",
}


def beam_tension_oracle_script() -> Path:
    return Path(__file__).resolve().parent / "assets" / "beam_tension_oracle.py"


class BeamTensionVerificationAttempt(VerificationAttempt):
    measurements: BeamTensionMeasurements | None = None
    verification: BeamTensionVerification | None = None


class BeamTensionRunVerifier(CodingRunVerifier[BeamTensionVerificationAttempt]):
    def __init__(self, oracle_script: Path | None = None) -> None:
        path = oracle_script or beam_tension_oracle_script()
        self._oracle_script = path.resolve(strict=True)

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> BeamTensionVerificationAttempt:
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
            "beam_tension.cae",
            "beam_tension.inp",
            "beam_tension.odb",
            "results.json",
        }
        missing = sorted(required - freshness.keys())
        if missing:
            return BeamTensionVerificationAttempt(
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
                {"odb_path": str(request.workspace / "beam_tension.odb")},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        oracle_result = await runner.run_trusted_abaqus_python(
            self._oracle_script,
            args=(str(oracle_request_path), str(oracle_output_path)),
            workspace_artifacts=("beam_tension.odb",),
            timeout_seconds=120.0,
        )
        if not oracle_result.succeeded or not oracle_output_path.is_file():
            feedback = (
                "Independent ODB oracle failed. "
                f"exit_code={oracle_result.exit_code}; "
                f"stderr={oracle_result.stderr[-4000:]}"
            )
            return BeamTensionVerificationAttempt(
                passed=False,
                feedback=feedback,
                artifacts=artifacts,
                failure_stage="oracle",
            )

        measurements = BeamTensionMeasurements.model_validate_json(
            oracle_output_path.read_text(encoding="utf-8")
        )
        job_completed = _job_completed(request.workspace, executions)
        verification = verify_beam_tension(
            measurements,
            job_completed=job_completed,
            artifact_freshness=freshness,
            agent_results=agent_results,
            input_contract_passed=beam_input_contract_passed(
                request.workspace / "beam_tension.inp"
            ),
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
        return BeamTensionVerificationAttempt(
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
    keys = ("right_mean_u1", "left_sum_rf1", "mean_sf1")
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
    status_path = workspace / "beam_tension.sta"
    if not status_path.is_file():
        return False
    raw = status_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    normalized = text.casefold()
    return solver_succeeded and "analysis has completed successfully" in normalized


def beam_input_contract_passed(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")

    keyword = ""
    step_headers: list[str] = []
    static_count = 0
    forbidden_load_found = False
    boundary_rows: list[tuple[str, ...]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            keyword = line.split(",", 1)[0].strip().casefold()
            if keyword == "*step":
                step_headers.append(line.upper().replace(" ", ""))
            elif keyword == "*static":
                static_count += 1
            elif keyword in _FORBIDDEN_LOAD_KEYWORDS:
                forbidden_load_found = True
            continue
        if keyword == "*boundary":
            boundary_rows.append(
                tuple(part.strip() for part in line.split(",") if part.strip())
            )

    if (
        len(step_headers) != 1
        or "NLGEOM=NO" not in step_headers[0]
        or static_count != 1
        or len(boundary_rows) != 2
        or forbidden_load_found
    ):
        return False
    encastre_rows = [
        row for row in boundary_rows if len(row) == 2 and row[1].upper() == "ENCASTRE"
    ]
    displacement_rows = [row for row in boundary_rows if _is_right_u1_row(row)]
    return (
        len(encastre_rows) == 1
        and len(displacement_rows) == 1
        and encastre_rows[0][0].casefold() != displacement_rows[0][0].casefold()
    )


def _is_right_u1_row(row: tuple[str, ...]) -> bool:
    if len(row) != 4:
        return False
    try:
        first_dof = int(row[1])
        last_dof = int(row[2])
        magnitude = float(row[3])
    except ValueError:
        return False
    return first_dof == last_dof == 1 and abs(magnitude - 0.1) <= 1.0e-12
