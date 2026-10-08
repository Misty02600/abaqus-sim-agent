import asyncio
from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.coder._instructions import INSTRUCTIONS as CODER_INSTRUCTIONS

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.read_model import message_views
from abaqus_sim_agent.coding.tool_selection import resolve_tool_names
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path
from tests.support.web import NoProcess


def test_product_preserves_native_guidance_and_interleaved_messages(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "facts.txt").write_text("A supplied fact", encoding="utf-8")
    request = CodingRunRequest(
        run_id="product-prompt",
        goal="Read facts.txt and explain it without running Abaqus",
        workspace=workspace,
        state_dir=tmp_path / "state",
        runtime=RuntimeProfile(launcher_path=Path("abq2024")),
    )
    captured = []
    selected = resolve_tool_names()

    def model(messages, info):
        captured.append(info.instructions)
        assert {t.name for t in info.function_tools} == set(selected)
        returned = any(
            isinstance(p, ToolReturnPart) and p.tool_name == "read_file"
            for m in messages
            for p in m.parts
        )
        if not returned:
            return ModelResponse(
                parts=[
                    TextPart("我先核查已有文件。"),
                    ToolCallPart("read_file", {"path": "facts.txt"}),
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result_ResponseDecision",
                    {"summary": "已核查文件；未运行 Abaqus。"},
                )
            ]
        )

    agent = create_abaqus_coding_agent(
        FunctionModel(model),
        request,
        skill_library=skill_library_path(),
        tools=selected,
        question_protocol="ask_user_v1",
        response_enabled=True,
        product_coding_tools=True,
    )
    deps = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(
            workspace, request.state_dir, process_strategy=NoProcess()
        ),
    )
    result = asyncio.run(agent.run(request.goal, deps=deps))
    for instructions in captured:
        assert instructions.count(CODER_INSTRUCTIONS) == 1
        normalized = " ".join(instructions.split())
        assert "never missing physical inputs" in normalized
        assert "not proof of physical correctness" in normalized
    assert len(captured) == 2
    assert deps.abaqus_calls == 0
    view = message_views(request.run_id, request.goal, result.all_messages_json(), None)
    assert [(m.kind, m.text) for m in view if m.role == "assistant"] == [
        ("text", "我先核查已有文件。"),
        ("tool", ""),
        ("text", "已核查文件；未运行 Abaqus。"),
    ]
