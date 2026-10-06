import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage, UsageLimits

from abaqus_sim_agent.coding.output_recovery import (
    RECOVERY_PROMPT,
    OutputTruncationRecovery,
)


def make_agent(responses):
    sent = []

    def respond(messages, info):
        sent.append(list(messages))
        return responses[len(sent) - 1]

    agent = Agent(
        FunctionModel(respond), capabilities=[OutputTruncationRecovery()], retries=2
    )
    return agent, sent


def truncated(parts=None):
    return ModelResponse(
        parts=parts if parts is not None else [ThinkingPart("synthetic")],
        finish_reason="length",
        usage=RequestUsage(input_tokens=2, output_tokens=8),
    )


@pytest.mark.parametrize(
    "parts",
    [[], [ThinkingPart("synthetic")], [ThinkingPart("synthetic"), TextPart("")]],
)
def test_truncation_retries_once_preserving_history_and_usage(parts):
    agent, sent = make_agent(
        [
            truncated(parts),
            ModelResponse(
                parts=[TextPart("ok")],
                usage=RequestUsage(input_tokens=3, output_tokens=1),
            ),
        ]
    )
    result = agent.run_sync(
        "test", deps=SimpleNamespace(output_truncation_recovery_used=False)
    )
    assert len(sent) == 2
    assert result.output == "ok"
    assert result.usage.requests == 2
    assert result.usage.output_tokens == 9
    assert any(
        isinstance(p, RetryPromptPart) and p.content == RECOVERY_PROMPT
        for m in result.all_messages()
        for p in m.parts
    )


def test_second_truncation_stops_and_preserves_second_response():
    agent, sent = make_agent([truncated(), truncated()])
    with (
        capture_run_messages() as history,
        pytest.raises(UnexpectedModelBehavior, match="token limit"),
    ):
        agent.run_sync(
            "test", deps=SimpleNamespace(output_truncation_recovery_used=False)
        )
    assert len(sent) == 2
    responses = [m for m in history if isinstance(m, ModelResponse)]
    assert len(responses) == 2
    assert sum(m.usage.output_tokens for m in responses) == 16


@pytest.mark.parametrize("compacted", [False, True])
def test_resume_does_not_grant_another_recovery(tmp_path, compacted):
    from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
    from abaqus_sim_agent.coding.resume import (
        CodingRunResumeError,
        DependencyResumeState,
    )
    from abaqus_sim_agent.execution import Abaqus2024Runner
    from tests.test_coding_agent import _request

    request = _request(tmp_path)

    def fresh_dependencies():
        return CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
        )

    dependencies = fresh_dependencies()
    first, _ = make_agent([truncated(), truncated()])
    with capture_run_messages() as history, pytest.raises(UnexpectedModelBehavior):
        first.run_sync("test", deps=dependencies)
    snapshot = DependencyResumeState.capture(dependencies)
    state_file = tmp_path / "dependency-state.json"
    state_file.write_text(snapshot.model_dump_json(), encoding="utf-8")
    persisted = DependencyResumeState.model_validate_json(state_file.read_bytes())
    restored = fresh_dependencies()
    persisted.restore_into(restored)
    assert restored.output_truncation_recovery_used
    with pytest.raises(CodingRunResumeError, match="freshly composed"):
        persisted.restore_into(restored)
    if compacted:
        # A summary may discard every original response and retry prompt.
        history = [ModelRequest(parts=[UserPromptPart("Summary of earlier work.")])]
    resumed, sent = make_agent([truncated()])
    with pytest.raises(UnexpectedModelBehavior, match="token limit"):
        resumed.run_sync(
            "continue",
            message_history=history,
            deps=restored,
        )
    assert len(sent) == 1


def test_recovery_respects_request_budget():
    agent, sent = make_agent([truncated()])
    with pytest.raises(UsageLimitExceeded):
        agent.run_sync(
            "test",
            deps=SimpleNamespace(output_truncation_recovery_used=False),
            usage_limits=UsageLimits(request_limit=1),
        )
    assert len(sent) == 1


def test_partial_text_is_not_retried():
    agent, sent = make_agent([truncated([TextPart("partial text")])])
    result = agent.run_sync(
        "test", deps=SimpleNamespace(output_truncation_recovery_used=False)
    )
    assert result.output == "partial text"
    assert len(sent) == 1


def test_tool_calls_are_not_replayed_by_recovery():
    agent, _sent = make_agent(
        [
            ModelResponse(parts=[ToolCallPart("work", {})], finish_reason="tool_call"),
            ModelResponse(parts=[TextPart("done")]),
        ]
    )
    calls = []

    @agent.tool_plain
    def work() -> str:
        calls.append(1)
        return "ok"

    deps = SimpleNamespace(output_truncation_recovery_used=False)
    agent.run_sync("test", deps=deps)
    assert calls == [1]
    assert not deps.output_truncation_recovery_used


def test_truncated_tool_arguments_are_outside_recovery_scope():
    response = truncated([ToolCallPart("work", '{"text":')])
    deps = SimpleNamespace(output_truncation_recovery_used=False)
    returned = asyncio.run(
        OutputTruncationRecovery().after_model_request(
            SimpleNamespace(deps=deps, messages=[]),
            request_context=None,
            response=response,
        )
    )
    assert returned is response
    assert not deps.output_truncation_recovery_used


def test_product_agent_installs_recovery_for_new_execution_config(tmp_path):
    from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
    from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
    from abaqus_sim_agent.coding.model import DeepSeekGateway, new_run_execution_config
    from abaqus_sim_agent.execution import Abaqus2024Runner
    from tests.test_coding_agent import _request

    request = _request(tmp_path)
    calls = []

    def respond(messages, info):
        calls.append(1)
        if len(calls) == 1:
            return truncated()
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result_FailedDecision",
                    {
                        "status": "failed",
                        "failure_stage": "offline",
                        "summary": "Synthetic test completion",
                    },
                )
            ]
        )

    config = new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO, model_name="deepseek-v4-flash"
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(respond),
        request,
        execution_config=config,
        context_policy_enabled=False,
    )
    deps = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(request.workspace, request.state_dir)
    )
    result = agent.run_sync("test", deps=deps)
    assert len(calls) == 2
    assert result.output.status == "failed"
    assert deps.output_truncation_recovery_used
