import json

import pytest
from pydantic import ValidationError
from tests.test_coding_amendments import _persist_base_state

from abaqus_sim_agent.coding.amendments import (
    CodingRunConfigPatch,
    append_operational_amendment,
)
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


def launch(tmp_path, version="coding-run-launch-v3"):
    values = {
        "schema_version": version,
        "model_provider": DeepSeekGateway.OPENCODE_GO,
        "model_name": "deepseek-v4-flash",
        "http_retries": 10,
        "skill_library": tmp_path,
    }
    if version.endswith("v3"):
        values.update(
            target_config().model_dump(
                exclude={"model_provider", "model_name", "http_retries"}
            )
        )
    return LaunchManifest(**values)


def test_new_target_and_legacy_compatibility(tmp_path):
    assert target_config().model_settings() == {
        "timeout": 60.0,
        "extra_body": {"thinking": {"type": "enabled"}},
        "openai_reasoning_effort": "high",
    }
    assert launch(tmp_path).effective_execution() == target_config()
    for version in ("coding-run-launch-v1", "coding-run-launch-v2"):
        original = launch(tmp_path, version).model_dump_json()
        decoded = LaunchManifest.model_validate_json(original)
        assert decoded.model_dump_json() == original
        assert decoded.effective_execution().model_settings() == {"timeout": 60.0}
        assert decoded.effective_execution().interaction_policy == "legacy"
        assert not decoded.effective_execution(pure_edit=True).planning_enabled
        assert "reasoning_policy" not in json.loads(original)
        with pytest.raises(ValidationError, match="legacy launch"):
            LaunchManifest.model_validate_json(
                original[:-1] + ',"reasoning_policy":null}'
            )


@pytest.mark.parametrize(
    "field",
    [
        "reasoning_policy",
        "http_timeout_seconds",
        "planning_enabled",
        "interaction_policy",
    ],
)
def test_v3_rejects_missing_and_null_fields(tmp_path, field):
    values = launch(tmp_path).model_dump(mode="json")
    for value in (None, "missing"):
        candidate = dict(values)
        if value == "missing":
            candidate.pop(field)
        else:
            candidate[field] = value
        with pytest.raises(ValidationError, match="complete effective"):
            LaunchManifest.model_validate_json(json.dumps(candidate))


@pytest.mark.parametrize(
    "settings",
    [
        {"extra_body": {"thinking": {"type": "disabled"}}},
        {"openai_reasoning_effort": "low"},
        {"extra_body": {"reasoning_effort": "low"}},
        {"thinking": {"type": "disabled"}},
        {"max_tokens": 2400},
        {"extra_body": {"max_completion_tokens": 2400}},
        {"timeout": 120.0},
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
