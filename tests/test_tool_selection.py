import asyncio
import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.api_catalog import (
    ApiCatalog,
    ApiMethodCard,
    ApiRuntimeNote,
    ApiSourceRef,
)
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.cli import _validate_resume_arguments, build_parser
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.options import RunOptions, merge_run_options
from abaqus_sim_agent.coding.tool_selection import (
    PROTOCOL_TOOLS,
    resolve_tool_names,
)
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.execution.abaqus2024 import ProcessOutput
from abaqus_sim_agent.skills import skill_library_path
from tests.test_coding_agent import _request
from tests.test_execution_config import launch


def _finish(info):
    name = next(t.name for t in info.output_tools if t.name.endswith("FailedDecision"))
    return ModelResponse(
        parts=[
            ToolCallPart(
                name,
                {
                    "status": "failed",
                    "failure_stage": "fixture",
                    "summary": "Inspected tools.",
                },
            )
        ]
    )


@pytest.mark.parametrize("discover", [False, True])
def test_selected_tools_and_api_instructions_match(tmp_path, discover):
    selected = resolve_tool_names()
    if discover:
        selected = resolve_tool_names((*selected, "discover_abaqus_api"))
    request = _request(tmp_path)

    def model(_messages, info):
        assert {tool.name for tool in info.function_tools} == set(selected)
        assert ("discover_abaqus_api" in info.instructions) == discover
        assert "lookup_abaqus_api" in info.instructions
        return _finish(info)

    agent = create_abaqus_coding_agent(
        FunctionModel(model),
        request,
        tools=selected,
        skill_library=skill_library_path(),
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )
    dependencies = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(request.workspace, request.state_dir)
    )
    asyncio.run(agent.run(request.goal, deps=dependencies))


def test_disabled_tool_is_not_advertised_or_executed(tmp_path):
    selected = resolve_tool_names(("read_file",))
    request = _request(tmp_path)
    attempts = 0

    def model(messages, info):
        nonlocal attempts
        assert {tool.name for tool in info.function_tools} == set(selected)
        assert "lookup_abaqus_api" not in info.instructions
        assert "discover_abaqus_api" not in info.instructions
        attempts += 1
        if attempts == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "forbidden.txt", "content": "must not write"},
                    )
                ]
            )
        assert any(
            getattr(part, "part_kind", None) == "retry-prompt"
            for message in messages
            for part in message.parts
        )
        return _finish(info)

    agent = create_abaqus_coding_agent(
        FunctionModel(model),
        request,
        tools=selected,
        skill_library=skill_library_path(),
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )
    dependencies = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(request.workspace, request.state_dir)
    )
    asyncio.run(agent.run(request.goal, deps=dependencies))
    assert attempts == 2
    assert not (request.workspace / "forbidden.txt").exists()


def test_runtime_error_hint_remains_when_active_lookup_is_disabled(tmp_path):
    request = _request(tmp_path)
    script = request.workspace / "build_model.py"
    script.write_text(
        "model = mdb.Model(name='M')\npart = model.Part(name='P')\npart.setElementType(regions=(part.cells,), elemTypes=())\n",
        encoding="utf-8",
    )
    note = ApiRuntimeNote(
        note_id="runtime:2024:region",
        qualified_name="Part.setElementType",
        runtime_release="2024",
        summary="Region dimensions",
        guidance="Select faces for planar geometry.",
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
                runtime_notes=(note,),
            )
        ],
        runtime_release="2024",
    )

    class FailedProcess:
        async def run(self, command, *, cwd, timeout_seconds):
            return ProcessOutput(
                exit_code=0,
                stdout=b"Abaqus Error: cae exited with an error.\n",
                stderr=b'File "build_model.py", line 3, in <module>\n    part.setElementType(\n',
            )

    calls = 0

    def model(messages, info):
        nonlocal calls
        calls += 1
        assert "lookup_abaqus_api" not in {tool.name for tool in info.function_tools}
        if calls == 1:
            return ModelResponse(
                parts=[ToolCallPart("run_cae_script", {"script_path": script.name})]
            )
        result = next(
            part.content
            for message in reversed(messages)
            for part in message.parts
            if getattr(part, "tool_name", None) == "run_cae_script"
            and getattr(part, "part_kind", None) == "tool-return"
        )
        assert result["stage_succeeded"] is False
        assert result["api_lookup"]["query"] == "Part.setElementType"
        assert note.guidance in str(result["api_lookup"])
        return _finish(info)

    agent = create_abaqus_coding_agent(
        FunctionModel(model),
        request,
        tools=resolve_tool_names(("run_cae_script",)),
        skill_library=skill_library_path(),
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )
    dependencies = CodingAgentDependencies(
        request=request,
        api_catalog=catalog,
        runner=Abaqus2024Runner(
            request.workspace, request.state_dir, process_strategy=FailedProcess()
        ),
    )
    asyncio.run(agent.run(request.goal, deps=dependencies))
    trace = json.loads(
        (request.state_dir / "api_lookups.jsonl").read_text(encoding="utf-8")
    )
    assert trace["source"] == "automatic"
    assert calls == 2


@pytest.mark.parametrize(
    "names", [("typo",), ("read_file", "read_file"), ("discover_abaqus_api",)]
)
def test_invalid_selection_is_rejected(names):
    with pytest.raises(ValueError, match=r"unknown|unique|requires lookup"):
        resolve_tool_names(names)


def test_resource_dependencies_and_protocol_tools_are_preserved():
    assert set(resolve_tool_names(())) == set(PROTOCOL_TOOLS)
    with pytest.raises(ValueError, match="unavailable"):
        resolve_tool_names(("knowledge_read_file",))
    with pytest.raises(ValueError, match="protocol"):
        resolve_tool_names(excluded=("ask_user",))
    with pytest.raises(ValueError, match="requires lookup"):
        resolve_tool_names(
            ("lookup_abaqus_api", "discover_abaqus_api"), ("lookup_abaqus_api",)
        )


def test_background_solver_instructions_do_not_depend_on_shell_selection(tmp_path):
    request = _request(tmp_path)
    selected = resolve_tool_names(("solver_job",))

    def model(_messages, info):
        assert {tool.name for tool in info.function_tools} == set(selected)
        assert "Use `solver_job`" in info.instructions
        assert "Use `run_solver_job`" not in info.instructions
        return _finish(info)

    agent = create_abaqus_coding_agent(
        FunctionModel(model),
        request,
        tools=selected,
        skill_library=skill_library_path(),
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )
    dependencies = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(request.workspace, request.state_dir)
    )
    asyncio.run(agent.run(request.goal, deps=dependencies))


def test_launch_keeps_actual_tools_and_rejects_old_product_contract(
    tmp_path, monkeypatch
):
    original = launch(tmp_path)
    data = original.model_dump_json()
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.tool_selection.resolve_tool_names",
        lambda *args, **kwargs: (),
    )
    assert type(original).model_validate_json(data).tools == original.tools
    assert "api_catalog_mode" not in json.loads(data)
    with pytest.raises(ValidationError):
        type(original).model_validate_json(
            data.replace("coding-run-launch-v11", "coding-run-launch-v10")
        )


def test_explicit_empty_tools_replace_startup_selection():
    merged = merge_run_options(RunOptions(tools=("read_file",)), RunOptions(tools=()))
    assert merged.tools == ()


@pytest.mark.parametrize("flag", ["--tools", "--exclude-tools"])
def test_resume_does_not_silently_replace_tools(flag):
    args = build_parser().parse_args(["--resume", "state", flag, "read_file"])
    with pytest.raises(ValueError, match=flag):
        _validate_resume_arguments(args)
