import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from abaqus_sim_agent.api_catalog import (
    ApiCatalog,
    ApiMethodCard,
    ApiParameter,
    ApiRuntimeNote,
    ApiSignature,
    ApiSourceRef,
    OfficialApiRecord,
)
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.api_lookup import (
    ApiCatalogMode,
)
from abaqus_sim_agent.coding.api_lookup import (
    runtime_api_hint as _runtime_api_hint,
)
from abaqus_sim_agent.coding.contracts import (
    ApiDiscoveryTrace,
    ApiLookupTrace,
    CodingRunBudget,
    CodingRunRequest,
    FailedDecision,
    NeedsInputDecision,
    RuntimeProfile,
    RuntimeScriptPolicy,
    StaticPreflightBinding,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.runtime_tools import (
    _python_checks_before_runtime,
    _tool_payload,
)
from abaqus_sim_agent.coding.static_preflight import (
    AbqpyBasedPyrightPreflight,
    PreparedAbqpySource,
)
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusDiagnostic,
    AbaqusExecutionResult,
    AbaqusInvocation,
    ProcessOutput,
)
from abaqus_sim_agent.execution.abaqus2024 import ArtifactFingerprint
from abaqus_sim_agent.skills import skill_library_path


def _request(
    tmp_path: Path,
    *,
    budget: CodingRunBudget | None = None,
    runtime_script_policy: RuntimeScriptPolicy | None = None,
    static_preflight: StaticPreflightBinding | None = None,
) -> CodingRunRequest:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    return CodingRunRequest(
        run_id="agent-test",
        goal="Create a plate model but ask for its missing thickness.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
        runtime_script_policy=runtime_script_policy or RuntimeScriptPolicy(),
        static_preflight=static_preflight,
        **(budget or CodingRunBudget()).as_request_controls(),
    )


