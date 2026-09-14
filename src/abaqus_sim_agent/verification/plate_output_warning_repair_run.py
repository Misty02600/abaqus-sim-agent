from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field

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
from abaqus_sim_agent.verification.assets import plate_tension_oracle_script
from abaqus_sim_agent.verification.model_change_evidence import (
    ModelChangeExecutionProvenance,
    model_change_execution_provenance,
    status_completed,
)
from abaqus_sim_agent.verification.plate_change import (
    PlateHoleInputContract,
    parse_plate_hole_input,
)
from abaqus_sim_agent.verification.plate_tension import (
    PlateTensionMeasurements,
    PlateTensionVerification,
    verify_plate_tension,
)

RF3_INACTIVE_WARNING = "OUTPUT REQUEST RF3 IS NOT VALID"
_RF3_FAULT = re.compile(r"variables\s*=\s*\(\s*['\"]RF3['\"]", re.IGNORECASE)
_HISTORY_OVERRIDE = re.compile(
    r"\n\nmodel\.historyOutputRequests\[['\"]H-Output-1['\"]\]\.setValues\(\s*"
    r"region=(?:leftSet|left_set)\s*,\s*variables=\((?P<variables>[^)]*)\)\s*\)",
    re.IGNORECASE,
)
_ACTIVE_HISTORY_VARIABLES = frozenset({"RF1", "RF2"})


class PlateOutputWarningRepairVerificationAttempt(VerificationAttempt):
    deliverable_manifest_passed: bool = False
    baseline_warning_reproduced: bool = False
    final_warning_removed: bool = False
    build_repaired: bool = False
    repair_kind: Literal["deleted_override", "active_rf_override"] | None = None
    extractor_preserved: bool = False
    input_contract_passed: bool = False
    ordered_runtime_passed: bool = False
    sidecar_free: bool = False
    execution_provenance: ModelChangeExecutionProvenance | None = None
    input_contract: PlateHoleInputContract | None = None
    measurements: PlateTensionMeasurements | None = None
    physical_verification: PlateTensionVerification | None = None
    baseline_warning_execution_index: int | None = Field(default=None, ge=0)
    clean_solver_execution_index: int | None = Field(default=None, ge=0)


