import pytest
from pydantic import ValidationError
from tests.test_coding_amendments import _persist_base_state

from abaqus_sim_agent.coding.amendments import (
    CodingRunConfigPatch,
    append_operational_amendment,
)
from abaqus_sim_agent.coding.context_policy import CONTEXT_POLICY_VERSION
from abaqus_sim_agent.coding.contracts import CapabilityDefinitionBinding
from abaqus_sim_agent.coding.instructions import build_instructions
from abaqus_sim_agent.coding.launch import LaunchManifest
from abaqus_sim_agent.coding.model import (
    DeepSeekGateway,
    ModelExecutionConfig,
    new_run_execution_config,
)


def target_config(**changes):
    return new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO,
        model_name="deepseek-v4-flash",
        **changes,
    )


def launch(tmp_path):
    return LaunchManifest(
        schema_version="coding-run-launch-v7",
        question_protocol="ask_user_v1",
        response_enabled=True,
        capability_definitions=(
            CapabilityDefinitionBinding(
                capability_id="fixture",
                definition_sha256="a" * 64,
            ),
        ),
        context_policy_version=CONTEXT_POLICY_VERSION,
        skill_library=tmp_path,
        **target_config().model_dump(),
    )


def test_current_launch_roundtrip_and_question_protocol(tmp_path):
    assert target_config().model_settings() == {
        "timeout": 300.0,
        "extra_body": {"thinking": {"type": "enabled"}},
        "openai_reasoning_effort": "high",
    }
    current = launch(tmp_path)
    assert current.effective_execution() == target_config()
    assert LaunchManifest.model_validate_json(current.model_dump_json()) == current
    values = current.model_dump(mode="python")
    without_question_protocol = dict(values)
    without_question_protocol.pop("question_protocol")
    with pytest.raises(ValidationError, match="response-enabled launch requires ask_user"):
        LaunchManifest.model_validate(without_question_protocol)


def test_current_launch_rejects_missing_and_null_required_execution_field(tmp_path):
    values = launch(tmp_path).model_dump(mode="python")
    for value in (None, "missing"):
        candidate = dict(values)
        if value == "missing":
            candidate.pop("reasoning_policy")
        else:
            candidate["reasoning_policy"] = value
        with pytest.raises(ValidationError):
            LaunchManifest.model_validate(candidate)


@pytest.mark.parametrize(
    "settings",
    [
        {"extra_body": {"thinking": {"type": "disabled"}}},
        {"openai_reasoning_effort": "low"},
        {"extra_body": {"reasoning_effort": "low"}},
        {"thinking": {"type": "disabled"}},
        {"max_tokens": 2400},
    ],
)
def test_target_rejects_downstream_overrides(settings):
    with pytest.raises(ValueError, match=r"override|differs|cap"):
        target_config().checked_settings(settings)


def test_incompatible_amendment_is_rejected_before_state_mutation(tmp_path):
    request = _persist_base_state(tmp_path)
    (request.state_dir / "launch.json").write_text(
        launch(tmp_path).model_dump_json(), encoding="utf-8"
    )
    before = {
        p.name: p.read_bytes() for p in request.state_dir.iterdir() if p.is_file()
    }
    with pytest.raises(ValueError, match="enabled_high"):
        append_operational_amendment(
            request.state_dir,
            patch=CodingRunConfigPatch(model_name="deepseek-other"),
            reason="offline incompatible change",
        )
    after = {
        p.name: p.read_bytes()
        for p in request.state_dir.iterdir()
        if p.is_file() and p.name in before
    }
    assert after == before
    assert not (request.state_dir / "amendments.json").exists()


def test_config_strictness_and_supported_scope():
    values = target_config().model_dump()
    for change in (
        {"planning_enabled": "false"},
        {"http_retries": None},
        {"http_timeout_seconds": float("inf")},
        {"model_provider": DeepSeekGateway.OFFICIAL},
        {"unexpected": True},
    ):
        with pytest.raises(ValidationError):
            ModelExecutionConfig.model_validate(values | change)


def test_interaction_policy_changes_only_the_declared_clarification_boundary(tmp_path):
    request = _persist_base_state(tmp_path)
    legacy = build_instructions(request, api_catalog_enabled=False)
    current = build_instructions(
        request, api_catalog_enabled=False, interaction_policy="workspace_facts_first"
    )
    assert "Before loading a capability, planning, inspecting the workspace" in legacy
    assert (
        "Before loading a capability, planning, inspecting the workspace" not in current
    )
    assert "You may inspect user-provided workspace files" in current
    assert (
        "Do not invent a value or proceed with dependent modeling/execution" in current
    )
    assert "user instruction to ask first without taking other actions" in current
    assert "when explicit user\n  constraints conflict" in current
