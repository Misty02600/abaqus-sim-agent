import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.api_catalog import (
    ApiCatalog,
    ApiMethodCard,
    ApiSourceRef,
    OfficialApiRecord,
)
from abaqus_sim_agent.api_catalog.discovery import CatalogDiscovery
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.execution import Abaqus2024Runner


def registration():
    path = (
        Path(__file__).parents[1] / "benchmarks/api_catalog/run_api_discovery_loop.py"
    )
    spec = importlib.util.spec_from_file_location("discovery_loop_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.register_discovery


class Encoder:
    def __init__(self):
        self.binding = {"fixture": "v1"}

    def encode(self, texts, *, query):
        return np.asarray([[1.0, 0.0]] * len(texts), dtype=np.float32)


@pytest.mark.asyncio
async def test_discovery_read_budget_and_dedup(tmp_path):
    name = "Part.Set"
    card = ApiMethodCard(
        card_id=name,
        canonical_name=name,
        declaring_name=name,
        entity_kind="method",
        class_name="Part",
        method_name="Set",
        official_records=(
            OfficialApiRecord(
                record_id="fixture",
                entity_kind="method",
                owner_name="Part",
                callable_name="Set",
                canonical_name=name,
                summary="Create a named set",
                source=ApiSourceRef(
                    kind="official", release="2024", source_path="fixture"
                ),
            ),
        ),
    )
    catalog = ApiCatalog([card], runtime_release="2024")
    catalog.write(tmp_path / "catalog")
    index = CatalogDiscovery.build(catalog, tmp_path / "index", encoder=Encoder())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    request = CodingRunRequest(
        run_id="fixture",
        goal="Inspect APIs",
        workspace=workspace,
        state_dir=state,
        runtime=RuntimeProfile(launcher_path=(tmp_path / "fake.bat").resolve()),
        budget=CodingRunBudget(
            max_api_lookup_calls=2, max_abaqus_calls=0, max_solver_calls=0
        ),
    )
    count = 0
    returned = []
    calls = [
        ("discover_abaqus_api", {"operation": "create a named set"}),
        ("lookup_abaqus_api", {"name": "Part.Set"}),
        ("discover_abaqus_api", {"operation": "create a named set"}),
        ("discover_abaqus_api", {"operation": "another operation"}),
    ]

    def reply(messages, info):
        nonlocal count
        if count:
            returned.extend(
                p.content for p in messages[-1].parts if isinstance(p, ToolReturnPart)
            )
        if count < len(calls):
            tool, args = calls[count]
        else:
            tool, args = (
                "final_result_FailedDecision",
                {"status": "failed", "failure_stage": "fixture", "summary": "fixture"},
            )
        count += 1
        return ModelResponse(parts=[ToolCallPart(tool, args, tool_call_id=str(count))])

    agent = create_abaqus_coding_agent(
        FunctionModel(reply),
        request,
        api_catalog_mode="full",
        question_protocol="ask_user_v1",
    )
    registration()(agent, index, Encoder(), "Original request stays intact")
    deps = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(workspace, state), api_catalog=catalog
    )
    completed = await agent.run("fixture", deps=deps)
    assert completed.usage.requests == 5
    assert completed.usage.input_tokens >= 0
    assert [r["status"] for r in returned] == [
        "candidates",
        "found",
        "duplicate",
        "unavailable",
    ]
    assert returned[0]["intent"]["original_request"] == "Original request stays intact"
    assert deps.api_lookup_calls == 2
    assert deps.abaqus_calls == 0
    lookup_trace = [
        json.loads(line)
        for line in (state / "api_lookups.jsonl").read_text().splitlines()
    ]
    discovery_trace = [
        json.loads(line)
        for line in (state / "api_discoveries.jsonl").read_text().splitlines()
    ]
    assert sum(row["counted"] for row in lookup_trace) == 1
    assert lookup_trace[0]["query"] == "Part.Set"
    assert sum(row["counted"] for row in discovery_trace) == 1
    assert [row["status"] for row in discovery_trace] == [
        "candidates",
        "duplicate",
        "unavailable",
    ]