@pytest.mark.parametrize(
    "mode", ["full", "automatic_verified_only", "automatic_exact_signature"]
)
def test_untyped_cae_failure_delivers_scoped_note_and_keeps_budget_and_replay_guards(
    tmp_path, mode
):
    request = _request(tmp_path)
    script = request.workspace / "build_model.py"
    script.write_text(
        "model = mdb.Model(name='M')\npart = model.Part(name='P')\n"
        "part.setElementType(regions=(part.cells,), elemTypes=())\n",
        encoding="utf-8",
    )
    source_hash = hashlib.sha256(script.read_bytes()).hexdigest()
    note = ApiRuntimeNote(
        note_id="runtime:2024:regions",
        qualified_name="Part.setElementType",
        runtime_release="2024",
        summary="Region dimensions",
        guidance="Select faces for 2D planar geometry.",
        evidence_ref="fixture.json",
        applies_to_parameters=("regions",),
        verified=True,
        source=ApiSourceRef(kind="runtime", release="2024", source_path="fixture.json"),
    )
    catalog = ApiCatalog(
        [
            ApiMethodCard(
                card_id="api:2024:Part.setElementType",
                entity_kind="method",
                canonical_name="Part.setElementType",
                declaring_name="Part.setElementType",
                class_name="Part",
                method_name="setElementType",
                signatures=(ApiSignature(text="setElementType(regions, elemTypes)"),),
                runtime_notes=(note,),
            )
        ],
        runtime_release="2024",
    )
    deps = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=catalog,
    )
    result = AbaqusExecutionResult(
        invocation=AbaqusInvocation.CAE_NOGUI,
        command=("fixture",),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=0.1,
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
        stdout="Abaqus Error: cae exited with an error.\n",
        stderr="错误: 单元类型指派时, 只有相同尺寸的区域参能被选择\n"
        'File "build_model.py", line 3, in <module>\n    part.setElementType(\n',
        input_fingerprints=(
            ArtifactFingerprint(
                path=script.name, size_bytes=script.stat().st_size, sha256=source_hash
            ),
        ),
    )
    assert (
        _runtime_api_hint(
            deps, result.model_copy(update={"timed_out": True}), mode=mode
        )
        is None
    )
    assert (
        _runtime_api_hint(deps, result.model_copy(update={"stdout": ""}), mode=mode)
        is None
    )
    assert deps.api_lookup_calls == 0
    hint = _runtime_api_hint(deps, result, mode=mode)
    assert hint is not None
    assert hint["query"] == "Part.setElementType"
    assert note.guidance in str(hint)
    assert deps.api_lookup_calls == 1
    again = _runtime_api_hint(deps, result, mode=mode)
    assert again is None or again["status"] == "duplicate"
    assert deps.api_lookup_calls == 1
    traces = [
        json.loads(line)
        for line in (request.state_dir / "api_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert traces[0]["delivered_note_ids"] == [note.note_id]
    # Changing the file must not reinterpret an old untyped error's source line.
    script.write_text(
        "model = mdb.Model(name='M')\npart = model.Part(name='P')\npart.Set(name='changed')\n",
        encoding="utf-8",
    )
    assert _runtime_api_hint(deps, result, mode=mode) is None


class _StaticPreflightProcess:
    def __init__(
        self,
        mutate_path: Path | None = None,
        *,
        message: str = 'No parameter named "orientation"',
        rule: str = "reportCallIssue",
        line: int = 2,
    ) -> None:
        self.calls = 0
        self.mutate_path = mutate_path
        self.message = message
        self.rule = rule
        self.line = line

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> ProcessOutput:
        del command, cwd, timeout_seconds
        self.calls += 1
        if self.mutate_path is not None:
            self.mutate_path.write_text("changed during preflight\n", encoding="utf-8")
        payload = {
            "generalDiagnostics": [
                {
                    "file": "build_model.py",
                    "severity": "error",
                    "message": self.message,
                    "range": {
                        "start": {"line": self.line, "character": 10},
                        "end": {"line": self.line, "character": 21},
                    },
                    "rule": self.rule,
                }
            ]
        }
        return ProcessOutput(
            exit_code=1,
            stdout=json.dumps(payload).encode(),
            stderr=b"",
        )


class _UnavailableStaticPreflightProcess:
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> ProcessOutput:
        del command, cwd, timeout_seconds
        self.calls += 1
        return ProcessOutput(exit_code=1, stdout=b"not-json", stderr=b"failed")


def _feedback_binding() -> StaticPreflightBinding:
    return StaticPreflightBinding(
        engine_version="1.1.407",
        api_source_version="2024.10",
        api_source_sha256="a" * 64,
    )


def _prepared_static_source(
    tmp_path: Path,
    binding: StaticPreflightBinding,
) -> PreparedAbqpySource:
    config_path = tmp_path / "pyrightconfig.json"
    config_path.write_text("{}\n", encoding="utf-8")
    return PreparedAbqpySource(
        root=tmp_path,
        config_path=config_path,
        wheel_sha256=binding.api_source_sha256,
        version=binding.api_source_version,
        basedpyright_version=binding.engine_version,
    )


def _production_policy() -> RuntimeScriptPolicy:
    return RuntimeScriptPolicy(
        allowed_cae_scripts=("build_model.py",),
        allowed_abaqus_python_scripts=("extract_results.py",),
        allowed_solver_inputs=("beam_tension.inp",),
    )


def test_agent_composes_coder_skill_and_typed_decisions(tmp_path: Path) -> None:
    request = _request(tmp_path)
    observed: dict[str, object] = {}

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        observed["function_tools"] = {tool.name for tool in info.function_tools}
        observed["output_tools"] = {tool.name for tool in info.output_tools}
        observed["output_tool_descriptions"] = {
            tool.name: tool.description for tool in info.output_tools
        }
        observed["deferred_capabilities"] = (
            info.model_request_parameters.deferred_capability_ids
        )
        observed["instructions"] = info.instructions
        output_tool = next(
            tool
            for tool in info.output_tools
            if tool.name.endswith("NeedsInputDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "needs_input",
                        "clarification": {
                            "field_id": "plate_thickness",
                            "kind": "missing_physical_input",
                            "question": "板厚是多少？",
                            "impact": "板厚会改变平面应力截面刚度和反力。",
                        },
                        "summary": "板厚会影响截面和反力，不能自行假设。",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        skill_library=skill_library_path(),
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, NeedsInputDecision)
    assert result.output.clarification.kind == "missing_physical_input"
    assert "截面刚度" in result.output.clarification.impact
    assert dependencies.abaqus_calls == 0
    assert observed["output_tools"] == {
        "final_result_NeedsInputDecision",
        "final_result_ReadyForVerificationDecision",
        "final_result_FailedDecision",
    }
    output_tool_descriptions = observed["output_tool_descriptions"]
    assert isinstance(output_tool_descriptions, dict)
    assert all(
        "workspace" in description and "not" in description
        for description in output_tool_descriptions.values()
    )
    function_tools = observed["function_tools"]
    assert isinstance(function_tools, set)
    assert {
        "run_cae_script",
        "run_solver_job",
        "run_abaqus_python",
        "read_file",
        "write_file",
    } <= function_tools
    assert "run_command" not in function_tools
    assert "delegate" not in function_tools
    assert "record_probe_result" not in function_tools
    assert "lookup_abaqus_api" not in function_tools
    assert "inventory_agent_context" not in function_tools
    assert "write_plan" not in function_tools
    assert {
        "read_plan",
        "add_task",
        "update_task_status",
        "update_task_statuses",
    }.isdisjoint(function_tools)
    assert observed["deferred_capabilities"] == {"abaqus-2024-coding"}
    instructions = observed["instructions"]
    assert isinstance(instructions, str)
    assert str(request.workspace) in instructions
    assert "plate_thickness" not in instructions
    assert "open-ended user coding task" in instructions
    assert "No task-specific physics oracle" not in instructions
    assert "does not run a hidden task-specific physics oracle" in instructions
    normalized_instructions = " ".join(instructions.split())
    assert "Before loading a capability" in instructions
    assert "two or more interpretations" in normalized_instructions
    assert "materially change the physical idealization" in normalized_instructions
    assert "Ask for the unresolved engineering fact" in normalized_instructions
    assert "Do not ask the user to choose an Abaqus API" in normalized_instructions
    assert "conflicting_user_constraint" in instructions
    assert (
        "No generic shell or host-Python execution tool is exposed"
        in normalized_instructions
    )
    assert "decision only in prose" in normalized_instructions
    assert "not to a JSON file in the workspace" in instructions
    assert "every occurrence" in instructions
    assert "same evidence-backed repair" in instructions


def test_agent_can_expose_linear_structural_foundation_skills_for_routing_smoke(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    observed: dict[str, object] = {}

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        observed["deferred_capabilities"] = (
            info.model_request_parameters.deferred_capability_ids
        )
        output_tool = next(
            tool
            for tool in info.output_tools
            if tool.name.endswith("NeedsInputDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "needs_input",
                        "clarification": {
                            "field_id": "plate_thickness",
                            "kind": "missing_physical_input",
                            "question": "板厚是多少？",
                            "impact": "板厚会改变平面应力截面刚度和反力。",
                        },
                        "summary": "仅检查路由 smoke 的 deferred capability 元数据。",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        skill_library=skill_library_path(),
        foundation_skill_profile="linear_structural_fea_v1",
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, NeedsInputDecision)
    assert observed["deferred_capabilities"] == {
        "abaqus-2024-coding",
        "linear-structural-fea-formulation",
        "linear-structural-fea-modeling",
        "linear-structural-fea-discretization",
        "linear-structural-fea-postprocess",
        "linear-structural-fea-verification",
    }
    assert dependencies.abaqus_calls == 0


def test_agent_can_expose_structural_topic_skills_for_routing_smoke(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    observed: dict[str, object] = {}

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        observed["deferred_capabilities"] = (
            info.model_request_parameters.deferred_capability_ids
        )
        output_tool = next(
            tool
            for tool in info.output_tools
            if tool.name.endswith("NeedsInputDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "needs_input",
                        "clarification": {
                            "field_id": "material_data",
                            "kind": "missing_physical_input",
                            "question": "材料本构参数来自哪里？",
                            "impact": "专题 Skill 不能替用户补造超弹性或塑性参数。",
                        },
                        "summary": "仅检查专题 Skill 的 deferred capability 路由。",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        skill_library=skill_library_path(),
        structural_topic_skill_profile="structural_mechanics_v1",
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, NeedsInputDecision)
    assert observed["deferred_capabilities"] == {
        "abaqus-2024-coding",
        "hyperelastic-fea",
        "hyperelastic-calibration",
        "plasticity-fea",
        "geometric-nonlinear-fea",
        "structural-dynamics-fea",
        "modal-dynamics-fea",
        "explicit-dynamics-fea",
        "quasi-static-explicit-fea",
        "contact-fea",
        "contact-diagnostics-fea",
        "interface-cohesive-fea",
    }
    assert dependencies.abaqus_calls == 0


def test_runtime_script_policy_blocks_unlisted_cae_without_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        tmp_path,
        runtime_script_policy=RuntimeScriptPolicy(
            allowed_cae_scripts=("build_model.py",),
            allowed_abaqus_python_scripts=("extract_results.py",),
            allowed_solver_inputs=("beam_tension.inp",),
        ),
    )
    observed_messages: list[ModelMessage] = []
    observed_instructions: list[str] = []
    call_count = 0

    async def fail_if_executed(
        runner: Abaqus2024Runner,
        script: str,
        *,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 180.0,
    ) -> AbaqusExecutionResult:
        del runner, script, args, timeout_seconds
        raise AssertionError("denied script reached the Abaqus runner")

    monkeypatch.setattr(Abaqus2024Runner, "run_cae_script", fail_if_executed)

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert info.instructions is not None
            observed_instructions.append(info.instructions)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "probe.py"},
                        tool_call_id="denied-probe",
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Runtime policy inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    agent = create_abaqus_coding_agent(FunctionModel(model_function), request)

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert dependencies.abaqus_calls == 0
    assert (
        dependencies.runtime_script_policy_denial(
            kind="cae",
            path="BUILD_MODEL.PY",
        )
        is None
    )
    python_denial = dependencies.runtime_script_policy_denial(
        kind="abaqus_python",
        path="probe_extract.py",
    )
    assert python_denial is not None
    assert python_denial["allowed_paths"] == ["extract_results.py"]
    solver_denial = dependencies.runtime_script_policy_denial(
        kind="solver_input",
        path="probe.inp",
    )
    assert solver_denial is not None
    assert solver_denial["allowed_paths"] == ["beam_tension.inp"]
    tool_returns = [
        part
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    denied = next(part for part in tool_returns if part.tool_name == "run_cae_script")
    content = denied.content
    assert isinstance(content, dict)
    assert content["error_type"] == "RuntimeScriptPolicyDenied"
    assert content["budget_consumed"] is False
    assert content["allowed_paths"] == ["build_model.py"]
    instructions = observed_instructions[0]
    assert "exact runtime-file allowlist" in instructions
    assert "Do not create a probe or\ncheck script" in instructions


@pytest.mark.parametrize("analysis_available", [True, False])
@pytest.mark.parametrize("runtime_fails", [False, True])
def test_static_feedback_accompanies_each_execution_and_reuses_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    analysis_available: bool,
    runtime_fails: bool,
) -> None:
    binding = StaticPreflightBinding(
        engine_version="1.1.407",
        api_source_version="2024.10",
        api_source_sha256="a" * 64,
    )
    request = _request(tmp_path, static_preflight=binding)
    (request.workspace / "build_model.py").write_text(
        "from abaqus import *\nmodel.BeamSection(orientation='x')\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "pyrightconfig.json"
    config_path.write_text("{}\n", encoding="utf-8")
    source = PreparedAbqpySource(
        root=tmp_path,
        config_path=config_path,
        wheel_sha256=binding.api_source_sha256,
        version=binding.api_source_version,
        basedpyright_version=binding.engine_version,
    )
    process = (
        _StaticPreflightProcess()
        if analysis_available
        else _UnavailableStaticPreflightProcess()
    )
    preflight = AbqpyBasedPyrightPreflight(
        source,
        request.state_dir,
        process_strategy=process,
    )
    runtime_calls: list[str] = []
    observed_messages: list[ModelMessage] = []
    observed_instructions: list[str] = []
    model_calls = 0

    async def fake_run_cae_script(
        runner: Abaqus2024Runner,
        script: str,
        *,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 180.0,
    ) -> AbaqusExecutionResult:
        del runner, args, timeout_seconds
        runtime_calls.append(script)
        return AbaqusExecutionResult(
            invocation=AbaqusInvocation.CAE_NOGUI,
            command=("abq2024.bat", "cae", f"noGUI={script}"),
            workspace=request.workspace,
            exit_code=1 if runtime_fails else 0,
            timed_out=False,
            duration_seconds=0.1,
            stdout="DONE\n",
            stderr="TypeError: runtime failure" if runtime_fails else "",
            stdout_encoding="utf-8",
            stderr_encoding="utf-8",
            stdout_raw_path=request.state_dir / "abaqus-stdout.bin",
            stderr_raw_path=request.state_dir / "abaqus-stderr.bin",
        )

    monkeypatch.setattr(Abaqus2024Runner, "run_cae_script", fake_run_cae_script)

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_calls
        tool_names = {tool.name for tool in info.function_tools}
        assert {"ask_user", "run_command", "solver_job"} <= tool_names
        model_calls += 1
        if info.instructions is not None:
            observed_instructions.append(info.instructions)
        if model_calls <= 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "build_model.py"},
                        tool_call_id=f"cae-{model_calls}",
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Advisory acknowledgment inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        static_preflight=preflight,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert process.calls == (1 if analysis_available else 2)
    assert dependencies.static_preflight_calls == process.calls
    assert dependencies.abaqus_calls == 2
    assert runtime_calls == ["build_model.py", "build_model.py"]
    tool_returns = [
        part.content
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "run_cae_script"
    ]
    assert len(tool_returns) == 2
    for payload in tool_returns:
        assert isinstance(payload, dict)
        assert payload["succeeded"] is (not runtime_fails)
        assert payload["static_preflight"]["status"] == (
            "diagnostics" if analysis_available else "unavailable"
        )
        assert payload["static_preflight"]["non_blocking"] is True
        if analysis_available:
            assert payload["static_preflight"]["diagnostics"]
        else:
            assert payload["static_preflight"]["error"]
        if runtime_fails:
            assert "runtime failure" in payload["stderr_tail"]
        assert "error_type" not in payload
    instructions = " ".join(observed_instructions[0].split())
    assert "never require a separate acknowledgement or block execution" in instructions
    assert "escape hatch" not in instructions


def test_unavailable_analysis_is_nonblocking_and_explicit(
    tmp_path: Path,
) -> None:
    binding = _feedback_binding()
    request = _request(
        tmp_path,
        runtime_script_policy=_production_policy(),
        static_preflight=binding,
    )
    (request.workspace / "build_model.py").write_text(
        "value = 1\n",
        encoding="utf-8",
    )
    source = _prepared_static_source(tmp_path, binding)
    process = _UnavailableStaticPreflightProcess()
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        static_preflight=AbqpyBasedPyrightPreflight(
            source,
            request.state_dir,
            process_strategy=process,
        ),
    )

    blocked, annotation = asyncio.run(
        _python_checks_before_runtime(
            dependencies,
            kind="cae",
            script_path="build_model.py",
        )
    )

    assert blocked is None
    assert annotation is not None
    assert annotation["status"] == "unavailable"
    assert annotation["non_blocking"] is True
    assert process.calls == 1
    assert dependencies.abaqus_calls == 0


def test_enforced_runtime_policy_denial_precedes_syntax_and_api_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _feedback_binding()
    request = _request(
        tmp_path,
        runtime_script_policy=_production_policy(),
        static_preflight=binding,
    )
    (request.workspace / "probe.py").write_text("def broken(:\n", encoding="utf-8")
    source = _prepared_static_source(tmp_path, binding)
    process = _StaticPreflightProcess()
    observed_messages: list[ModelMessage] = []
    model_calls = 0

    async def fail_if_executed(
        runner: Abaqus2024Runner,
        script: str,
        *,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 180.0,
    ) -> AbaqusExecutionResult:
        del runner, script, args, timeout_seconds
        raise AssertionError("denied script reached Abaqus")

    monkeypatch.setattr(Abaqus2024Runner, "run_cae_script", fail_if_executed)

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "probe.py"},
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Authorization order inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        static_preflight=AbqpyBasedPyrightPreflight(
            source,
            request.state_dir,
            process_strategy=process,
        ),
    )
    agent = create_abaqus_coding_agent(FunctionModel(model_function), request)

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert process.calls == 0
    assert dependencies.static_preflight_calls == 0
    assert dependencies.syntax_gate is not None
    assert dependencies.syntax_gate.checks == 0
    assert dependencies.abaqus_calls == 0
    tool_return = next(
        part
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "run_cae_script"
    )
    assert isinstance(tool_return.content, dict)
    assert tool_return.content["error_type"] == "RuntimeScriptPolicyDenied"


def test_typed_probe_protocol_exposes_conclusion_tool_and_guidance(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    observed: dict[str, object] = {}

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        observed["function_tools"] = {tool.name for tool in info.function_tools}
        observed["instructions"] = info.instructions
        observed["cae_schema"] = next(
            tool.parameters_json_schema
            for tool in info.function_tools
            if tool.name == "run_cae_script"
        )
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Probe protocol inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        probe_protocol_mode="typed",
        probe_target_scripts=("build_model.py",),
    )

    asyncio.run(agent.run(request.goal, deps=dependencies))

    function_tools = observed["function_tools"]
    assert isinstance(function_tools, set)
    assert "record_probe_result" in function_tools
    schema = observed["cae_schema"]
    assert isinstance(schema, dict)
    assert "purpose" in schema["required"]
    assert "probe_result" in schema["properties"]
    instructions = observed["instructions"]
    assert isinstance(instructions, str)
    assert "diagnostic execution succeeding only means that its script ran" in (
        instructions
    )
    assert "host blocks Solver and\nall further diagnostics" in instructions
    assert "`build_model.py`" in instructions
    assert "newly created probe file is not a\nproduction target" in instructions


@pytest.mark.parametrize(
    "api_catalog_mode",
    ["automatic_verified_only", "automatic_exact_signature"],
)
def test_automatic_modes_are_invisible_until_runtime_failure(
    tmp_path: Path,
    api_catalog_mode: ApiCatalogMode,
) -> None:
    request = _request(tmp_path)
    observed: dict[str, object] = {}

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        observed["function_tools"] = {tool.name for tool in info.function_tools}
        observed["instructions"] = info.instructions
        output_tool = next(
            tool
            for tool in info.output_tools
            if tool.name.endswith("NeedsInputDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "needs_input",
                        "clarification": {
                            "field_id": "plate_thickness",
                            "kind": "missing_physical_input",
                            "question": "板厚是多少？",
                            "impact": "板厚会改变平面应力截面刚度和反力。",
                        },
                        "summary": "板厚是必需的物理输入。",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=ApiCatalog([], runtime_release="2024"),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        api_catalog_mode=api_catalog_mode,
        planning_enabled=False,
    )

    asyncio.run(agent.run(request.goal, deps=dependencies))

    function_tools = observed["function_tools"]
    assert isinstance(function_tools, set)
    assert "lookup_abaqus_api" not in function_tools
    assert "write_plan" not in function_tools
    instructions = observed["instructions"]
    assert isinstance(instructions, str)
    assert "exact Abaqus API catalog" not in instructions
    assert "lookup_abaqus_api" not in instructions


def test_open_product_agent_exposes_shell_and_background_solver_without_planning(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    observed: dict[str, object] = {}

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        observed["tools"] = {tool.name for tool in info.function_tools}
        observed["instructions"] = info.instructions
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Tool schema inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )

    asyncio.run(agent.run(request.goal, deps=dependencies))

    tools = observed["tools"]
    assert isinstance(tools, set)
    assert {"run_command", "start_command", "check_command", "stop_command"} <= tools
    assert {"list_directory", "search_files", "file_info", "read_tool_result"} <= tools
    assert not {"shell", "list_files", "grep", "delegate_task"} & tools
    assert "solver_job" in tools
    assert "run_solver_job" not in tools
    assert "write_plan" not in tools
    instructions = observed["instructions"]
    assert isinstance(instructions, str)
    assert "A general coding shell is available" in instructions
    assert "action=submit" in instructions
    assert "action=wait" in instructions


def test_open_product_shell_blocks_versioned_abaqus_launcher(tmp_path: Path) -> None:
    request = _request(tmp_path)
    observed_messages: list[ModelMessage] = []
    model_calls = 0

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_command",
                        {"command": "abq2025hf2.bat job=demo"},
                        tool_call_id="blocked-abaqus-shell",
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Shell denial inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )

    asyncio.run(agent.run(request.goal, deps=dependencies))

    returns = [
        part
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "run_command"
    ]
    assert len(returns) == 1
    assert "Abaqus launchers are not available" in str(returns[0].content)


@pytest.mark.parametrize("resources", [{}, {"cpus": 4, "memory": "512 mb"}])
def test_open_product_solver_submit_allows_other_work_before_wait(
    tmp_path: Path,
    resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        tmp_path, budget=CodingRunBudget(max_abaqus_calls=1, max_solver_calls=1)
    )
    (request.workspace / "demo.inp").write_text("*HEADING\n", encoding="utf-8")
    (request.workspace / "notes.txt").write_text("independent\n", encoding="utf-8")
    execution_id = "a" * 32
    calls: list[str] = []
    model_calls = 0

    def submit_solver_job(
        runner: Abaqus2024Runner,
        job_name: str,
        *,
        input_file: Path | str,
        timeout_seconds: float,
        cpus: int | None = None,
        memory: str | None = None,
    ) -> str:
        del runner, timeout_seconds
        assert cpus == resources.get("cpus")
        assert memory == resources.get("memory")
        calls.append(f"submit:{job_name}:{input_file}")
        return execution_id

    async def wait_solver_job(
        runner: Abaqus2024Runner,
        requested_execution_id: str,
    ) -> AbaqusExecutionResult:
        del runner
        calls.append(f"wait:{requested_execution_id}")
        return AbaqusExecutionResult(
            invocation=AbaqusInvocation.SOLVER_JOB,
            command=("abq2024.bat", "job=demo", "input=demo.inp", "interactive"),
            workspace=request.workspace,
            exit_code=0,
            timed_out=False,
            duration_seconds=0.2,
            stdout="COMPLETED\n",
            stderr="",
            stdout_encoding="utf-8",
            stderr_encoding="utf-8",
            stdout_raw_path=request.state_dir / "solver-stdout.bin",
            stderr_raw_path=request.state_dir / "solver-stderr.bin",
        )

    monkeypatch.setattr(Abaqus2024Runner, "submit_solver_job", submit_solver_job)
    monkeypatch.setattr(Abaqus2024Runner, "wait_solver_job", wait_solver_job)

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "solver_job",
                        {
                            "request": {
                                "action": "submit",
                                "job_name": "demo",
                                "input_file": "demo.inp",
                                **resources,
                            }
                        },
                        tool_call_id="solver-submit",
                    )
                ]
            )
        if model_calls == 2:
            submit_return = next(
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "solver_job"
            )
            assert isinstance(submit_return.content, dict)
            assert submit_return.content["execution_id"] == execution_id
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "read_file",
                        {"path": "notes.txt"},
                        tool_call_id="independent-read",
                    )
                ]
            )
        if model_calls == 3:
            assert any(
                isinstance(part, ToolReturnPart) and part.tool_name == "read_file"
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "solver_job",
                        {"request": {"action": "wait", "execution_id": execution_id}},
                        tool_call_id="solver-wait",
                    )
                ]
            )
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Submit/read/wait flow inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )

    asyncio.run(agent.run(request.goal, deps=dependencies))

    assert calls == ["submit:demo:demo.inp", f"wait:{execution_id}"]
    assert dependencies.pending_solver_executions == set()
    assert len(dependencies.executions) == 1


