from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal, cast

import pytest

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    DeliverableDeclaration,
    ReadyForVerificationDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusDiagnostic,
    AbaqusExecutionResult,
    AbaqusInvocation,
    ArtifactChange,
    ArtifactChangeKind,
    ArtifactFingerprint,
)
from abaqus_sim_agent.verification.plate_output_warning_repair_run import (
    PlateOutputWarningRepairRunVerifier,
)
from abaqus_sim_agent.verification.plate_tension import PlateTensionMeasurements

_SOURCE_BUILD = b"value = 'source'\n"
_DEFECTIVE_BUILD = (
    _SOURCE_BUILD
    + b"\nmodel.historyOutputRequests['H-Output-1'].setValues(\n"
    + b"    region=left_set, variables=('RF3',))\n"
)
_EXTRACTOR = b"print('extract')\n"
_WARNING = (
    "***WARNING: OUTPUT REQUEST RF3 IS NOT VALID SINCE IT REQUESTS OUTPUT "
    "AT A DOF THAT IS NOT ACTIVE"
)


class _OracleRunner:
    async def run_trusted_abaqus_python(
        self,
        _script: Path,
        *,
        args: tuple[str, ...] = (),
        workspace_artifacts: tuple[str, ...] = (),
        timeout_seconds: float = 120.0,
    ) -> AbaqusExecutionResult:
        del workspace_artifacts, timeout_seconds
        output = Path(args[1])
        content = PlateTensionMeasurements(
            odb_path=args[0],
            step_name="Step-1",
            frame_index=1,
            instance_name="PLATE-1",
            element_type_counts={"CPS4R": 80},
            right_mean_u1=0.1,
            mean_s11=210.0,
            left_sum_rf1=-4200.0,
        ).model_dump_json()
        await asyncio.to_thread(output.write_text, content, encoding="utf-8")
        return AbaqusExecutionResult(
            invocation=AbaqusInvocation.TRUSTED_ABAQUS_PYTHON,
            command=("abq2024.bat", "python", "oracle.py"),
            workspace=output.parent,
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            stdout="ok",
            stderr="",
            stdout_encoding="utf-8",
            stderr_encoding="utf-8",
            stdout_raw_path=output.parent / "oracle.stdout",
            stderr_raw_path=output.parent / "oracle.stderr",
        )


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fingerprint(path: str, content: bytes) -> ArtifactFingerprint:
    return ArtifactFingerprint(
        path=path,
        size_bytes=len(content),
        sha256=_sha(content),
    )


def _change(path: str, content: bytes) -> ArtifactChange:
    return ArtifactChange(
        kind=ArtifactChangeKind.CREATED,
        path=path,
        after=_fingerprint(path, content),
    )


def _execution(
    workspace: Path,
    state_dir: Path,
    invocation: AbaqusInvocation,
    *,
    input_path: str,
    input_content: bytes,
    outputs: tuple[tuple[str, bytes], ...] = (),
    warning: bool = False,
) -> AbaqusExecutionResult:
    command = {
        AbaqusInvocation.CAE_NOGUI: (
            "abq2024.bat",
            "cae",
            "noGUI=build_model.py",
        ),
        AbaqusInvocation.SOLVER_JOB: (
            "abq2024.bat",
            "job=plate_tension",
            "input=plate_tension.inp",
            "interactive",
        ),
        AbaqusInvocation.ABAQUS_PYTHON: (
            "abq2024.bat",
            "python",
            "extract_results.py",
        ),
    }[invocation]
    return AbaqusExecutionResult(
        invocation=invocation,
        command=command,
        workspace=workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
        stdout="ok",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=state_dir / f"{len(command)}.stdout",
        stderr_raw_path=state_dir / f"{len(command)}.stderr",
        input_fingerprints=(_fingerprint(input_path, input_content),),
        artifact_changes=tuple(_change(path, content) for path, content in outputs),
        diagnostics=(
            (
                AbaqusDiagnostic(
                    level="warning",
                    source="plate_tension.dat",
                    line=1,
                    message=_WARNING,
                ),
            )
            if warning
            else ()
        ),
    )


def _valid_inp(*, history: str = "") -> bytes:
    return (
        "*Heading\n"
        "*Element, type=CPS4R\n1, 1, 2, 3, 4\n"
        "*Solid Section, elset=Plate, material=Steel\n1.0,\n"
        "*Material, name=Steel\n*Elastic\n210000.0, 0.3\n"
        "*Boundary\nLeft, 1, 1\n*Boundary\nPin, 2, 2\n"
        "*Step, name=Step-1, nlgeom=NO\n*Static\n1.0, 1.0\n"
        "*Boundary\nRight, 1, 1, 0.1\n"
        "*Output, field\n*Node Output\nRF, U\n*Element Output\nS\n"
        f"{history}"
        "*End Step\n"
    ).encode()


