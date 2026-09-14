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
from abaqus_sim_agent.verification.beam_run import (
    beam_input_contract_passed,
    beam_tension_oracle_script,
)
from abaqus_sim_agent.verification.beam_section_change import (
    BeamSectionChangeOracleSpec,
    BeamSectionChangeVerification,
    verify_beam_section_change,
)
from abaqus_sim_agent.verification.beam_tension import BeamTensionMeasurements
from abaqus_sim_agent.verification.model_change_evidence import (
    ModelChangeExecutionProvenance,
    WorkspaceChangeReport,
    build_workspace_change_report,
    model_change_execution_provenance,
    status_completed,
)


class BeamSectionChangeVerificationAttempt(VerificationAttempt):
    case_id: Literal["beam_section"] = "beam_section"
    measurements: BeamTensionMeasurements | None = None
    verification: BeamSectionChangeVerification | None = None
    execution_provenance: ModelChangeExecutionProvenance | None = None
    change_report: WorkspaceChangeReport | None = None


class BeamSectionChangeRunVerifier(
    CodingRunVerifier[BeamSectionChangeVerificationAttempt]
):
    def __init__(
        self,
        *,
        source_run_id: str,
        source_run_verified: bool,
        seed_texts: Mapping[str, str],
        oracle_script: Path | None = None,
        spec: BeamSectionChangeOracleSpec | None = None,
    ) -> None:
        self._oracle_script = (oracle_script or beam_tension_oracle_script()).resolve(
            strict=True
        )
        self._source_run_id = source_run_id
        self._source_run_verified = source_run_verified
        self._seed_texts = dict(seed_texts)
        self._spec = spec or BeamSectionChangeOracleSpec()

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> BeamSectionChangeVerificationAttempt:
        del decision
        current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, freshness = artifact_evidence_from_snapshots(
            initial_snapshot,
            current,
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
            "beam_tension.cae",
            "beam_tension.inp",
            "beam_tension.odb",
            "results.json",
        }
        missing = sorted(required - freshness.keys())
        if missing:
            return BeamSectionChangeVerificationAttempt(
                passed=False,
                feedback=f"Required artifacts are missing: {', '.join(missing)}",
                artifacts=artifacts,
                change_report=change_report,
                failure_stage="artifacts",
            )

        provenance = model_change_execution_provenance(
            executions,
            current,
            artifact_stem="beam_tension",
        )
        oracle_request_path = request.state_dir / "beam_section_oracle_request.json"
        oracle_output_path = request.state_dir / "beam_section_oracle_results.json"
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
            return BeamSectionChangeVerificationAttempt(
                passed=False,
                feedback=(
                    "Independent B31 section-change ODB oracle failed. "
                    f"exit_code={oracle_result.exit_code}; "
                    f"stderr={oracle_result.stderr[-4000:]}"
                ),
                artifacts=artifacts,
                execution_provenance=provenance,
                change_report=change_report,
                failure_stage="oracle",
            )

        measurements = BeamTensionMeasurements.model_validate_json(
            oracle_output_path.read_text(encoding="utf-8")
        )
        build_script_changed = freshness.get("build_model.py") is True and bool(
            change_report.build_model_patch
        )
        extractor_preserved = (
            freshness.get("extract_results.py") is False
            and not change_report.extract_results_patch
        )
        verification = verify_beam_section_change(
            measurements,
            job_completed=(
                provenance.solver_generated_current_odb
                and status_completed(request.workspace / "beam_tension.sta")
            ),
            artifact_freshness=freshness,
            source_run_verified=self._source_run_verified,
            build_script_changed=build_script_changed,
            extractor_preserved=extractor_preserved,
            execution_provenance_passed=provenance.passed,
            agent_results=_load_agent_results(request.workspace / "results.json"),
            input_contract_passed=beam_input_contract_passed(
                request.workspace / "beam_tension.inp"
            ),
            spec=self._spec,
        )
        (request.state_dir / "beam_section_change_verification.json").write_text(
            verification.model_dump_json(indent=2),
            encoding="utf-8",
        )
        feedback = (
            "Independent B31 section-change and regression verification passed."
            if verification.passed
            else "Independent B31 section-change verification failed: "
            + "; ".join(verification.findings)
        )
        return BeamSectionChangeVerificationAttempt(
            passed=verification.passed,
            feedback=feedback,
            artifacts=artifacts,
            measurements=measurements,
            verification=verification,
            execution_provenance=provenance,
            change_report=change_report,
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
