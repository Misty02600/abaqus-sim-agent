import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.coding.cli import (
    _validate_resume_arguments,
    build_parser,
    run_cli,
)
from abaqus_sim_agent.coding.model import (
    DeepSeekGateway,
    take_gateway_api_keys_from_environment,
)
from abaqus_sim_agent.coding.option_arguments import options_from_args
from abaqus_sim_agent.coding.options import (
    RunOptions,
    merge_run_options,
    model_execution,
    resolve_run_config,
)


def test_explicit_precedence_preserves_zero_and_empty_selection():
    result = merge_run_options(
        RunOptions(
            model_provider=DeepSeekGateway.OFFICIAL,
            model_name="deepseek-v4-flash",
            http_retries=4,
            knowledge_pack_ids=("sample",),
        ),
        RunOptions(http_retries=0, knowledge_pack_ids=()),
    )
    assert result.http_retries == 0
    assert result.knowledge_pack_ids == ()
    assert model_execution(result).model_provider is DeepSeekGateway.OFFICIAL


@pytest.mark.parametrize(
    "values",
    [
        {"model_name": None},
        {"http_retries": "4"},
        {"api_key": "not-an-option"},
        {"context_window": 128_000},
        {"model_base_url": "https://custom.invalid/v1"},
        {"api_catalog_dir": "D:/catalog"},
        {"launcher_path": "D:/vendor.bat"},
        {"skill_library": "D:/skills"},
        {"abqpy_static_wheel": "D:/wheel.whl"},
        {"static_preflight_cache_dir": "D:/cache"},
    ],
)
def test_options_reject_null_coercion_and_internal_resource_paths(values):
    with pytest.raises(ValidationError):
        RunOptions.model_validate(values)


def test_cli_ignores_old_preference_files_and_does_not_modify_them(
    tmp_path, monkeypatch, capsys
):
    workspace = tmp_path / "workspace"
    old_project = workspace / ".abaqus-sim-agent/settings.json"
    old_project.parent.mkdir(parents=True)
    old_project.write_text("invalid JSON", encoding="utf-8")
    appdata = tmp_path / "appdata"
    old_user = appdata / "abaqus-sim-agent/settings.json"
    old_user.parent.mkdir(parents=True)
    old_user.write_text(json.dumps({"model_provider": "opencode-go"}), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    before = {p: p.read_bytes() for p in (old_user, old_project)}
    args = [
        "--goal",
        "test",
        "--workspace",
        str(workspace),
        "--state-dir",
        str(tmp_path / "state"),
    ]
    assert run_cli(args) == 1
    assert "请先选择模型" in capsys.readouterr().out
    assert run_cli([*args, "--model-provider", "opencode-go"]) == 1
    assert "请先选择模型" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in before} == before


def test_cli_and_web_share_run_arguments(tmp_path, monkeypatch):
    from abaqus_sim_agent.web.cli import parse_args

    monkeypatch.setenv("OPENCODE_API_KEY", "fixture")

    args = [
        "--http-retries",
        "0",
        "--tools",
        "read_file,lookup_abaqus_api",
        "--knowledge-pack",
        "custom-fea",
    ]
    cli = options_from_args(
        build_parser().parse_args(
            [
                "--goal",
                "test",
                "--model-provider",
                "opencode-go",
                "--model",
                "deepseek-v4-flash",
                *args,
            ]
        )
    )
    web = merge_run_options(
        options_from_args(parse_args(args)),
        RunOptions(
            model_provider=DeepSeekGateway.OPENCODE_GO, model_name="deepseek-v4-flash"
        ),
    )
    assert cli == web
    settings = resolve_run_config(tmp_path, cli)
    assert settings == resolve_run_config(tmp_path, web)
    assert settings.runtime.launcher_path == Path("abq2024")
    assert settings.abqpy_static_wheel is None


@pytest.mark.parametrize(
    "flag",
    [
        "--launcher",
        "--context-window",
        "--model-base-url",
        "--api-catalog-dir",
        "--api-catalog-mode",
        "--skills-dir",
        "--abqpy-static-wheel",
        "--static-preflight-cache-dir",
        "--document-policy-mode",
    ],
)
def test_removed_cli_flags_are_not_silently_accepted(flag):
    from abaqus_sim_agent.web.cli import parse_args

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--goal", "test", flag, "obsolete"])
    with pytest.raises(SystemExit):
        parse_args([flag, "obsolete"])


@pytest.mark.parametrize("mode", ["off", "on"])
def test_resume_rejects_explicit_static_mode(mode):
    args = build_parser().parse_args(
        ["--resume", "unused-state", "--static-preflight-mode", mode]
    )
    with pytest.raises(ValueError, match="--static-preflight-mode"):
        _validate_resume_arguments(args)


def test_gateway_credentials_are_captured_then_removed():
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
