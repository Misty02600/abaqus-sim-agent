from typing import Any, cast

import pytest
from pydantic_ai.messages import (
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolAvailabilityDeltaPart,
    UserPromptPart,
)
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.compaction import ClearToolResults, TieredCompaction

from abaqus_sim_agent.coding.context_policy import (
    CONTEXT_COMPACTION_KEEP_TOKENS,
    CONTEXT_COMPACTION_TARGET_TOKENS,
    CapabilityPreservingCompaction,
    bind_filesystem_skills,
    build_context_policy,
    validate_filesystem_skill_bindings,
)


class DropPrefix:
    async def compact(
        self,
        messages: list[ModelMessage],
        ctx: RunContext[Any],
    ) -> list[ModelMessage]:
        del messages, ctx
        return [
            ModelRequest(parts=[SystemPromptPart(content="summary")]),
            ModelRequest(parts=[UserPromptPart(content="recent")]),
        ]


@pytest.mark.asyncio
async def test_compaction_restores_typed_capability_activation_capsule() -> None:
    call = LoadCapabilityCallPart(
        tool_call_id="load-1",
        args={"id": "abaqus-2024-coding"},
    )
    result = LoadCapabilityReturnPart(
        tool_call_id="load-1",
        content={"instructions": "Use the typed Abaqus tools."},
    )
    delta = ToolAvailabilityDeltaPart(
        tools_added=["run_cae_script"],
        tool_call_id="load-1",
    )
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="goal")]),
        ModelResponse(parts=[call], model_name="fixture"),
        ModelRequest(parts=[result, delta]),
        ModelResponse(parts=[]),
    ]

    compacted = await CapabilityPreservingCompaction(DropPrefix()).compact(
        history,
        cast(RunContext[Any], None),
    )

    assert isinstance(compacted[0], ModelRequest)
    assert isinstance(compacted[0].parts[0], SystemPromptPart)
    restored_call = compacted[1]
    restored_return = compacted[2]
    assert isinstance(restored_call, ModelResponse)
    assert restored_call.parts == [call]
    assert isinstance(restored_return, ModelRequest)
    assert restored_return.parts == [result, delta]


def test_tiered_context_policy_has_explicit_product_thresholds() -> None:
    policy = build_context_policy()

    assert isinstance(policy, TieredCompaction)
    assert policy.target_tokens == CONTEXT_COMPACTION_TARGET_TOKENS == 960_000
    assert len(policy.tiers) == 2
    assert isinstance(policy.tiers[0], ClearToolResults)
    assert policy.tiers[0].keep_pairs == 3
    assert policy.tiers[0].min_clear_tokens == 16_384
    summarizer = cast(CapabilityPreservingCompaction, policy.tiers[1])
    assert summarizer.inner.keep_tokens == CONTEXT_COMPACTION_KEEP_TOKENS == 32_000


def test_filesystem_skill_binding_changes_with_definition(tmp_path) -> None:
    skill = tmp_path / "abaqus-2024-coding"
    skill.mkdir()
    source = skill / "SKILL.md"
    source.write_text("first", encoding="utf-8")

    first = bind_filesystem_skills(tmp_path, ("abaqus-2024-coding",))
    source.write_text("second", encoding="utf-8")
    second = bind_filesystem_skills(tmp_path, ("abaqus-2024-coding",))

    assert first[0].capability_id == "abaqus-2024-coding"
    assert first[0].definition_sha256 != second[0].definition_sha256
    with pytest.raises(ValueError, match="definition binding changed"):
        validate_filesystem_skill_bindings(tmp_path, first)
