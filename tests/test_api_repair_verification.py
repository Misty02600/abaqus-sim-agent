from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
    ArtifactChange,
    ArtifactChangeKind,
    ArtifactFingerprint,
)
from abaqus_sim_agent.verification.api_repair import ApiRepairRunVerifier


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _execution(
    workspace: Path,
    state_dir: Path,
    result_path: Path,
) -> AbaqusExecutionResult:
    raw_dir = state_dir / "raw"
    raw_dir.mkdir(parents=True)
    stdout_path = raw_dir / "stdout.bin"
    stderr_path = raw_dir / "stderr.bin"
    stdout_path.write_bytes(b"")
    stderr_path.write_bytes(b"")
    return AbaqusExecutionResult(
        invocation=AbaqusInvocation.CAE_NOGUI,
        command=("abq2024.bat", "cae", "noGUI=build_model.py"),
        workspace=workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=stdout_path,
        stderr_raw_path=stderr_path,
        artifact_changes=(
            ArtifactChange(
                kind=ArtifactChangeKind.CREATED,
                path=result_path.name,
                after=ArtifactFingerprint(
                    path=result_path.name,
                    size_bytes=result_path.stat().st_size,
                    sha256=_sha256(result_path),
                ),
            ),
        ),
    )


def test_api_repair_verifier_requires_modified_script_and_cae_result(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    state_dir.mkdir()
    script = workspace / "build_model.py"
    script.write_text("broken = True\n", encoding="utf-8")
    initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    script.write_text("broken = False\n", encoding="utf-8")
    result_path = workspace / "case_result.json"
    result_path.write_text(
        json.dumps({"status": "passed", "edge_count": 2}),
        encoding="utf-8",
    )
    launcher = (tmp_path / "abq2024.bat").resolve()
    request = CodingRunRequest(
        run_id="repair-test",
        goal="repair",
        workspace=workspace.resolve(),
        state_dir=state_dir.resolve(),
        runtime=RuntimeProfile(launcher_path=launcher),
    )
    runner = Abaqus2024Runner(
        request.workspace,
        request.state_dir,
        launcher=launcher,
    )
    verifier = ApiRepairRunVerifier(
        entry_script=script.name,
        result_file=result_path.name,
        expected_result={"status": "passed", "edge_count": 2},
    )

    attempt = asyncio.run(
        verifier.verify(
            request,
            runner,
            initial,
            (_execution(workspace.resolve(), state_dir.resolve(), result_path),),
        )
    )

    assert attempt.passed
    assert attempt.script_modified
    assert attempt.result_created_by_successful_cae
    assert attempt.result_payload == {"status": "passed", "edge_count": 2}


def test_api_repair_verifier_rejects_host_written_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    state_dir.mkdir()
    script = workspace / "build_model.py"
    script.write_text("broken = True\n", encoding="utf-8")
    initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    script.write_text("broken = False\n", encoding="utf-8")
    (workspace / "case_result.json").write_text(
        '{"status":"passed"}',
        encoding="utf-8",
    )
    launcher = (tmp_path / "abq2024.bat").resolve()
    request = CodingRunRequest(
        run_id="repair-host-result",
        goal="repair",
        workspace=workspace.resolve(),
        state_dir=state_dir.resolve(),
        runtime=RuntimeProfile(launcher_path=launcher),
    )
    runner = Abaqus2024Runner(
        request.workspace,
        request.state_dir,
        launcher=launcher,
    )

    attempt = asyncio.run(
        ApiRepairRunVerifier(
            entry_script="build_model.py",
            result_file="case_result.json",
            expected_result={"status": "passed"},
        ).verify(request, runner, initial, ())
    )

    assert not attempt.passed
    assert not attempt.result_created_by_successful_cae
    assert "successful CAE execution" in attempt.feedback
