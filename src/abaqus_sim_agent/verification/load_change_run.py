from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

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
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult
from abaqus_sim_agent.verification.load_change import (
    LoadModeInputContract,
    LoadModeMeasurements,
    LoadModeOracleSpec,
    LoadModeVerification,
    parse_load_mode_input,
    verify_load_mode_change,
)
from abaqus_sim_agent.verification.model_change_evidence import (
    ModelChangeExecutionProvenance,
    WorkspaceChangeReport,
    build_workspace_change_report,
    model_change_execution_provenance,
    status_completed,
)


class LoadModeChangeVerificationAttempt(VerificationAttempt):
    case_id: Literal["load_mode"] = "load_mode"
    measurements: LoadModeMeasurements | None = None
    input_contract: LoadModeInputContract | None = None
    verification: LoadModeVerification | None = None
    execution_provenance: ModelChangeExecutionProvenance | None = None
    change_report: WorkspaceChangeReport | None = None


class LoadModeChangeRunVerifier(CodingRunVerifier[LoadModeChangeVerificationAttempt]):
    def __init__(
        self,
        oracle_script: Path,
        *,
        source_run_id: str,
        source_run_verified: bool,
        seed_texts: Mapping[str, str],
        spec: LoadModeOracleSpec | None = None,
    ) -> None:
        self._oracle_script = oracle_script.resolve(strict=True)
        self._source_run_id = source_run_id
        self._source_run_verified = source_run_verified
        self._seed_texts = dict(seed_texts)
        self._spec = spec or LoadModeOracleSpec()

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> LoadModeChangeVerificationAttempt:
        del decision
        current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, freshness = artifact_evidence_from_snapshots(
            initial_snapshot, current
        )
        change_report = build_workspace_change_report(
            initial_snapshot,
            current,
            source_run_id=self._source_run_id,
            seed_texts=self._seed_texts,
            workspace=request.workspace,
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
            return LoadModeChangeVerificationAttempt(
                passed=False,
                feedback=f"Required artifacts are missing: {', '.join(missing)}",
                artifacts=artifacts,
                change_report=change_report,
                failure_stage="artifacts",
            )
        try:
            input_contract = parse_load_mode_input(
                request.workspace / "plate_tension.inp"
            )
        except (OSError, UnicodeError, ValueError) as exc:
            return LoadModeChangeVerificationAttempt(
                passed=False,
                feedback=f"Host INP contract parsing failed: {exc}",
                artifacts=artifacts,
                change_report=change_report,
                failure_stage="verification",
            )
        if len(input_contract.traction_records) != 1:
            return LoadModeChangeVerificationAttempt(
                passed=False,
                feedback=(
                    "INP traction record count mismatch: expected exactly one "
                    "general *DSLOAD traction record, got "
                    f"{len(input_contract.traction_records)}."
                ),
                artifacts=artifacts,
                input_contract=input_contract,
                change_report=change_report,
                failure_stage="verification",
            )
        provenance = model_change_execution_provenance(executions, current)
        agent_results = _load_agent_results(request.workspace / "results.json")
        traction = input_contract.traction_records[0]
        oracle_request_path = request.state_dir / "load_change_oracle_request.json"
        oracle_output_path = request.state_dir / "load_change_oracle_results.json"
        oracle_request_path.write_text(
            json.dumps(
                {
                    "odb_path": str(request.workspace / "plate_tension.odb"),
                    "surface_name": traction.surface_name,
                },
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
            return LoadModeChangeVerificationAttempt(
                passed=False,
                feedback=(
                    "Independent load-mode ODB oracle failed. "
                    f"exit_code={oracle_result.exit_code}; "
                    f"stderr={oracle_result.stderr[-4000:]}"
                ),
                artifacts=artifacts,
                input_contract=input_contract,
                execution_provenance=provenance,
                change_report=change_report,
                failure_stage="oracle",
            )
        measurements = LoadModeMeasurements.model_validate_json(
            oracle_output_path.read_text(encoding="utf-8")
        )
        verification = verify_load_mode_change(
            measurements,
            input_contract,
            job_completed=(
                provenance.solver_generated_current_odb
                and status_completed(request.workspace / "plate_tension.sta")
            ),
            artifact_freshness=freshness,
            source_run_verified=self._source_run_verified,
            agent_results=agent_results,
            spec=self._spec,
        )
        (request.state_dir / "load_change_verification.json").write_text(
            verification.model_dump_json(indent=2), encoding="utf-8"
        )
        findings = list(verification.findings)
        if not provenance.cae_generated_current_model:
            findings.append("no successful CAE call produced the current CAE and INP")
        if not provenance.cae_did_not_solve:
            findings.append("a CAE noGUI call generated solver artifacts")
        if not provenance.solver_generated_current_odb:
            findings.append(
                "no linked plate_tension.inp Solver call produced the current ODB"
            )
        if not provenance.extraction_generated_current_results:
            findings.append(
                "no standalone extract_results.py call produced the current results.json"
            )
        passed = verification.passed and provenance.passed
        feedback = (
            "Independent load-mode and regression verification passed."
            if passed
            else "Independent load-mode verification failed: " + "; ".join(findings)
        )
        return LoadModeChangeVerificationAttempt(
            passed=passed,
            feedback=feedback,
            artifacts=artifacts,
            measurements=measurements,
            input_contract=input_contract,
            verification=verification,
            execution_provenance=provenance,
            change_report=change_report,
            failure_stage=None if passed else "verification",
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
