import asyncio
from pathlib import Path

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    DeliverableDeclaration,
    ReadyForVerificationDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
)


def _context(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = (tmp_path / "state").resolve()
    resolved = WorkspaceResolver(captured_cwd=workspace).resolve_interactive(
        run_id="execution-verifier",
        workspace=workspace,
        state_dir=state_dir,
    )
    request = CodingRunRequest(
        run_id="execution-verifier",
        goal="Create or validate an arbitrary Abaqus user script.",
        workspace=resolved.workspace,
        state_dir=resolved.state_dir,
        runtime=RuntimeProfile(launcher_path=(tmp_path / "abq2024.bat").resolve()),
    )
    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    return request, runner, resolved.initial_snapshot


def _execution(
    request: CodingRunRequest,
    *,
    invocation: AbaqusInvocation = AbaqusInvocation.CAE_NOGUI,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> AbaqusExecutionResult:
    return AbaqusExecutionResult(
        invocation=invocation,
        command=("abq2024.bat", invocation.value),
        workspace=request.workspace,
        exit_code=exit_code,
        timed_out=False,
        duration_seconds=1.0,
        stdout=stdout,
        stderr=stderr,
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
    )


def _decision(*paths: str) -> ReadyForVerificationDecision:
    return ReadyForVerificationDecision(
        deliverables=tuple(
            DeliverableDeclaration(relative_path=path) for path in paths
        ),
        summary="Declared deliverables are ready.",
    )


def test_accepts_arbitrary_fresh_python_without_plate_assumptions(
    tmp_path: Path,
) -> None:
    request, runner, initial_snapshot = _context(tmp_path)
    (request.workspace / "user_contact_model.py").write_text(
        "from abaqus import mdb\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        ExecutionEvidenceVerifier().verify(
            request,
            runner,
            initial_snapshot,
            (),
            _decision("user_contact_model.py"),
        )
    )

    assert result.passed
    assert result.scope == "execution_evidence"
    assert result.fresh_artifact_paths == ("user_contact_model.py",)
    assert result.checked_python_scripts == ("user_contact_model.py",)
    assert "No task-specific physics oracle was run" in result.feedback


def test_rejects_python_310_syntax_error(tmp_path: Path) -> None:
    request, runner, initial_snapshot = _context(tmp_path)
    (request.workspace / "broken.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        ExecutionEvidenceVerifier().verify(
            request,
            runner,
            initial_snapshot,
            (),
            _decision("broken.py"),
        )
    )

    assert not result.passed
    assert result.failure_stage == "syntax"
    assert "broken.py:1" in result.feedback


def test_rejects_missing_declared_deliverable(tmp_path: Path) -> None:
    request, runner, initial_snapshot = _context(tmp_path)

    result = asyncio.run(
        ExecutionEvidenceVerifier().verify(
            request,
            runner,
            initial_snapshot,
            (),
            _decision("missing.py"),
        )
    )

    assert not result.passed
    assert result.failure_stage == "deliverables"
    assert "missing.py" in result.feedback


def test_latest_used_runtime_stage_must_succeed(tmp_path: Path) -> None:
    request, runner, initial_snapshot = _context(tmp_path)
    (request.workspace / "model.py").write_text("value = 1\n", encoding="utf-8")
    failed = _execution(request, stdout="Abaqus Error: cae exited with an error.\n")

    result = asyncio.run(
        ExecutionEvidenceVerifier().verify(
            request,
            runner,
            initial_snapshot,
            (failed,),
            _decision("model.py"),
        )
    )

    assert not result.passed
    assert result.failure_stage == "execution"
    assert "cae_nogui" in result.feedback


def test_later_success_supersedes_repaired_stage_failure(tmp_path: Path) -> None:
    request, runner, initial_snapshot = _context(tmp_path)
    (request.workspace / "model.py").write_text("value = 1\n", encoding="utf-8")
    failed = _execution(
        request,
        stderr="Traceback (most recent call last):\nSyntaxError\n",
    )
    repaired = _execution(request)

    result = asyncio.run(
        ExecutionEvidenceVerifier().verify(
            request,
            runner,
            initial_snapshot,
            (failed, repaired),
            _decision("model.py"),
        )
    )

    assert result.passed
    assert result.execution_attempts == 2
    assert result.latest_successful_invocations == (AbaqusInvocation.CAE_NOGUI,)
