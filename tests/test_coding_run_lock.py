import asyncio
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ReadyForVerificationDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.run_lock import (
    CodingRunAlreadyLocked,
    CodingRunFileLock,
)
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.verification_port import VerificationAttempt
from abaqus_sim_agent.coding.workspace import WorkspaceResolver, WorkspaceSnapshot
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult


class _PassingVerifier:
    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> VerificationAttempt:
        del request, runner, initial_snapshot, executions, decision
        return VerificationAttempt(passed=True, feedback="passed")


def _request(tmp_path: Path) -> tuple[CodingRunRequest, WorkspaceSnapshot]:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    request = CodingRunRequest(
        run_id="lock-test",
        goal="Test the CodingRun lock.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
    )
    snapshot = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    return request, snapshot


def test_file_lock_is_exclusive_and_reusable(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    with CodingRunFileLock(first_dir):
        with pytest.raises(CodingRunAlreadyLocked):
            with CodingRunFileLock(first_dir):
                pass
        with CodingRunFileLock(second_dir):
            pass

    with CodingRunFileLock(first_dir):
        pass


def test_file_lock_survives_await_and_releases_on_task_cancellation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"

    async def scenario() -> None:
        started = asyncio.Event()
        never = asyncio.Event()

        async def hold_lock() -> None:
            with CodingRunFileLock(state_dir):
                started.set()
                await never.wait()

        task = asyncio.create_task(hold_lock())
        await started.wait()
        with pytest.raises(CodingRunAlreadyLocked):
            with CodingRunFileLock(state_dir):
                pass
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with CodingRunFileLock(state_dir):
            pass

    asyncio.run(scenario())


def test_service_lock_contention_never_calls_model_or_mutates_run_state(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(tmp_path)
    model_called = False

    def model_function(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_called
        model_called = True
        raise AssertionError("model must not run while the state lock is held")

    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
    )

    with CodingRunFileLock(request.state_dir):
        with pytest.raises(CodingRunAlreadyLocked):
            asyncio.run(service.run())

    assert not model_called
    assert not (request.state_dir / "request.json").exists()
    assert not (request.state_dir / "resume_state.json").exists()
    assert not (request.state_dir / "events.jsonl").exists()


def test_file_lock_is_released_when_holding_process_is_terminated(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    code = (
        "from pathlib import Path\n"
        "from abaqus_sim_agent.coding.run_lock import CodingRunFileLock\n"
        f"with CodingRunFileLock(Path({str(state_dir)!r})):\n"
        "    print('locked', flush=True)\n"
        "    input()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(CodingRunAlreadyLocked):
            with CodingRunFileLock(state_dir):
                pass

        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 3.0
        while True:
            try:
                with CodingRunFileLock(state_dir):
                    break
            except CodingRunAlreadyLocked:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