class PlateOutputWarningRepairRunVerifier(
    CodingRunVerifier[PlateOutputWarningRepairVerificationAttempt]
):
    def __init__(
        self,
        *,
        defective_build: bytes,
        source_build: bytes,
        source_extractor: bytes,
    ) -> None:
        self._defective_build = defective_build
        self._source_build = source_build
        self._defective_build_sha256 = hashlib.sha256(defective_build).hexdigest()
        self._source_extractor = source_extractor
        self._source_extractor_sha256 = hashlib.sha256(source_extractor).hexdigest()
        self._oracle_script = plate_tension_oracle_script().resolve(strict=True)

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> PlateOutputWarningRepairVerificationAttempt:
        current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, freshness = artifact_evidence_from_snapshots(
            initial_snapshot,
            current,
        )
        required = {
            "build_model.py",
            "extract_results.py",
            "plate_tension.cae",
            "plate_tension.inp",
            "plate_tension.odb",
            "results.json",
        }
        required_deliverables = required - {"extract_results.py"}
        declared = (
            {item.relative_path for item in decision.deliverables}
            if decision is not None
            else set()
        )
        deliverable_manifest_passed = required_deliverables.issubset(declared) and all(
            freshness.get(path, False) for path in declared
        )
        if missing := sorted(required - freshness.keys()):
            return PlateOutputWarningRepairVerificationAttempt(
                passed=False,
                feedback=f"Required artifacts are missing: {', '.join(missing)}",
                artifacts=artifacts,
                deliverable_manifest_passed=deliverable_manifest_passed,
                failure_stage="artifacts",
            )

        build_sha256 = current.by_path()["build_model.py"].sha256
        build_text = (request.workspace / "build_model.py").read_text(encoding="utf-8")
        repair_kind = _repair_kind(
            build_text,
            source_text=self._source_build.decode("utf-8"),
            defective_text=self._defective_build.decode("utf-8"),
        )
        build_repaired = repair_kind is not None
        extractor_preserved = (
            request.workspace.joinpath("extract_results.py").read_bytes()
            == self._source_extractor
        )
        sidecar_free = _sidecar_free(current, executions)
        try:
            input_contract = parse_plate_hole_input(
                request.workspace / "plate_tension.inp"
            )
        except (OSError, UnicodeError, ValueError) as exc:
            return PlateOutputWarningRepairVerificationAttempt(
                passed=False,
                feedback=f"Host INP contract parsing failed: {exc}",
                artifacts=artifacts,
                deliverable_manifest_passed=deliverable_manifest_passed,
                build_repaired=build_repaired,
                repair_kind=repair_kind,
                extractor_preserved=extractor_preserved,
                sidecar_free=sidecar_free,
                failure_stage="verification",
            )
        input_contract_passed = _input_contract_passed(input_contract)
        warning_indices = [
            index
            for index, execution in enumerate(executions)
            if execution.invocation is AbaqusInvocation.SOLVER_JOB
            and _has_target_warning(execution)
        ]
        solver_indices = [
            index
            for index, execution in enumerate(executions)
            if execution.invocation is AbaqusInvocation.SOLVER_JOB
        ]
        warning_index = warning_indices[0] if warning_indices else None
        final_solver_index = solver_indices[-1] if solver_indices else None
        extraction_indices = [
            index
            for index, execution in enumerate(executions)
            if execution.invocation is AbaqusInvocation.ABAQUS_PYTHON
            and execution.stage_succeeded
        ]
        final_extraction_index = extraction_indices[-1] if extraction_indices else None
        final_solver_clean = (
            final_solver_index is not None
            and executions[final_solver_index].stage_succeeded
            and not _has_target_warning(executions[final_solver_index])
            and _produced_current_odb(
                executions[final_solver_index],
                current.by_path()["plate_tension.odb"].sha256,
            )
        )
        baseline_lineage_passed = _has_cae_solver_lineage(
            executions,
            build_sha256=self._defective_build_sha256,
            solver_index=warning_index,
            lower_bound=-1,
        )
        repair_lineage_passed = _has_cae_solver_lineage(
            executions,
            build_sha256=build_sha256,
            solver_index=final_solver_index,
            lower_bound=warning_index if warning_index is not None else -1,
        )
        extraction_input_passed = (
            final_extraction_index is not None
            and _input_sha256(
                executions[final_extraction_index],
                "extract_results.py",
            )
            == self._source_extractor_sha256
        )
        solver_lineage_builds = [
            _matching_cae_build_sha256(executions, solver_index)
            for solver_index in solver_indices
        ]
        all_solver_inputs_from_cae = bool(solver_lineage_builds) and all(
            value is not None for value in solver_lineage_builds
        )
        all_cae_inputs_allowed = all(
            _input_sha256(execution, "build_model.py") is not None
            for execution in executions
            if execution.invocation is AbaqusInvocation.CAE_NOGUI
        )
        all_extractor_inputs_allowed = all(
            _input_sha256(execution, "extract_results.py")
            == self._source_extractor_sha256
            for execution in executions
            if execution.invocation is AbaqusInvocation.ABAQUS_PYTHON
        )
        ordered_runtime_passed = (
            warning_index is not None
            and warning_index == 1
            and len(executions) >= 2
            and executions[0].invocation is AbaqusInvocation.CAE_NOGUI
            and final_solver_clean
            and final_solver_index is not None
            and final_extraction_index is not None
            and baseline_lineage_passed
            and repair_lineage_passed
            and extraction_input_passed
            and all_solver_inputs_from_cae
            and all_cae_inputs_allowed
            and all_extractor_inputs_allowed
            and warning_index < final_solver_index < final_extraction_index
            and final_extraction_index == len(executions) - 1
        )
        provenance = model_change_execution_provenance(executions, current)
        try:
            measurements, physical = await self._physical_verification(
                request,
                runner,
                freshness,
                job_completed=(
                    provenance.solver_generated_current_odb
                    and status_completed(request.workspace / "plate_tension.sta")
                ),
            )
        except RuntimeError as exc:
            return PlateOutputWarningRepairVerificationAttempt(
                passed=False,
                feedback=f"Independent plate oracle failed: {exc}",
                artifacts=artifacts,
                deliverable_manifest_passed=deliverable_manifest_passed,
                baseline_warning_reproduced=warning_index is not None,
                final_warning_removed=final_solver_clean,
                build_repaired=build_repaired,
                repair_kind=repair_kind,
                extractor_preserved=extractor_preserved,
                sidecar_free=sidecar_free,
                input_contract_passed=input_contract_passed,
                ordered_runtime_passed=ordered_runtime_passed,
                execution_provenance=provenance,
                input_contract=input_contract,
                baseline_warning_execution_index=warning_index,
                clean_solver_execution_index=(
                    final_solver_index if final_solver_clean else None
                ),
                failure_stage="oracle",
            )
        checks = {
            "ready decision did not declare every fresh user deliverable": (
                deliverable_manifest_passed
            ),
            "baseline Solver warning was not reproduced": warning_index is not None,
            "the final Solver evidence still contains the RF3 warning": (
                final_solver_clean
            ),
            "build_model.py was not repaired from the defective seed": build_repaired,
            "extract_results.py changed from the verified source": extractor_preserved,
            "an executable workspace sidecar was created or modified": sidecar_free,
            "the preserved plate INP contract did not pass": input_contract_passed,
            "runtime order did not show warning then clean Solver then extraction": (
                ordered_runtime_passed
            ),
            "the current artifacts lack linked CAE/Solver/extraction provenance": (
                provenance.passed
            ),
            "the independent plate physical oracle did not pass": physical.passed,
        }
        findings = [message for message, passed in checks.items() if not passed]
        passed = not findings
        verification_path = (
            request.state_dir / "plate_output_warning_repair_verification.json"
        )
        attempt = PlateOutputWarningRepairVerificationAttempt(
            passed=passed,
            feedback=(
                "Independent output-warning repair verification passed."
                if passed
                else "Independent repair verification failed: " + "; ".join(findings)
            ),
            artifacts=artifacts,
            deliverable_manifest_passed=deliverable_manifest_passed,
            baseline_warning_reproduced=warning_index is not None,
            final_warning_removed=final_solver_clean,
            build_repaired=build_repaired,
            repair_kind=repair_kind,
            extractor_preserved=extractor_preserved,
            sidecar_free=sidecar_free,
            input_contract_passed=input_contract_passed,
            ordered_runtime_passed=ordered_runtime_passed,
            execution_provenance=provenance,
            input_contract=input_contract,
            measurements=measurements,
            physical_verification=physical,
            baseline_warning_execution_index=warning_index,
            clean_solver_execution_index=(
                final_solver_index if final_solver_clean else None
            ),
            failure_stage=None if passed else "verification",
        )
        verification_path.write_text(
            attempt.model_dump_json(indent=2), encoding="utf-8"
        )
        return attempt

    async def _physical_verification(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        freshness: Mapping[str, bool],
        *,
        job_completed: bool,
    ) -> tuple[PlateTensionMeasurements, PlateTensionVerification]:
        oracle_request = request.state_dir / "warning_repair_oracle_request.json"
        oracle_output = request.state_dir / "warning_repair_oracle_results.json"
        oracle_request.write_text(
            json.dumps(
                {"odb_path": str(request.workspace / "plate_tension.odb")},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        result = await runner.run_trusted_abaqus_python(
            self._oracle_script,
            args=(str(oracle_request), str(oracle_output)),
            workspace_artifacts=("plate_tension.odb",),
            timeout_seconds=120.0,
        )
        if not result.succeeded or not oracle_output.is_file():
            raise RuntimeError(
                "independent plate oracle failed: "
                f"exit_code={result.exit_code}; stderr={result.stderr[-1000:]}"
            )
        measurements = PlateTensionMeasurements.model_validate_json(
            oracle_output.read_text(encoding="utf-8")
        )
        verification_freshness = dict(freshness)
        verification_freshness["extract_results.py"] = True
        physical = verify_plate_tension(
            measurements,
            job_completed=job_completed,
            artifact_freshness=verification_freshness,
            agent_results=_load_agent_results(request.workspace / "results.json"),
        )
        return measurements, physical


def _input_contract_passed(contract: PlateHoleInputContract) -> bool:
    return (
        contract.element_types == ("CPS4R",)
        and contract.step_count == 1
        and contract.static_step_count == 1
        and contract.nlgeom_off
        and contract.mechanical_load_keywords == ()
        and set(contract.field_output_variables) == {"RF", "S", "U"}
        and set(contract.history_output_variables).issubset({"RF1", "RF2"})
        and sorted(contract.boundary_records)
        == sorted(((1, 1, 0.0), (2, 2, 0.0), (1, 1, 0.1)))
        and contract.elastic_modulus == 210_000.0
        and contract.poisson_ratio == 0.3
        and contract.thickness == 1.0
    )


def _allowed_build_repair(
    final_text: str,
    *,
    source_text: str,
    defective_text: str,
) -> bool:
    return (
        _repair_kind(
            final_text,
            source_text=source_text,
            defective_text=defective_text,
        )
        is not None
    )


def _repair_kind(
    final_text: str,
    *,
    source_text: str,
    defective_text: str,
) -> Literal["deleted_override", "active_rf_override"] | None:
    final_text = _normalized_newlines(final_text)
    source_text = _normalized_newlines(source_text)
    defective_text = _normalized_newlines(defective_text)
    if _same_python_program(final_text, source_text):
        return "deleted_override"
    if final_text == defective_text or _RF3_FAULT.search(final_text):
        return None
    match = _HISTORY_OVERRIDE.search(final_text)
    if match is None:
        return None
    variables = {
        value.strip().strip("'\"").upper()
        for value in match.group("variables").split(",")
        if value.strip()
    }
    if not variables or not variables.issubset(_ACTIVE_HISTORY_VARIABLES):
        return None
    without_override = final_text[: match.start()] + final_text[match.end() :]
    return (
        "active_rf_override"
        if _same_python_program(without_override, source_text)
        else None
    )


def _normalized_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _same_python_program(left: str, right: str) -> bool:
    try:
        left_tree = ast.parse(left, feature_version=(3, 10))
        right_tree = ast.parse(right, feature_version=(3, 10))
    except SyntaxError:
        return False
    return ast.dump(left_tree, include_attributes=False) == ast.dump(
        right_tree,
        include_attributes=False,
    )


def _has_target_warning(execution: AbaqusExecutionResult) -> bool:
    return any(
        diagnostic.level == "warning"
        and RF3_INACTIVE_WARNING.casefold() in diagnostic.message.casefold()
        for diagnostic in execution.diagnostics
    )


def _produced_current_odb(
    execution: AbaqusExecutionResult,
    current_sha256: str,
) -> bool:
    return any(
        change.path == "plate_tension.odb"
        and change.after is not None
        and change.after.sha256 == current_sha256
        for change in execution.artifact_changes
    )


def _has_cae_solver_lineage(
    executions: Sequence[AbaqusExecutionResult],
    *,
    build_sha256: str,
    solver_index: int | None,
    lower_bound: int,
) -> bool:
    if solver_index is None:
        return False
    solver_input_sha256 = _input_sha256(
        executions[solver_index],
        "plate_tension.inp",
    )
    if solver_input_sha256 is None:
        return False
    return any(
        lower_bound < index < solver_index
        and execution.invocation is AbaqusInvocation.CAE_NOGUI
        and execution.stage_succeeded
        and _input_sha256(execution, "build_model.py") == build_sha256
        and _output_sha256(execution, "plate_tension.inp") == solver_input_sha256
        for index, execution in enumerate(executions)
    )


def _matching_cae_build_sha256(
    executions: Sequence[AbaqusExecutionResult],
    solver_index: int,
) -> str | None:
    solver_input_sha256 = _input_sha256(
        executions[solver_index],
        "plate_tension.inp",
    )
    if solver_input_sha256 is None:
        return None
    return next(
        (
            _input_sha256(execution, "build_model.py")
            for index, execution in reversed(
                tuple(enumerate(executions[:solver_index]))
            )
            if execution.invocation is AbaqusInvocation.CAE_NOGUI
            and execution.stage_succeeded
            and _output_sha256(execution, "plate_tension.inp") == solver_input_sha256
        ),
        None,
    )


def _input_sha256(
    execution: AbaqusExecutionResult,
    path: str,
) -> str | None:
    return next(
        (
            fingerprint.sha256
            for fingerprint in execution.input_fingerprints
            if fingerprint.path == path
        ),
        None,
    )


def _output_sha256(
    execution: AbaqusExecutionResult,
    path: str,
) -> str | None:
    return next(
        (
            change.after.sha256
            for change in execution.artifact_changes
            if change.path == path and change.after is not None
        ),
        None,
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


def _sidecar_free(
    current: WorkspaceSnapshot,
    executions: Sequence[AbaqusExecutionResult],
) -> bool:
    paths = {
        *(item.relative_path for item in current.files),
        *(
            change.path
            for execution in executions
            for change in execution.artifact_changes
        ),
    }
    return not any(_is_executable_sidecar(path) for path in paths)


def _is_executable_sidecar(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold()
    name = normalized.rsplit("/", maxsplit=1)[-1]
    if normalized in {"build_model.py", "extract_results.py"}:
        return False
    return name.endswith(".py") or name in {
        "abaqus_v6.env",
        "sitecustomize.py",
        "usercustomize.py",
    }
