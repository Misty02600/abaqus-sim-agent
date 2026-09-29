import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import SkipToolExecution
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.guardrails import ToolCallInfo

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.amendments import (
    CodingRunConfigPatch,
    RuntimeExecutionPolicyPatch,
    append_operational_amendment,
)
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.cli import (
    _validate_resume_arguments,
    build_parser,
    run_cli,
)
from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.edit_scope import ReferenceEditGate, ReferenceEditScope
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.resume import load_request_for_resume
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunService,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner

SEED = "# 保留注释\nvalues = field.getSubset(region=wrong).values\nprint(values)\n"
PATCH = SEED.replace("region=wrong", "region=odb.rootAssembly.nodeSets['LEFT']")


def _scope(source: str = SEED) -> ReferenceEditScope:
    normalized = source.replace("\r\n", "\n")
    start = normalized.index("wrong")
    return ReferenceEditScope(
        path="extract.py",
        baseline_source=source,
        baseline_sha256=hashlib.sha256(source.encode()).hexdigest(),
        start=start,
        end=start + len("wrong"),
    )


def _gate(tmp_path: Path, source: str = SEED) -> ReferenceEditGate:
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    (workspace / "extract.py").write_bytes(source.encode())
    gate = ReferenceEditGate(workspace, state, _scope(source))
    gate.prepare(resume=False)
    return gate


def _call(candidate: str = PATCH, *, path: str = "extract.py") -> ToolCallInfo:
    return ToolCallInfo(
        name="write_file",
        args={"path": path, "content": candidate},
        tool_call_id="write-1",
    )


@pytest.mark.parametrize(
    "replacement",
    [
        "make_set()",
        "sets[compute()]",
        "42",
    ],
)
def test_reference_form_denies_extra_behavior(tmp_path: Path, replacement: str) -> None:
    gate = _gate(tmp_path)
    verdict, _ = gate.check(_call(SEED.replace("wrong", replacement)))
    assert verdict.action == "block"
    assert (gate.workspace / "extract.py").read_bytes() == SEED.encode()


@pytest.mark.parametrize(
    "candidate",
    [
        PATCH + "helper()\n",
        PATCH.replace("field.getSubset", "other.getSubset"),
    ],
)
def test_outside_text_is_fixed(tmp_path: Path, candidate: str) -> None:
    gate = _gate(tmp_path)
    assert gate.check(_call(candidate))[0].action == "block"


@pytest.mark.parametrize(
    "path",
    [
        "other.py",
        "../extract.py",
    ],
)
def test_other_files_and_aliases_are_denied(tmp_path: Path, path: str) -> None:
    gate = _gate(tmp_path)
    assert gate.check(_call(path=path))[0].action == "block"


def test_contract_is_strict_and_selects_a_full_expression() -> None:
    payload = _scope().model_dump()
    for update in (
        {"start": True},
        {"start": payload["start"] + 1},
        {"baseline_sha256": "0" * 64},
        {"path": "../x.py"},
        {"extra_authority": True},
    ):
        with pytest.raises(ValidationError):
            ReferenceEditScope.model_validate({**payload, **update})
    assert (
        ReferenceEditScope.model_validate_json(_scope().model_dump_json()) == _scope()
    )


def test_invalid_baseline_is_a_contract_validation_error() -> None:
    with pytest.raises(ValidationError, match=r"valid Python 3\.10"):
        _scope("value = wrong\nif broken(:\n")


def test_unicode_line_separator_does_not_shift_ast_span() -> None:
    source = 'label = "一\u2028二"; value = wrong\n'
    scope = _scope(source)
    scope.validate_candidate(source.replace("wrong", "known.region"))


