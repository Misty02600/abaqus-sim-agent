import json

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.coding import preferences
from abaqus_sim_agent.coding.cli import (
    _validate_resume_arguments,
    build_parser,
    run_cli,
)
from abaqus_sim_agent.coding.model import (
    DeepSeekGateway,
    take_gateway_api_keys_from_environment,
)
from abaqus_sim_agent.coding.preference_arguments import preferences_from_args
from abaqus_sim_agent.coding.preferences import (
    PROJECT_SETTINGS_PATH,
    RunPreferences,
    read_preferences,
    resolve_model_config,
    resolve_run_config,
)
from tests.test_coding_cli import _catalog_dir


def write_preferences(path, **values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")


def test_precedence_is_per_field_and_does_not_write_sources(tmp_path):
    user = preferences.user_settings_path()
    project = tmp_path / "workspace" / PROJECT_SETTINGS_PATH
    write_preferences(
        user, model_provider="opencode-go", http_retries=2, http_timeout_seconds=100
    )
    write_preferences(project, http_retries=6, model_name="deepseek-v4-flash")
    originals = {p: p.read_bytes() for p in (user, project)}

    inherited = resolve_model_config(project.parent.parent)
    assert inherited.model_provider is DeepSeekGateway.OPENCODE_GO
    assert inherited.http_retries == 6
    assert inherited.http_timeout_seconds == 100
    assert inherited.reasoning_policy == "enabled_high"

    explicit = resolve_model_config(
        project.parent.parent,
        RunPreferences(http_retries=3),
        RunPreferences(http_retries=0, reasoning_policy="provider_default"),
    )
    assert explicit.http_retries == 0
    assert explicit.reasoning_policy == "provider_default"
    assert {p: p.read_bytes() for p in originals} == originals


@pytest.mark.parametrize(
    "content",
    [
        "",
        "[1]",
        "{",
        '{"model_name": null}',
        '{"http_retries": "4"}',
        '{"api_key": "private-sentinel"}',
        '{"unknown": true}',
    ],
)
def test_invalid_preferences_fail_without_rewriting_or_echoing_values(
    tmp_path, content
):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid preferences") as error:
        read_preferences(path)
    assert str(path) in str(error.value)
    assert "private-sentinel" not in str(error.value)
    assert path.read_text(encoding="utf-8") == content


def test_missing_preferences_use_builtins_without_creating_files(tmp_path):
    resolved = resolve_model_config(tmp_path)
    assert resolved.model_provider is DeepSeekGateway.OFFICIAL
    assert resolved.model_name == "deepseek-v4-flash"
    assert resolved.http_retries == 4
    assert not preferences.user_settings_path().exists()
    assert not (tmp_path / PROJECT_SETTINGS_PATH).exists()


def test_final_combination_is_validated_after_overlay(tmp_path):
    write_preferences(
        preferences.user_settings_path(),
        model_provider="opencode-go",
        reasoning_policy="enabled_high",
    )
    with pytest.raises(ValidationError, match="enabled_high"):
        resolve_model_config(
            tmp_path, RunPreferences(model_provider=DeepSeekGateway.OFFICIAL)
        )
    config = resolve_model_config(
        tmp_path,
        RunPreferences(
            model_provider=DeepSeekGateway.OFFICIAL, reasoning_policy="provider_default"
        ),
    )
    assert config.reasoning_policy == "provider_default"


def test_cli_uses_selected_workspace_preferences_and_explicit_override(
    tmp_path, monkeypatch, capsys
):
    workspace = tmp_path / "workspace"
    other = tmp_path / "startup"
    other.mkdir()
    project = workspace / PROJECT_SETTINGS_PATH
    write_preferences(project, model_provider="opencode-go")
    write_preferences(other / PROJECT_SETTINGS_PATH, model_provider="deepseek")
    monkeypatch.chdir(other)
    args = [
        "--goal",
        "test",
        "--workspace",
        str(workspace),
        "--state-dir",
        str(tmp_path / "state"),
        "--api-catalog-dir",
        str(_catalog_dir(tmp_path)),
    ]
    assert run_cli(args) == 1
    assert "OPENCODE_API_KEY must not be empty" in capsys.readouterr().out
    assert run_cli([*args, "--model-provider", "deepseek"]) == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().out
    assert json.loads(project.read_text()) == {"model_provider": "opencode-go"}


@pytest.mark.parametrize("mode", ["off", "on"])
def test_resume_rejects_explicit_static_mode_instead_of_ignoring_it(mode):
    args = build_parser().parse_args(
        ["--resume", "unused-state", "--static-preflight-mode", mode]
    )
    with pytest.raises(ValueError, match="--static-preflight-mode"):
        _validate_resume_arguments(args)
    _validate_resume_arguments(build_parser().parse_args(["--resume", "unused-state"]))


def test_gateway_credentials_are_captured_separately_and_removed_from_environment():
    env = {
        "DEEPSEEK_API_KEY": "official",
        "OPENCODE_API_KEY": "opencode",
        "KEEP": "kept",
    }
    keys = take_gateway_api_keys_from_environment(env)
    assert keys[DeepSeekGateway.OFFICIAL].get_secret_value() == "official"
    assert keys[DeepSeekGateway.OPENCODE_GO].get_secret_value() == "opencode"
    assert keys[DeepSeekGateway.OPENCODE_ZEN].get_secret_value() == "opencode"
    assert env == {"KEEP": "kept"}


def test_cli_and_web_share_preference_options_and_resolution(tmp_path):
    from abaqus_sim_agent.web.cli import parse_args

    catalog = _catalog_dir(tmp_path)
    args = [
        "--model-provider",
        "opencode-go",
        "--http-retries",
        "0",
        "--api-catalog-dir",
        str(catalog),
        "--api-catalog-mode",
        "full",
        "--launcher",
        str(tmp_path / "vendor.bat"),
        "--knowledge-pack",
        "custom-fea",
    ]
    cli = preferences_from_args(build_parser().parse_args(["--goal", "test", *args]))
    web = preferences_from_args(parse_args(["--root", str(tmp_path / "host"), *args]))
    assert cli == web
    assert resolve_run_config(tmp_path, cli) == resolve_run_config(tmp_path, web)


def test_paths_are_relative_to_their_source_and_empty_list_clears_defaults(tmp_path):
    user = preferences.user_settings_path()
    workspace = tmp_path / "workspace"
    project = workspace / PROJECT_SETTINGS_PATH
    write_preferences(
        user,
        api_catalog_dir="catalog",
        launcher_path="vendor/abq2024.bat",
        knowledge_pack_ids=["default-fea"],
    )
    write_preferences(project, skill_library="../skills", knowledge_pack_ids=[])
    settings = resolve_run_config(workspace)
    assert settings.api_catalog_dir == user.parent / "catalog"
    assert settings.runtime.launcher_path == user.parent / "vendor/abq2024.bat"
    assert settings.skill_library == workspace / "skills"
    assert settings.knowledge_pack_ids == ()
    args = build_parser().parse_args(["--goal", "test", "--no-knowledge-packs"])
    assert preferences_from_args(args).knowledge_pack_ids == ()


def test_disabled_static_feedback_does_not_bind_dormant_source_defaults(tmp_path):
    write_preferences(
        preferences.user_settings_path(),
        api_catalog_dir="catalog",
        static_preflight_mode="on",
        abqpy_static_wheel="source.whl",
        static_preflight_cache_dir="cache",
    )
    enabled = resolve_run_config(tmp_path)
    assert enabled.static_preflight_mode == "on"
    assert enabled.abqpy_static_wheel == tmp_path / "source.whl"
    disabled = resolve_run_config(tmp_path, RunPreferences(static_preflight_mode="off"))
    assert disabled.abqpy_static_wheel is None
    assert disabled.static_preflight_cache_dir is None