def test_runtime_preflight_error_is_returned_to_agent_for_repair(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    call_count = 0
    observed_messages: list[ModelMessage] = []

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "missing.py"},
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "authoring",
                        "summary": "The requested script does not exist.",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    agent = create_abaqus_coding_agent(FunctionModel(model_function), request)

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert dependencies.abaqus_calls == 0
    assert dependencies.executions == []
    assert any("FileNotFoundError" in str(message) for message in observed_messages)


def test_coder_filesystem_and_runner_share_canonical_workspace_without_shell(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    (request.workspace / "marker.txt").write_bytes("工作区标记".encode("cp936"))
    call_count = 0
    observed_messages: list[ModelMessage] = []

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert "run_command" not in {tool.name for tool in info.function_tools}
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "read_file",
                        {"path": "marker.txt"},
                        tool_call_id="read-marker",
                    ),
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Workspace roots were inspected.",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    agent = create_abaqus_coding_agent(FunctionModel(model_function), request)

    asyncio.run(agent.run(request.goal, deps=dependencies))

    tool_returns = [
        part
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    read_result = next(part for part in tool_returns if part.tool_name == "read_file")
    assert "工作区标记" in str(read_result.content)
    assert "encoding:cp936" in str(read_result.content)
    assert all(part.tool_name != "run_command" for part in tool_returns)
    assert runner.workspace == request.workspace


def test_agent_can_lookup_optional_exact_api_catalog(tmp_path: Path) -> None:
    request = _request(tmp_path)
    catalog = ApiCatalog(
        [
            ApiMethodCard(
                card_id="api:2024:MeshPart.seedEdgeByNumber",
                entity_kind="method",
                canonical_name="Part.seedEdgeByNumber",
                declaring_name="MeshPart.seedEdgeByNumber",
                class_name="MeshPart",
                method_name="seedEdgeByNumber",
                public_names=("Part.seedEdgeByNumber",),
                signatures=(
                    ApiSignature(
                        text="seedEdgeByNumber(edges, number, constraint=FREE)",
                        parameters=(
                            ApiParameter(name="edges", required=True),
                            ApiParameter(name="number", required=True),
                        ),
                    ),
                ),
                official_records=(
                    OfficialApiRecord(
                        record_id="official:2026:seedEdgeByNumber",
                        entity_kind="method",
                        owner_name="Part",
                        callable_name="seedEdgeByNumber",
                        canonical_name="Part.seedEdgeByNumber",
                        summary="Seeds selected edges by number.",
                        access=(
                            "import part mdb.models[name].parts[name].seedEdgeByNumber",
                        ),
                        paths=("mdb.models[name].parts[name].seedEdgeByNumber",),
                        required_parameters=(
                            ApiParameter(
                                name="edges",
                                required=True,
                                description="A sequence of Edge objects.",
                            ),
                            ApiParameter(
                                name="number",
                                required=True,
                                description="The number of elements along the edge.",
                            ),
                        ),
                        source=ApiSourceRef(
                            kind="official",
                            release="2026 FD02",
                            source_path="SIMACAEKERRefMap/seed-edge.htm",
                            source_url="http://localhost:4040/seed-edge.htm",
                        ),
                    ),
                ),
            )
        ],
        runtime_release="2024",
    )
    call_count = 0
    observed_messages: list[ModelMessage] = []

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            tool_names = {tool.name for tool in info.function_tools}
            assert "lookup_abaqus_api" in tool_names
            assert "discover_abaqus_api" not in tool_names
            assert info.instructions is not None
            assert "lookup_abaqus_api" in info.instructions
            assert "Do not\nquery it preemptively" in info.instructions
            assert "build_model.py" not in info.instructions
            assert "extract_results.py" not in info.instructions
            assert "open-ended user coding task" in info.instructions
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_abaqus_api",
                        {"name": "Part.seedEdgeByNumber"},
                        tool_call_id="lookup-api",
                    )
                ]
            )
        if call_count == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_abaqus_api",
                        {"name": "Part.seedEdgeByNumber"},
                        tool_call_id="lookup-api-duplicate",
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "API card inspected.",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(
        request=request,
        runner=runner,
        api_catalog=catalog,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        api_catalog_mode="full",
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert dependencies.api_lookup_calls == 1
    tool_returns = [
        part
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "lookup_abaqus_api"
    ]
    assert len(tool_returns) == 2
    assert "Part.seedEdgeByNumber" in str(tool_returns[0].content)
    assert "A sequence of Edge objects." in str(tool_returns[0].content)
    assert "'imports': ['part']" in str(tool_returns[0].content)
    assert "duplicate" in str(tool_returns[1].content)
    trace_lines = (
        (request.state_dir / "api_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    traces = [ApiLookupTrace.model_validate_json(line) for line in trace_lines]
    assert len(traces) == 2
    assert traces[0].source == "active"
    assert traces[0].query == "Part.seedEdgeByNumber"
    assert traces[0].status == "found"
    assert traces[0].counted is True
    assert traces[0].payload_bytes > 0
    assert traces[1].status == "duplicate"
    assert traces[1].counted is False
    assert dependencies.returned_api_card_ids == {"api:2024:MeshPart.seedEdgeByNumber"}


def test_explicit_api_discovery_requires_exact_lookup_for_evidence(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    card = ApiMethodCard(
        card_id="api:2024:MeshPart.seedPart",
        entity_kind="method",
        canonical_name="MeshPart.seedPart",
        declaring_name="MeshPart.seedPart",
        class_name="MeshPart",
        method_name="seedPart",
        public_names=("Part.seedPart",),
        signatures=(ApiSignature(text="seedPart(size)"),),
    )
    catalog = ApiCatalog([card], runtime_release="2024")
    call_count = 0
    observed_messages: list[ModelMessage] = []

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            tool_names = {tool.name for tool in info.function_tools}
            assert {"lookup_abaqus_api", "discover_abaqus_api"}.issubset(tool_names)
            assert info.instructions is not None
            assert (
                "Discovery returns candidate names and Card IDs only"
                in info.instructions
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "discover_abaqus_api",
                        {"receiver": "Part", "query": "seed", "limit": 5},
                        tool_call_id="discover-api",
                    )
                ]
            )
        if call_count == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_abaqus_api",
                        {"name": "Part.seedPart"},
                        tool_call_id="lookup-api",
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Candidate was verified with an exact Card lookup.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=catalog,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        api_catalog_mode="full_with_discovery",
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    tool_returns = [
        part
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "discover_abaqus_api"
    ]
    assert len(tool_returns) == 1
    assert tool_returns[0].content["candidates"] == [
        {"name": "Part.seedPart", "card_id": card.card_id}
    ]
    assert dependencies.api_lookup_calls == 2
    assert dependencies.returned_api_card_ids == {card.card_id}
    discovery_trace = ApiDiscoveryTrace.model_validate_json(
        (request.state_dir / "api_discoveries.jsonl").read_text(encoding="utf-8")
    )
    assert discovery_trace.candidate_card_ids == (card.card_id,)
    assert discovery_trace.candidate_names == ("Part.seedPart",)
    assert discovery_trace.counted is True
    lookup_trace = ApiLookupTrace.model_validate_json(
        (request.state_dir / "api_lookups.jsonl").read_text(encoding="utf-8")
    )
    assert lookup_trace.card_ids == (card.card_id,)
    assert lookup_trace.delivered_card_ids == (card.card_id,)
    assert lookup_trace.counted is True


def test_agent_parameter_lookup_is_scoped_and_independent_of_method_card(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    note = ApiRuntimeNote(
        note_id="runtime:2024:Model.SurfaceTraction:side-aware-region",
        qualified_name="Model.SurfaceTraction",
        runtime_release="2024",
        summary="Use a side-aware surface region.",
        guidance="Pass an Assembly.Surface as region.",
        evidence_ref="benchmarks/evidence.json",
        applies_to_parameters=("region",),
        verified=True,
        source=ApiSourceRef(
            kind="runtime",
            release="2024",
            source_path="benchmarks/evidence.json",
        ),
    )
    card = ApiMethodCard(
        card_id="api:2024:LoadModel.SurfaceTraction",
        entity_kind="constructor",
        canonical_name="Model.SurfaceTraction",
        declaring_name="LoadModel.SurfaceTraction",
        class_name="LoadModel",
        method_name="SurfaceTraction",
        public_names=("Model.SurfaceTraction",),
        signatures=(
            ApiSignature(
                text=(
                    "SurfaceTraction(name, createStepName, region, magnitude, "
                    "directionVector=())"
                ),
                parameters=(
                    ApiParameter(name="region", annotation="Region", required=True),
                    ApiParameter(
                        name="directionVector",
                        annotation="tuple",
                        default="()",
                        required=False,
                    ),
                ),
            ),
        ),
        official_records=(
            OfficialApiRecord(
                record_id="official:2026:SurfaceTraction",
                entity_kind="constructor",
                owner_name="Model",
                callable_name="SurfaceTraction",
                canonical_name="Model.SurfaceTraction",
                access=("import load",),
                required_parameters=(
                    ApiParameter(
                        name="region",
                        required=True,
                        description="A Region object specifying the loaded region.",
                    ),
                ),
                optional_parameters=(
                    ApiParameter(
                        name="directionVector",
                        required=False,
                        description=(
                            "A VertexArray of length 2, or two tuples of "
                            "coordinates, specifying the load direction."
                        ),
                    ),
                ),
                source=ApiSourceRef(
                    kind="official",
                    release="2026 FD02",
                    source_path="SIMACAEKERRefMap/surface-traction.htm",
                    source_url="http://localhost:4040/surface-traction.htm",
                ),
            ),
        ),
        runtime_notes=(note,),
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=ApiCatalog([card], runtime_release="2024"),
    )
    call_count = 0
    observed_messages: list[ModelMessage] = []

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        queries = (
            "Model.SurfaceTraction",
            "SurfaceTraction.directionVector",
            "Model.SurfaceTraction.region",
            "SurfaceTraction.region",
        )
        if call_count <= len(queries):
            if call_count == 1:
                assert info.instructions is not None
                assert "Model.SurfaceTraction.directionVector" in info.instructions
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_abaqus_api",
                        {"name": queries[call_count - 1]},
                        tool_call_id=f"parameter-lookup-{call_count}",
                    )
                ]
            )
        observed_messages.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Parameter contracts inspected.",
                    },
                )
            ]
        )

    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        api_catalog_mode="full",
    )
    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    returns = [
        part.content
        for message in observed_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "lookup_abaqus_api"
    ]
    assert len(returns) == 4
    assert all(isinstance(payload, dict) for payload in returns)
    method_payload, direction_payload, region_payload, duplicate_payload = returns
    assert isinstance(method_payload, dict)
    assert isinstance(direction_payload, dict)
    assert isinstance(region_payload, dict)
    assert isinstance(duplicate_payload, dict)
    assert "cards" in method_payload
    assert "parameter" not in method_payload
    direction = direction_payload["parameter"]
    assert isinstance(direction, dict)
    assert direction["name"] == "directionVector"
    assert direction["signature_variants"] == [
        {
            "kind": "unknown",
            "annotation": "tuple",
            "default": "()",
            "required": False,
        }
    ]
    assert "length 2" in str(direction["official_descriptions"])
    assert direction["runtime_notes"] == []
    assert direction["imports"] == ["load"]
    region = region_payload["parameter"]
    assert isinstance(region, dict)
    assert region["name"] == "region"
    assert [item["note_id"] for item in region["runtime_notes"]] == [note.note_id]
    assert duplicate_payload["status"] == "duplicate"
    assert duplicate_payload["parameter_name"] == "region"
    assert dependencies.api_lookup_calls == 3
    assert dependencies.returned_api_card_ids == {card.card_id}
    assert dependencies.returned_api_parameter_keys == {
        (card.card_id, "directionVector"),
        (card.card_id, "region"),
    }
    traces = [
        ApiLookupTrace.model_validate_json(line)
        for line in (request.state_dir / "api_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [trace.counted for trace in traces] == [True, True, True, False]
    assert traces[1].delivered_note_ids == ()
    assert traces[2].delivered_note_ids == (note.note_id,)


def test_cae_error_reports_stage_failure_and_attaches_exact_api_hint(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    (request.workspace / "build.py").write_text(
        "# line\n" * 7
        + "model = mdb.models['Model-1']\n"
        + "part = model.Part(name='Plate')\n"
        + "part.seedEdgeByNumber(edges=(bottom, top), number=20)\n",
        encoding="utf-8",
    )
    catalog = ApiCatalog(
        [
            ApiMethodCard(
                card_id="api:2024:MeshPart.seedEdgeByNumber",
                entity_kind="method",
                canonical_name="Part.seedEdgeByNumber",
                declaring_name="MeshPart.seedEdgeByNumber",
                class_name="MeshPart",
                method_name="seedEdgeByNumber",
                public_names=("Part.seedEdgeByNumber",),
                runtime_notes=(
                    ApiRuntimeNote(
                        note_id="runtime:2024:Part.seedEdgeByNumber:verified",
                        qualified_name="Part.seedEdgeByNumber",
                        runtime_release="2024",
                        summary="Verified note.",
                        guidance="Use verified guidance.",
                        evidence_ref="evidence/verified.json",
                        verified=True,
                        source=ApiSourceRef(
                            kind="runtime",
                            release="2024",
                            source_path="evidence/verified.json",
                        ),
                    ),
                    ApiRuntimeNote(
                        note_id="runtime:2024:Part.seedEdgeByNumber:advisory",
                        qualified_name="Part.seedEdgeByNumber",
                        runtime_release="2024",
                        summary="Unverified note.",
                        guidance="Do not inject unverified guidance automatically.",
                        evidence_ref="evidence/advisory.json",
                        verified=False,
                        source=ApiSourceRef(
                            kind="runtime",
                            release="2024",
                            source_path="evidence/advisory.json",
                        ),
                    ),
                ),
            )
        ],
        runtime_release="2024",
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=catalog,
    )
    execution = AbaqusExecutionResult(
        invocation=AbaqusInvocation.CAE_NOGUI,
        command=("abq2024.bat", "cae", "noGUI=build.py"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="Abaqus Error: cae exited with an error.\n",
        stderr=(
            "TypeError: found GeomSequence, expected Vertex\n"
            'File "build.py", line 10, in <module>\n'
            "    part.seedEdgeByNumber(edges=(bottom, top), number=20)\n"
        ),
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
    )

    payload = _tool_payload(execution)
    hint = _runtime_api_hint(dependencies, execution)

    assert payload["process_succeeded"] is True
    assert payload["abaqus_error_detected"] is True
    assert payload["stage_succeeded"] is False
    assert payload["succeeded"] is False
    assert hint is not None
    assert hint["status"] == "found"
    assert "Use verified guidance." in str(hint)
    assert "evidence/verified.json" in str(hint)
    assert "Do not inject unverified guidance automatically." not in str(hint)
    assert dependencies.api_lookup_calls == 1
    traces = [
        ApiLookupTrace.model_validate_json(line)
        for line in (request.state_dir / "api_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(traces) == 1
    assert traces[0].source == "automatic"
    assert traces[0].counted is True

    solver_execution = execution.model_copy(
        update={"invocation": AbaqusInvocation.SOLVER_JOB}
    )
    assert _runtime_api_hint(dependencies, solver_execution) is None
    non_api_error = execution.model_copy(
        update={
            "stderr": (
                "RuntimeError: license checkout failed\n"
                "    part.seedEdgeByNumber(edges=edges, number=20)\n"
            )
        }
    )
    assert _runtime_api_hint(dependencies, non_api_error) is None
    assert dependencies.api_lookup_calls == 1


def test_automatic_verified_only_hint_is_compact_and_fingerprint_is_soft(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    (request.workspace / "build.py").write_text(
        "# line\n" * 8
        + "model = mdb.models['Model-1']\n"
        + "part = model.Part(name='Plate')\n"
        + "part.seedEdgeByNumber(edges=(bottom, top), number=20)\n",
        encoding="utf-8",
    )
    verified_note_id = "runtime:2024:Part.seedEdgeByNumber:verified"
    mismatch_note_id = "runtime:2024:Part.seedEdgeByNumber:english-fingerprint"
    catalog = ApiCatalog(
        [
            ApiMethodCard(
                card_id="api:2024:MeshPart.seedEdgeByNumber",
                entity_kind="method",
                canonical_name="Part.seedEdgeByNumber",
                declaring_name="MeshPart.seedEdgeByNumber",
                class_name="MeshPart",
                method_name="seedEdgeByNumber",
                public_names=("Part.seedEdgeByNumber",),
                signatures=(
                    ApiSignature(
                        text="seedEdgeByNumber(edges, number, constraint=FREE)"
                    ),
                ),
                official_records=(
                    OfficialApiRecord(
                        record_id="official:2026:seedEdgeByNumber",
                        entity_kind="method",
                        owner_name="Part",
                        callable_name="seedEdgeByNumber",
                        canonical_name="Part.seedEdgeByNumber",
                        access=(
                            "import part mdb.models[name].parts[name].seedEdgeByNumber",
                        ),
                        source=ApiSourceRef(
                            kind="official",
                            release="2026 FD02",
                            source_path="SIMACAEKERRefMap/seed-edge.htm",
                            source_url="http://localhost:4040/seed-edge.htm",
                        ),
                    ),
                ),
                runtime_notes=(
                    ApiRuntimeNote(
                        note_id=verified_note_id,
                        qualified_name="Part.seedEdgeByNumber",
                        runtime_release="2024",
                        summary="General verified note.",
                        guidance="Pass a consumer-compatible edge sequence.",
                        evidence_ref="evidence/general.json",
                        verified=True,
                        source=ApiSourceRef(
                            kind="runtime",
                            release="2024",
                            source_path="evidence/general.json",
                        ),
                    ),
                    ApiRuntimeNote(
                        note_id=mismatch_note_id,
                        qualified_name="Part.seedEdgeByNumber",
                        runtime_release="2024",
                        summary="English fingerprint note.",
                        guidance="Do not wrap a native sequence in another tuple.",
                        evidence_ref="evidence/english.json",
                        error_fingerprint="found GeomSequence, expected Vertex",
                        verified=True,
                        source=ApiSourceRef(
                            kind="runtime",
                            release="2024",
                            source_path="evidence/english.json",
                        ),
                    ),
                    ApiRuntimeNote(
                        note_id="runtime:2024:Part.seedEdgeByNumber:unverified",
                        qualified_name="Part.seedEdgeByNumber",
                        runtime_release="2024",
                        summary="Unverified note.",
                        guidance="Never deliver this note automatically.",
                        evidence_ref="evidence/unverified.json",
                        verified=False,
                        source=ApiSourceRef(
                            kind="runtime",
                            release="2024",
                            source_path="evidence/unverified.json",
                        ),
                    ),
                ),
            )
        ],
        runtime_release="2024",
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=catalog,
    )
    execution = AbaqusExecutionResult(
        invocation=AbaqusInvocation.CAE_NOGUI,
        command=("abq2024.bat", "cae", "noGUI=build.py"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="Abaqus Error: cae exited with an error.\n",
        stderr=(
            "TypeError: 参数类型错误\n"
            'File "build.py", line 11, in <module>\n'
            "    part.seedEdgeByNumber(edges=(bottom, top), number=20)\n"
        ),
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
    )

    hint = _runtime_api_hint(
        dependencies,
        execution,
        mode="automatic_verified_only",
    )

    assert hint is not None
    assert hint["status"] == "found"
    assert hint["query"] == "Part.seedEdgeByNumber"
    assert hint["note_ids"] == [verified_note_id, mismatch_note_id]
    assert hint["signature"] == "seedEdgeByNumber(edges, number, constraint=FREE)"
    assert hint["imports"] == ["part"]
    assert "Do not wrap a native sequence" in str(hint)
    assert "Never deliver this note automatically" not in str(hint)
    assert "cards" not in hint
    assert "source_urls" not in str(hint)
    assert "localhost:4040" not in str(hint)
    traces = [
        ApiLookupTrace.model_validate_json(line)
        for line in (request.state_dir / "api_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(traces) == 1
    assert traces[0].delivered_note_ids == (verified_note_id, mismatch_note_id)
    assert traces[0].source == "automatic"
    assert traces[0].counted is True


def test_automatic_verified_only_returns_no_hint_without_verified_note(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    (request.workspace / "build.py").write_text(
        "# line\n" * 8
        + "model = mdb.models['Model-1']\n"
        + "part = model.Part(name='Plate')\n"
        + "part.Set(name='EDGE', edges=edges)\n",
        encoding="utf-8",
    )
    card = ApiMethodCard(
        card_id="api:2024:RegionPart.Set",
        entity_kind="method",
        canonical_name="Part.Set",
        declaring_name="RegionPart.Set",
        class_name="RegionPart",
        method_name="Set",
        public_names=("Part.Set",),
        runtime_notes=(
            ApiRuntimeNote(
                note_id="runtime:2024:Part.Set:unverified",
                qualified_name="Part.Set",
                runtime_release="2024",
                summary="Unverified note.",
                guidance="This must not be injected.",
                evidence_ref="evidence/unverified.json",
                verified=False,
                source=ApiSourceRef(
                    kind="runtime",
                    release="2024",
                    source_path="evidence/unverified.json",
                ),
            ),
        ),
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=ApiCatalog([card], runtime_release="2024"),
    )
    execution = AbaqusExecutionResult(
        invocation=AbaqusInvocation.CAE_NOGUI,
        command=("abq2024.bat", "cae", "noGUI=build.py"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="Abaqus Error: cae exited with an error.\n",
        stderr=(
            "TypeError: invalid region\n"
            'File "build.py", line 11, in <module>\n'
            "    part.Set(name='EDGE', edges=edges)\n"
        ),
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
    )

    hint = _runtime_api_hint(
        dependencies,
        execution,
        mode="automatic_verified_only",
    )

    assert hint is None
    assert dependencies.api_lookup_calls == 0
    assert dependencies.api_lookup_queries == set()
    assert not (request.state_dir / "api_lookups.jsonl").exists()


def test_automatic_exact_signature_returns_compact_unique_card_without_note(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    (request.workspace / "build.py").write_text(
        "model = mdb.models['Model-1']\n"
        "model.BeamSection(\n"
        "    name='Beam', integration=BEFORE_ANALYSIS,\n"
        "    profile='Rect', orientation=(0.0, 0.0, 1.0),\n"
        ")\n",
        encoding="utf-8",
    )
    card = ApiMethodCard(
        card_id="api:2024:SectionModel.BeamSection",
        entity_kind="constructor",
        canonical_name="Model.BeamSection",
        declaring_name="SectionModel.BeamSection",
        class_name="SectionModel",
        method_name="BeamSection",
        public_names=("Model.BeamSection",),
        signatures=(
            ApiSignature(
                text=(
                    "BeamSection(name, integration, profile, "
                    "poissonRatio=0, material='')"
                )
            ),
        ),
        official_records=(
            OfficialApiRecord(
                record_id="official:2026:BeamSection",
                entity_kind="constructor",
                owner_name="Model",
                callable_name="BeamSection",
                canonical_name="Model.BeamSection",
                access=("import section mdb.models[name].BeamSection",),
                source=ApiSourceRef(
                    kind="official",
                    release="2026 FD02",
                    source_path="SIMACAEKERRefMap/beam-section.htm",
                ),
            ),
        ),
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        api_catalog=ApiCatalog([card], runtime_release="2024"),
    )
    execution = AbaqusExecutionResult(
        invocation=AbaqusInvocation.CAE_NOGUI,
        command=("abq2024.bat", "cae", "noGUI=build.py"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="Abaqus Error: cae exited with an error.\n",
        stderr=(
            "TypeError: 关键字错误: orientation\n"
            'File "build.py", line 2, in <module>\n'
            "    model.BeamSection(\n"
        ),
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
    )

    hint = _runtime_api_hint(
        dependencies,
        execution,
        mode="automatic_exact_signature",
    )

    assert hint == {
        "status": "found",
        "query": "Model.BeamSection",
        "card_id": card.card_id,
        "signature": (
            "BeamSection(name, integration, profile, poissonRatio=0, material='')"
        ),
        "guidance": (
            "Use this exact callable signature to remove unsupported arguments. "
            "Preserve user physics inputs and the runtime receiver's ownership."
        ),
        "imports": ["section"],
    }
    assert dependencies.api_lookup_calls == 1
    assert dependencies.api_lookup_queries == {"Model.BeamSection"}
    traces = [
        ApiLookupTrace.model_validate_json(line)
        for line in (request.state_dir / "api_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(traces) == 1
    assert traces[0].source == "automatic"
    assert traces[0].card_ids == (card.card_id,)
    assert traces[0].delivered_note_ids == ()
    assert traces[0].counted is True


def test_exit_zero_python_traceback_is_not_a_successful_stage(tmp_path: Path) -> None:
    request = _request(tmp_path)
    base = AbaqusExecutionResult(
        invocation=AbaqusInvocation.ABAQUS_PYTHON,
        command=("abq2024.bat", "python", "extract_results.py"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="",
        stderr=(
            "Traceback (most recent call last):\n"
            '  File "extract_results.py", line 8, in <module>\n'
            "KeyError: 'Tension'\n"
        ),
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
    )

    for invocation in (
        AbaqusInvocation.ABAQUS_PYTHON,
        AbaqusInvocation.CAE_NOGUI,
    ):
        payload = _tool_payload(base.model_copy(update={"invocation": invocation}))
        assert payload["process_succeeded"] is True
        assert payload["python_traceback_detected"] is True
        assert payload["stage_succeeded"] is False
        assert payload["succeeded"] is False

    solver_payload = _tool_payload(
        base.model_copy(update={"invocation": AbaqusInvocation.SOLVER_JOB})
    )
    assert solver_payload["python_traceback_detected"] is False


def test_solver_tool_payload_includes_high_signal_diagnostics(tmp_path: Path) -> None:
    request = _request(tmp_path)
    execution = AbaqusExecutionResult(
        invocation=AbaqusInvocation.SOLVER_JOB,
        command=("abq2024.bat", "job=plate", "input=plate.inp", "interactive"),
        workspace=request.workspace,
        exit_code=1,
        timed_out=False,
        duration_seconds=1.0,
        stdout="Abaqus Error: Analysis Input File Processor exited with an error.",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
        diagnostics=(
            AbaqusDiagnostic(
                level="error",
                source="plate.dat",
                line=42,
                message="80 elements have missing property definitions",
            ),
        ),
    )

    payload = _tool_payload(execution)

    assert payload["diagnostics"] == [
        {
            "level": "error",
            "source": "plate.dat",
            "line": 42,
            "message": "80 elements have missing property definitions",
        }
    ]

    cae_payload = _tool_payload(
        execution.model_copy(update={"invocation": AbaqusInvocation.CAE_NOGUI})
    )
    assert "diagnostics" not in cae_payload


@pytest.mark.parametrize("tool_name", ["run_cae_script", "run_abaqus_python"])
def test_syntax_blocks_runtime_without_static_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    request = _request(tmp_path)
    script = request.workspace / "draft.py"
    script.write_text("def broken(:\n", encoding="utf-8")
    observed = []

    async def must_not_run(*args, **kwargs):
        raise AssertionError("invalid syntax reached vendor runtime")

    monkeypatch.setattr(Abaqus2024Runner, tool_name, must_not_run)

    def model_function(messages, info):
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
        ]
        if not returned:
            return ModelResponse(
                parts=[ToolCallPart(tool_name, {"script_path": "draft.py"})]
            )
        observed.append(returned[-1].content)
        output = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output.name,
                    {
                        "status": "failed",
                        "failure_stage": "syntax",
                        "summary": "Invalid draft.",
                    },
                )
            ]
        )

    deps = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(request.workspace, request.state_dir)
    )
    agent = create_abaqus_coding_agent(FunctionModel(model_function), request)
    asyncio.run(agent.run(request.goal, deps=deps))
    assert observed[0]["error_type"] == "PythonSyntaxRuntimeDenied"
    assert observed[0]["diagnostic"]["line"] == 1
    assert observed[0]["runtime_executed"] is False
    assert deps.abaqus_calls == 0
    assert deps.static_preflight_calls == 0
    assert script.read_text(encoding="utf-8") == "def broken(:\n"


def test_analysis_cache_and_usage_survive_restore(tmp_path: Path) -> None:
    from abaqus_sim_agent.coding.resume import DependencyResumeState

    binding = _feedback_binding()
    request = _request(tmp_path, static_preflight=binding)
    script = request.workspace / "build_model.py"
    script.write_text("value = missing_name\n", encoding="utf-8")
    source = _prepared_static_source(tmp_path, binding)
    process = _StaticPreflightProcess()

    def dependencies():
        return CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            static_preflight=AbqpyBasedPyrightPreflight(
                source, request.state_dir, process_strategy=process
            ),
        )

    original = dependencies()
    block, first = asyncio.run(
        _python_checks_before_runtime(original, kind="cae", script_path=script.name)
    )
    assert block is None
    snapshot = DependencyResumeState.capture(original)
    resumed = dependencies()
    snapshot.restore_into(resumed)
    block, second = asyncio.run(
        _python_checks_before_runtime(resumed, kind="cae", script_path=script.name)
    )
    assert block is None
    assert second == first
    assert process.calls == 1
    assert resumed.static_preflight_calls == 1
    assert resumed.syntax_gate is not None
    assert resumed.syntax_gate.runtime_checks == 2


@pytest.mark.parametrize("probe_mode", ["off", "typed"])
def test_solver_resource_tool_validation_precedes_execution(tmp_path, probe_mode):
    from pydantic_ai import Agent

    from abaqus_sim_agent.coding.runtime_tools import register_runtime_tools

    request = _request(tmp_path)
    (request.workspace / "demo.inp").write_text("*HEADING\n", encoding="ascii")
    commands = []
    calls = 0

    class Process:
        async def run(self, command, *, cwd, timeout_seconds):
            commands.append(tuple(command))
            return ProcessOutput(exit_code=0, stdout=b"", stderr=b"")

    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls <= 2:
            definition = next(
                t for t in info.function_tools if t.name == "run_solver_job"
            )
            assert {"cpus", "memory"} <= definition.parameters_json_schema[
                "properties"
            ].keys()
            assert "cpus" not in definition.parameters_json_schema["required"]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_solver_job",
                        {
                            "job_name": "demo",
                            "input_file": "demo.inp",
                            "cpus": 0 if calls == 1 else 4,
                            "memory": "50%",
                        },
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "status": "failed",
                        "failure_stage": "fixture",
                        "summary": "Resource arguments inspected.",
                    },
                )
            ]
        )

    agent = Agent(
        FunctionModel(respond),
        deps_type=CodingAgentDependencies,
        output_type=FailedDecision,
    )
    register_runtime_tools(
        agent,
        probe_protocol_mode=probe_mode,
        probe_target_scripts=None,
        api_catalog_mode="off",
        diagnostic_retrieval_mode="off",
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(
            request.workspace,
            request.state_dir,
            process_strategy=Process(),
        ),
    )
    agent.run_sync("Run the input with explicit resources.", deps=dependencies)
    assert calls == 3
    assert dependencies.solver_calls == 1
    assert len(commands) == 1
    assert commands[0][-2:] == ("cpus=4", "memory=50%")
    assert dependencies.executions[0].command == commands[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize(
    ("job_name", "input_file"),
    [
        ("bad name", "demo.inp"),
        ("demo", "missing.inp"),
        ("demo", "wrong.txt"),
        ("demo", "../outside.inp"),
        ("demo", "directory.inp"),
    ],
)
async def test_solver_prevalidation_failure_preserves_runtime_budget(
    tmp_path, background, job_name, input_file
):
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from abaqus_sim_agent.coding.runtime_tools import (
        _run_solver_tool,
        _submit_solver_tool,
        _wait_solver_tool,
    )

    request = _request(tmp_path)
    for name in ("demo.inp", "wrong.txt"):
        (request.workspace / name).write_text("*HEADING\n", encoding="ascii")
    (tmp_path / "outside.inp").write_text("*HEADING\n", encoding="ascii")
    (request.workspace / "directory.inp").mkdir()
    commands = []

    class Process:
        async def run(self, command, *, cwd, timeout_seconds):
            commands.append(tuple(command))
            return ProcessOutput(exit_code=0, stdout=b"", stderr=b"")

    runner = Abaqus2024Runner(
        request.workspace, request.state_dir, process_strategy=Process()
    )
    deps = CodingAgentDependencies(request=request, runner=runner)
    # Existing usage must survive both a rejected call and the following valid call.
    deps.abaqus_calls = 2
    deps.solver_calls = 1
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    async def call(job, inp):
        if background:
            return await _submit_solver_tool(deps, job, inp)
        return await _run_solver_tool(
            ctx,
            job,
            inp,
            probe_protocol_enabled=False,
            probe_result=None,
            probe_target_scripts=None,
            api_catalog_mode="off",
            diagnostic_retrieval_mode="off",
        )

    rejected = await call(job_name, input_file)
    assert rejected["succeeded"] is False
    assert rejected["error_type"] in {"ValueError", "FileNotFoundError"}
    assert (deps.abaqus_calls, deps.solver_calls) == (2, 1)
    assert not commands
    assert not runner._solver_tasks
    assert not deps.pending_solver_executions
    assert not deps.executions

    accepted = await call("demo", "demo.inp")
    if background:
        accepted = await _wait_solver_tool(
            deps,
            str(accepted["execution_id"]),
            api_catalog_mode="off",
            diagnostic_retrieval_mode="off",
        )
    assert accepted["succeeded"] is True
    assert (deps.abaqus_calls, deps.solver_calls) == (3, 2)
    assert len(commands) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("process_error", [False, True])
async def test_solver_failure_after_prevalidation_still_consumes_budget(
    tmp_path, background, process_error
):
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from abaqus_sim_agent.coding.runtime_tools import (
        _run_solver_tool,
        _submit_solver_tool,
        _wait_solver_tool,
    )

    request = _request(tmp_path)
    (request.workspace / "demo.inp").write_text("*HEADING\n", encoding="ascii")

    class Process:
        async def run(self, command, *, cwd, timeout_seconds):
            if process_error:
                raise OSError("process creation failed")
            return ProcessOutput(exit_code=1, stdout=b"", stderr=b"solver failed")

    deps = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(
            request.workspace, request.state_dir, process_strategy=Process()
        ),
    )
    if background:
        submitted = await _submit_solver_tool(deps, "demo", "demo.inp")
        result = await _wait_solver_tool(
            deps,
            str(submitted["execution_id"]),
            api_catalog_mode="off",
            diagnostic_retrieval_mode="off",
        )
    else:
        result = await _run_solver_tool(
            RunContext(deps=deps, model=TestModel(), usage=RunUsage()),
            "demo",
            "demo.inp",
            probe_protocol_enabled=False,
            probe_result=None,
            probe_target_scripts=None,
            api_catalog_mode="off",
            diagnostic_retrieval_mode="off",
        )
    assert result["succeeded"] is False
    assert (deps.abaqus_calls, deps.solver_calls) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cae", "extract", "solver", "submit"])
@pytest.mark.parametrize("permission_denied", [False, True])
async def test_runtime_policy_denial_is_local_and_does_not_spend_allowance(
    tmp_path, kind, permission_denied
):
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from abaqus_sim_agent.coding.runtime_tools import (
        _run_abaqus_python_tool,
        _run_cae_tool,
        _run_solver_tool,
        _submit_solver_tool,
    )

    solver = kind in {"solver", "submit"}
    request = _request(
        tmp_path,
        budget=CodingRunBudget(
            max_abaqus_calls=3 if solver else (0 if permission_denied else 1),
            max_solver_calls=(0 if permission_denied else 1) if solver else 0,
        ),
    )
    deps = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(request.workspace, request.state_dir)
    )
    if not permission_denied:
        deps.abaqus_calls = 1
        deps.solver_calls = 1 if solver else 0
    before = deps.abaqus_calls, deps.solver_calls
    common = {
        "probe_protocol_enabled": False,
        "probe_result": None,
        "probe_target_scripts": None,
        "api_catalog_mode": "off",
    }
    if kind == "cae":
        result = await _run_cae_tool(deps, "missing.py", purpose="production", **common)
    elif kind == "extract":
        result = await _run_abaqus_python_tool(
            deps, "missing.py", purpose="production", **common
        )
    elif kind == "solver":
        result = await _run_solver_tool(
            RunContext(deps=deps, model=TestModel(), usage=RunUsage()),
            "job",
            "missing.inp",
            diagnostic_retrieval_mode="off",
            **common,
        )
    else:
        result = await _submit_solver_tool(deps, "job", "missing.inp")
    assert result["error_type"] == "RuntimePolicyDenied"
    assert result["blocked"] is True
    assert result["budget_consumed"] is False
    assert result["runtime_executed"] is False
    assert (deps.abaqus_calls, deps.solver_calls) == before
    assert not deps.executions
    assert not deps.pending_solver_executions