def test_scope_does_not_encode_the_correct_answer(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    assert (
        gate.check(_call(SEED.replace("wrong", "some.other.repository['ANY']")))[
            0
        ].action
        == "allow"
    )


def test_hardlinked_target_is_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    alias = tmp_path / "alias.py"
    alias.hardlink_to(gate.workspace / "extract.py")
    assert gate.check(_call())[0].action == "block"


def test_full_write_and_second_edit_survive_raw_hash_reload(tmp_path: Path) -> None:
    gate = _gate(tmp_path, SEED.replace("\n", "\r\n"))

    async def handler(arguments: dict[str, object]) -> str:
        (gate.workspace / "extract.py").write_text(
            str(arguments["content"]), encoding="utf-8"
        )
        return "written"

    asyncio.run(gate.execute(_call(), handler))
    restored = ReferenceEditGate(gate.workspace, gate.state_dir, gate.scope)
    restored.prepare(resume=True)
    assert (
        restored.check(_call(PATCH.replace("['LEFT']", "['RIGHT']")))[0].action
        == "allow"
    )
    assert (gate.workspace / "extract.py").read_text(encoding="utf-8") == PATCH


def test_changed_contract_does_not_rebind_a_resumed_run(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    payload = gate.scope.model_dump()
    start = SEED.index("field.getSubset")
    payload.update(start=start, end=start + len("field"))
    changed = ReferenceEditScope.model_validate(payload)
    with pytest.raises(ValueError, match="contract changed"):
        ReferenceEditGate(gate.workspace, gate.state_dir, changed).prepare(resume=True)


def test_edit_candidate_and_stale_hash(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    args = {
        "path": "extract.py",
        "old_text": "region=wrong",
        "new_text": "region=known.region",
        "expected_hash": "bad",
    }
    call = ToolCallInfo(name="edit_file", args=args, tool_call_id="edit-1")
    assert gate.check(call)[0].action == "block"
    args["expected_hash"] = hashlib.sha256(SEED.encode()).hexdigest()[:12]
    assert gate.check(call)[0].action == "allow"


def test_drift_and_missing_pending_checkpoint_fail_closed(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    target = gate.workspace / "extract.py"
    target.write_bytes(PATCH.encode())
    assert gate.check(_call())[0].action == "block"
    recovered = ReferenceEditGate(gate.workspace, gate.state_dir, gate.scope)
    with pytest.raises(ValueError, match="drift"):
        recovered.prepare(resume=True)
    target.write_bytes(SEED.encode())
    state_path = gate.state_dir / "edit_scope_state.json"
    payload = json.loads(state_path.read_text())
    payload["pending"] = True
    state_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unresolved"):
        recovered.prepare(resume=True)
    state_path.unlink()
    with pytest.raises(FileNotFoundError):
        recovered.prepare(resume=True)


def test_parallel_stale_edits_are_serialized(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    args = {
        "path": "extract.py",
        "content": PATCH,
        "expected_hash": hashlib.sha256(SEED.encode()).hexdigest()[:12],
    }
    call = ToolCallInfo(name="write_file", args=args, tool_call_id="parallel")
    writes = 0

    async def handler(arguments: dict[str, object]) -> str:
        nonlocal writes
        await asyncio.sleep(0)
        writes += 1
        (gate.workspace / "extract.py").write_text(
            str(arguments["content"]), encoding="utf-8"
        )
        return "written"

    async def run() -> list[object]:
        return list(
            await asyncio.gather(
                gate.execute(call, handler),
                gate.execute(call, handler),
                return_exceptions=True,
            )
        )

    results = asyncio.run(run())
    assert writes == 1
    assert sum(isinstance(value, SkipToolExecution) for value in results) == 1
    ReferenceEditGate(gate.workspace, gate.state_dir, gate.scope).prepare(resume=True)


def test_failed_partial_write_is_unresolved(tmp_path: Path) -> None:
    gate = _gate(tmp_path)

    async def handler(_args: dict[str, object]) -> str:
        (gate.workspace / "extract.py").write_bytes(b"partial")
        raise OSError("injected disk failure")

    with pytest.raises(OSError, match="injected disk failure"):
        asyncio.run(gate.execute(_call(), handler))
    with pytest.raises(ValueError, match="unresolved"):
        ReferenceEditGate(gate.workspace, gate.state_dir, gate.scope).prepare(
            resume=True
        )


def _request(gate: ReferenceEditGate) -> CodingRunRequest:
    return CodingRunRequest(
        run_id="scope-test",
        goal="Replace only the declared reference.",
        workspace=gate.workspace.resolve(),
        state_dir=gate.state_dir.resolve(),
        runtime=RuntimeProfile(launcher_path=Path("D:/unused.bat")),
        edit_scope=gate.scope,
    )


def test_real_harness_guard_blocks_then_allows_and_service_resumes(
    tmp_path: Path,
) -> None:
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    (workspace / "extract.py").write_bytes(SEED.encode())
    gate = ReferenceEditGate(workspace, state, _scope())
    request = _request(gate)
    initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    calls = 0

    def first(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        names = {tool.name for tool in info.function_tools}
        assert not names.intersection(
            {
                "run_cae_script",
                "run_solver_job",
                "run_abaqus_python",
                "run_command",
                "write_plan",
            }
        )
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file", {"path": "extra.py", "content": "pass\n"}
                    ),
                    ToolCallPart("create_directory", {"path": "extra"}),
                    ToolCallPart(
                        "write_file",
                        {"path": "extract.py", "content": PATCH + "helper()\n"},
                    ),
                ]
            )
        if calls == 2:
            assert (workspace / "extract.py").read_bytes() == SEED.encode()
            assert not (workspace / "extra.py").exists()
            assert not (workspace / "extra").exists()
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "edit_file",
                        {
                            "path": "extract.py",
                            "old_text": "region=wrong",
                            "new_text": "region=odb.rootAssembly.nodeSets['LEFT']",
                        },
                    )
                ]
            )
        assert (workspace / "extract.py").read_text(encoding="utf-8") == PATCH
        raise ConnectionError("injected after guarded write")

    def service(
        model: FunctionModel, req: CodingRunRequest, *, resume: bool = False
    ) -> CodingRunService:
        runner = Abaqus2024Runner(workspace, state)
        deps = CodingAgentDependencies(request=req, runner=runner)
        assert deps.reserve_runtime_call(solver=False) is not None
        assert deps.abaqus_calls == 0
        return CodingRunService(
            agent=create_abaqus_coding_agent(model, req),
            request=req,
            dependencies=deps,
            clarification_provider=ScriptedClarificationProvider({}),
            verifier=ExecutionEvidenceVerifier(),
            initial_snapshot=initial,
            resume=resume,
            expected_amendment_sequence=1 if resume else None,
        )

    with pytest.raises(CodingRunInfrastructureError, match="injected"):
        asyncio.run(service(FunctionModel(first), request).run())
    append_operational_amendment(
        state,
        reason="test continuation",
        patch=CodingRunConfigPatch(
            runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                {"solver": {"mode": "allow"}}
            )
        ),
    )
    recovered = load_request_for_resume(state)
    assert recovered.edit_scope == request.edit_scope

    def second(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    next(
                        tool.name
                        for tool in info.output_tools
                        if tool.name.endswith("ReadyForVerificationDecision")
                    ),
                    {
                        "status": "ready_for_verification",
                        "summary": "Edited reference only; not executed.",
                        "deliverables": [{"relative_path": "extract.py"}],
                    },
                )
            ]
        )

    result = asyncio.run(service(FunctionModel(second), recovered, resume=True).run())
    assert result.outcome.status == "completed"
    assert result.verification is not None
    assert result.verification.scope == "execution_evidence"
    assert (workspace / "extract.py").read_text(encoding="utf-8") == PATCH


