import asyncio
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

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.knowledge_files import knowledge_toolset
from abaqus_sim_agent.execution import Abaqus2024Runner


def _request(tmp_path: Path) -> CodingRunRequest:
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    return CodingRunRequest(
        run_id="knowledge-test",
        goal="Inspect reference material only.",
        workspace=workspace.resolve(),
        state_dir=state.resolve(),
        runtime=RuntimeProfile(launcher_path=(tmp_path / "never-run.bat").resolve()),
        budget=CodingRunBudget(max_abaqus_calls=0, max_solver_calls=0),
    )


def _run(request, root, calls):
    seen = set()
    position = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal position
        seen.update(t.name for t in info.function_tools)
        if position < len(calls):
            name, args = calls[position]
            position += 1
            return ModelResponse(
                parts=[ToolCallPart(name, args, tool_call_id=f"call-{position}")]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result_FailedDecision",
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Read-only test complete.",
                    },
                )
            ]
        )

    deps = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(request.workspace, request.state_dir)
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model), request, knowledge_resources_dir=root
    )
    result = asyncio.run(agent.run(request.goal, deps=deps))
    assert deps.abaqus_calls == 0
    return result, seen


def test_reference_read_preserves_source_and_native_history(tmp_path):
    request = _request(tmp_path)
    root = tmp_path / "references"
    root.mkdir()
    script = root / "demo.py"
    original = b"# Only reference\nraise RuntimeError('must not execute')\n"
    script.write_bytes(original)
    (root / "source.json").write_text('{"private":true}')
    result, seen = _run(
        request,
        root,
        [
            ("knowledge_list_directory", {"path": "."}),
            ("knowledge_read_file", {"path": "demo.py", "offset": 1, "limit": 1}),
        ],
    )
    returns = [
        p
        for m in result.all_messages()
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    read = next(p for p in returns if p.tool_name == "knowledge_read_file")
    assert "must not execute" in str(read.content)
    assert "# Only reference" not in str(read.content)
    assert "source.json" not in str(
        next(p.content for p in returns if p.tool_name == "knowledge_list_directory")
    )
    assert {
        "read_file",
        "write_file",
        "knowledge_read_file",
        "knowledge_list_directory",
    } <= seen
    assert (
        not {
            "knowledge_write_file",
            "knowledge_edit_file",
            "knowledge_search_files",
            "run_command",
        }
        & seen
    )
    assert script.read_bytes() == original
    assert not list(request.workspace.iterdir())
    assert list(request.state_dir.rglob("tool_effects.jsonl"))


@pytest.mark.parametrize(
    "args",
    [
        {"path": "../secret.md"},
        {"path": "source.json"},
        {"path": "demo.py", "offset": -1},
        {"path": "demo.py", "limit": 301},
    ],
)
def test_reference_denials_do_not_leak_or_block_later_valid_read(tmp_path, args):
    request = _request(tmp_path)
    root = tmp_path / "references"
    root.mkdir()
    (tmp_path / "secret.md").write_text("private sentinel")
    (root / "source.json").write_text("private sentinel")
    (root / "demo.py").write_text("# allowed sentinel\n")
    result, _ = _run(
        request,
        root,
        [
            ("knowledge_read_file", args),
            ("knowledge_read_file", {"path": "demo.py"}),
        ],
    )
    assert b"private sentinel" not in result.all_messages_json()
    assert b"allowed sentinel" in result.all_messages_json()


@pytest.mark.parametrize("location", ["workspace", "state_dir", "parent"])
def test_reference_root_cannot_overlap_owned_run_paths(tmp_path, location):
    request = _request(tmp_path)
    root = tmp_path if location == "parent" else getattr(request, location)
    with pytest.raises(ValueError, match="overlap"):
        knowledge_toolset(
            root, workspace=request.workspace, state_dir=request.state_dir
        )


def test_reference_tools_default_off(tmp_path):
    _, seen = _run(_request(tmp_path), None, [])
    assert not any(name.startswith("knowledge_") for name in seen)


@pytest.mark.parametrize("outside", [True, False])
def test_symlink_cannot_escape_or_alias_a_disallowed_file(tmp_path, outside):
    request = _request(tmp_path)
    root = tmp_path / "references"
    root.mkdir()
    target = (tmp_path / "secret.md") if outside else (root / "source.json")
    target.write_text("private sentinel")
    try:
        (root / "alias.py").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    result, _ = _run(
        request,
        root,
        [
            ("knowledge_read_file", {"path": "alias.py"}),
            ("knowledge_list_directory", {"path": "."}),
        ],
    )
    assert b"private sentinel" not in result.all_messages_json()
    assert target.read_text() == "private sentinel"
