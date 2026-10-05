"""后台 Solver 的确定性结果与可控完成场景。"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.options import RunOptions
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
    ArtifactChange,
    ArtifactChangeKind,
    ArtifactFingerprint,
)
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from tests.support.web import Client


def _result(tmp_path: Path, workspace: Path) -> AbaqusExecutionResult:
    stdout = tmp_path / "solver.stdout.bin"
    stderr = tmp_path / "solver.stderr.bin"
    stdout.write_bytes(b"solver ok")
    stderr.write_bytes(b"")
    return AbaqusExecutionResult(
        invocation=AbaqusInvocation.SOLVER_JOB,
        command=("abq2024.bat", "job=demo", "input=demo.inp"),
        workspace=workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=0.25,
        stdout="solver ok",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=stdout,
        stderr_raw_path=stderr,
    )


class _DeferredSuccessfulSolver(Abaqus2024Runner):
    def __init__(
        self, tmp_path: Path, workspace: Path, release: threading.Event
    ) -> None:
        super().__init__(workspace, tmp_path / "solver-state")
        self.tmp_path = tmp_path
        self.workspace = workspace
        self.release = release
        self.started = threading.Event()
        self.calls = 0
        self.options: dict = {}
        self.process_started = None

    def set_solver_process_started_callback(self, callback) -> None:
        self.process_started = callback

    async def run_solver_job(self, *args, **kwargs) -> AbaqusExecutionResult:
        self.calls += 1
        self.options = kwargs
        self.started.set()
        if self.process_started is not None:
            self.process_started(4343)
        await asyncio.to_thread(self.release.wait)
        odb = self.workspace / "demo.odb"
        odb.write_bytes(b"fixture odb")
        fingerprint = ArtifactFingerprint(
            path=odb.name,
            size_bytes=odb.stat().st_size,
            sha256=hashlib.sha256(odb.read_bytes()).hexdigest(),
        )
        return _result(self.tmp_path, self.workspace).model_copy(
            update={
                "artifact_changes": (
                    ArtifactChange(
                        kind=ArtifactChangeKind.CREATED,
                        path=odb.name,
                        after=fingerprint,
                    ),
                )
            }
        )


def _background_solver_app(tmp_path: Path, *, solver_resources: dict | None = None):
    release = threading.Event()
    runners: list[_DeferredSuccessfulSolver] = []
    model_calls: list[list] = []
    solver_call_id = "solver-submit-1"

    def model_factory(**kwargs):
        def model(messages, info):
            model_calls.append(messages)
            solver_returns = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart)
                and part.tool_name == "solver_job"
                # A cancelled wait leaves a synthetic return; resume from submit's ID.
                and part.outcome != "interrupted"
            ]
            if not solver_returns:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "solver_job",
                            {
                                "request": {
                                    "action": "submit",
                                    "job_name": "demo",
                                    "input_file": "demo.inp",
                                    **(solver_resources or {}),
                                }
                            },
                            tool_call_id=solver_call_id,
                        )
                    ]
                )
            solver_return = solver_returns[-1]
            if solver_return.outcome == "interrupted":
                # A stopped wait can return text; resume waiting on the same job.
                solver_return = next(
                    part
                    for part in reversed(solver_returns)
                    if isinstance(part.content, dict)
                    and part.content.get("submitted") is True
                )
            assert isinstance(solver_return.content, dict)
            if solver_return.content.get("submitted") is True:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "solver_job",
                            {
                                "request": {
                                    "action": "wait",
                                    "execution_id": solver_return.content[
                                        "execution_id"
                                    ],
                                }
                            },
                            tool_call_id="solver-wait-1",
                        )
                    ]
                )
            if solver_return.content.get("succeeded") is not True:
                name = next(
                    tool.name
                    for tool in info.output_tools
                    if tool.name.endswith("FailedDecision")
                )
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            name,
                            {
                                "status": "failed",
                                "failure_stage": "solver_cancelled",
                                "summary": "Solver execution was cancelled by the user.",
                            },
                            tool_call_id="cancelled-response",
                        )
                    ]
                )
            assert solver_return.content["succeeded"] is True
            name = next(
                tool.name
                for tool in info.output_tools
                if tool.name.endswith("ReadyForVerificationDecision")
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        name,
                        {
                            "status": "ready_for_verification",
                            "deliverables": [{"relative_path": "demo.odb"}],
                            "summary": "Solver completed and result was consumed.",
                        },
                        tool_call_id="final-response",
                    )
                ]
            )

        return FunctionModel(model), Client()

    def runner_factory(workspace, state, **kwargs):
        input_file = workspace / "demo.inp"
        if not input_file.exists():
            input_file.write_text("*HEADING\nfixture\n", encoding="ascii")
        runner = _DeferredSuccessfulSolver(tmp_path, workspace, release)
        runners.append(runner)
        return runner

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            overrides=RunOptions(),
            stream_model=False,
            **CodingRunBudget(
                max_model_requests=4,
                max_abaqus_calls=1,
                max_solver_calls=1,
            ).as_request_controls(),
        ),
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture-key")},
        model_factory=model_factory,
        runner_factory=runner_factory,
    )
    return app, release, runners, model_calls, solver_call_id