def test_cli_scope_is_explicit_and_cannot_override_resume() -> None:
    parser = build_parser()
    assert parser.parse_args(["--goal", "edit"]).edit_scope_file is None
    args = parser.parse_args(["--resume", "state", "--edit-scope-file", "scope.json"])
    with pytest.raises(ValueError, match="edit-scope-file"):
        _validate_resume_arguments(args)


def test_cli_loads_and_persists_scope_before_real_file_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    (workspace / "extract.py").write_bytes(SEED.encode())
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(_scope().model_dump_json(), encoding="utf-8")
    calls = 0

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert all(not tool.name.startswith("run_") for tool in info.function_tools)
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("write_file", {"path": "extract.py", "content": PATCH})
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    next(
                        tool.name
                        for tool in info.output_tools
                        if tool.name.endswith("ReadyForVerificationDecision")
                    ),
                    {
                        "status": "ready_for_verification",
                        "summary": "Scoped edit only.",
                        "deliverables": [{"relative_path": "extract.py"}],
                    },
                )
            ]
        )

    class Client:
        async def close(self) -> None:
            pass

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    monkeypatch.setenv("OPENCODE_API_KEY", "fixture")
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda _provider: "test-key",
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model",
        lambda **_kwargs: (FunctionModel(model), Client()),
    )
    catalog = tmp_path / "api-catalog"
    ApiCatalog([], runtime_release="2024").write(catalog)
    code = run_cli(
        [
            "--model-provider",
            "deepseek",
            "--model",
            "deepseek-v4-flash",
            "--goal",
            "Replace only the authorized reference",
            "--workspace",
            str(workspace),
            "--state-dir",
            str(state),
            "--run-id",
            "scope-cli",
            "--edit-scope-file",
            str(scope_file),
        ]
    )
    assert code == 0
    persisted = CodingRunRequest.model_validate_json(
        (state / "request.json").read_bytes()
    )
    assert persisted.edit_scope == _scope()
    assert (workspace / "extract.py").read_text(encoding="utf-8") == PATCH
