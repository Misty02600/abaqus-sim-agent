import pytest
from pydantic import ValidationError

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
from abaqus_sim_agent.coding.tool_selection import resolve_tool_names
from tests.test_coding_amendments import _persist_base_state


def target_config(**changes):
    return new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO,
        model_name="deepseek-v4-flash",
        **changes,
    )


def launch(tmp_path):
    return LaunchManifest(
        schema_version="coding-run-launch-v11",
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
        api_catalog_dir=(tmp_path / "catalog").resolve(),
        tools=resolve_tool_names(),
        **target_config().model_dump(),
    )


def test_launch_binds_workspace_text_policy(tmp_path) -> None:
    manifest = launch(tmp_path)
    assert manifest.schema_version == "coding-run-launch-v11"
    assert manifest.filesystem_policy_version == "utf8-cp936-preserve-v1"


def test_current_launch_roundtrip_and_question_protocol(tmp_path):
    assert target_config().planning_enabled is False
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
    with pytest.raises(
        ValidationError, match="response-enabled launch requires ask_user"
    ):
        LaunchManifest.model_validate(without_question_protocol)


def test_product_launch_v11_requires_catalog_and_saved_tools(tmp_path) -> None:
    values = launch(tmp_path).model_dump(mode="python")

    current = LaunchManifest.model_validate(values)
    assert "lookup_abaqus_api" in current.tools
    with pytest.raises(ValidationError, match="requires an API catalog"):
        LaunchManifest.model_validate(values | {"api_catalog_dir": None})
    with pytest.raises(ValidationError, match="requires saved tools"):
        LaunchManifest.model_validate(values | {"tools": None})
    with pytest.raises(ValidationError, match="uses tools"):
        LaunchManifest.model_validate(values | {"api_catalog_mode": "full"})


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


def test_foundation_profile_adds_decision_owned_skill_routing_rule(tmp_path):
    request = _persist_base_state(tmp_path)
    default = build_instructions(request, api_catalog_enabled=False)
    foundation = build_instructions(
        request,
        api_catalog_enabled=False,
        foundation_skill_profile="linear_structural_fea_v1",
    )

    marker = "When you must make a substantive engineering choice"
    accepted_model_marker = (
        "An accepted existing formulation does not by itself resolve a modeling edit."
    )
    assert marker not in default
    assert accepted_model_marker not in default
    assert marker in foundation
    assert accepted_model_marker in foundation
    assert "meaning-level\n  changes to an accepted existing model" in foundation
    assert "linear-structural-fea-discretization" in foundation
    assert "purely local coding/API repair" in foundation


@pytest.mark.parametrize(
    "version",
    [None, "coding-run-launch-v7", "coding-run-launch-v8", "coding-run-launch-v10"],
)
def test_launch_rejects_missing_or_old_execution_contract(tmp_path, version):
    data = launch(tmp_path).model_dump(mode="json")
    if version is None:
        data.pop("schema_version")
    else:
        data["schema_version"] = version
    with pytest.raises(ValidationError, match="schema_version"):
        LaunchManifest.model_validate(data)


@pytest.mark.parametrize(
    "version",
    [None, "coding-run-resume-v3", "coding-run-resume-v4", "coding-run-resume-v5"],
)
def test_resume_rejects_missing_or_old_execution_contract(tmp_path, version):
    import json

    from abaqus_sim_agent.coding.resume import CodingRunResumeState

    request = _persist_base_state(tmp_path)
    data = json.loads(
        (request.state_dir / "resume_state.json").read_text(encoding="utf-8")
    )
    if version is None:
        data.pop("schema_version")
    else:
        data["schema_version"] = version
    with pytest.raises(ValidationError, match="schema_version"):
        CodingRunResumeState.model_validate(data)