def _run_scenario(
    tmp_path: Path,
    *,
    attack: Literal[
        "none",
        "dirty_final_solver",
        "broken_solver_lineage",
        "wrong_final_build",
        "missing_deliverable",
        "sidecar",
        "pre_solve_before_warning",
    ] = "none",
    active_history_repair: bool = False,
) -> bool:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir(parents=True)
    state_dir.mkdir()
    (workspace / "build_model.py").write_bytes(_DEFECTIVE_BUILD)
    (workspace / "extract_results.py").write_bytes(_EXTRACTOR)
    initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)

    if active_history_repair:
        final_build = _DEFECTIVE_BUILD.replace(b"'RF3'", b"'RF1'")
        history = "*Output, history\n*Node Output\nRF1\n"
    else:
        final_build = _SOURCE_BUILD
        history = ""
    if attack == "wrong_final_build":
        final_build += b"geometry = 'changed'\n"
    final_inp = _valid_inp(history=history)
    final_cae = b"cae"
    final_odb = b"odb"
    final_results = json.dumps(
        {
            "right_mean_u1": 0.1,
            "mean_s11": 210.0,
            "left_sum_rf1": -4200.0,
        }
    ).encode()
    for path, content in {
        "build_model.py": final_build,
        "plate_tension.cae": final_cae,
        "plate_tension.inp": final_inp,
        "plate_tension.odb": final_odb,
        "results.json": final_results,
        "plate_tension.sta": b"THE ANALYSIS HAS COMPLETED SUCCESSFULLY\n",
    }.items():
        (workspace / path).write_bytes(content)
    if attack == "sidecar":
        (workspace / "json.py").write_text("print('sidecar')\n", encoding="utf-8")

    baseline_inp = b"*Output, history\n*Node Output\nRF3\n"
    executions = [
        _execution(
            workspace,
            state_dir,
            AbaqusInvocation.CAE_NOGUI,
            input_path="build_model.py",
            input_content=_DEFECTIVE_BUILD,
            outputs=(("plate_tension.inp", baseline_inp),),
        ),
        _execution(
            workspace,
            state_dir,
            AbaqusInvocation.SOLVER_JOB,
            input_path="plate_tension.inp",
            input_content=baseline_inp,
            warning=True,
        ),
        _execution(
            workspace,
            state_dir,
            AbaqusInvocation.CAE_NOGUI,
            input_path="build_model.py",
            input_content=final_build,
            outputs=(
                ("plate_tension.cae", final_cae),
                ("plate_tension.inp", final_inp),
            ),
        ),
        _execution(
            workspace,
            state_dir,
            AbaqusInvocation.SOLVER_JOB,
            input_path="plate_tension.inp",
            input_content=(
                b"direct edit" if attack == "broken_solver_lineage" else final_inp
            ),
            outputs=(("plate_tension.odb", final_odb),),
        ),
    ]
    if attack == "dirty_final_solver":
        executions.append(
            _execution(
                workspace,
                state_dir,
                AbaqusInvocation.SOLVER_JOB,
                input_path="plate_tension.inp",
                input_content=final_inp,
                outputs=(("plate_tension.odb", final_odb),),
                warning=True,
            )
        )
    elif attack == "pre_solve_before_warning":
        executions = [
            executions[2],
            executions[3],
            executions[0],
            executions[1],
            executions[2],
            executions[3],
        ]
    executions.append(
        _execution(
            workspace,
            state_dir,
            AbaqusInvocation.ABAQUS_PYTHON,
            input_path="extract_results.py",
            input_content=_EXTRACTOR,
            outputs=(("results.json", final_results),),
        )
    )
    request = CodingRunRequest(
        run_id="rf3-verifier-test",
        goal="Repair output warning",
        workspace=workspace.resolve(),
        state_dir=state_dir.resolve(),
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
    )
    deliverables = [
        "build_model.py",
        "plate_tension.cae",
        "plate_tension.inp",
        "plate_tension.odb",
        "results.json",
    ]
    if attack == "missing_deliverable":
        deliverables.pop()
    decision = ReadyForVerificationDecision(
        deliverables=tuple(
            DeliverableDeclaration(relative_path=path) for path in deliverables
        ),
        summary="ready",
    )
    verifier = PlateOutputWarningRepairRunVerifier(
        defective_build=_DEFECTIVE_BUILD,
        source_build=_SOURCE_BUILD,
        source_extractor=_EXTRACTOR,
    )
    result = asyncio.run(
        verifier.verify(
            request,
            cast(Abaqus2024Runner, _OracleRunner()),
            initial,
            executions,
            decision,
        )
    )
    return result.passed


def test_verifier_accepts_delete_and_active_rf_repairs(tmp_path: Path) -> None:
    assert _run_scenario(tmp_path / "delete")
    assert _run_scenario(tmp_path / "rf1", active_history_repair=True)


@pytest.mark.parametrize(
    "attack",
    [
        "dirty_final_solver",
        "broken_solver_lineage",
        "wrong_final_build",
        "missing_deliverable",
        "sidecar",
        "pre_solve_before_warning",
    ],
)
def test_verifier_rejects_evidence_and_contract_bypasses(
    tmp_path: Path,
    attack: Literal[
        "dirty_final_solver",
        "broken_solver_lineage",
        "wrong_final_build",
        "missing_deliverable",
        "sidecar",
        "pre_solve_before_warning",
    ],
) -> None:
    assert not _run_scenario(tmp_path, attack=attack)
