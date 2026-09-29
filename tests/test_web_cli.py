from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from abaqus_sim_agent.web.cli import parse_args


@pytest.mark.parametrize("flag", ["--model", "--model-provider"])
def test_web_cli_has_no_default_model_options(flag):
    with pytest.raises(SystemExit):
        parse_args([flag, "unused"])


def test_server_configuration_cannot_supply_an_implicit_model(tmp_path):
    from abaqus_sim_agent.coding.options import RunOptions
    from abaqus_sim_agent.coding.providers import ModelProvider
    from abaqus_sim_agent.web.controller import ServerConfig

    with pytest.raises(ValueError, match="不设置默认模型"):
        ServerConfig(
            root=tmp_path,
            overrides=RunOptions(
                model_provider=ModelProvider.OPENAI, model_name="gpt-4.1"
            ),
        )


def test_web_cli_opens_plain_local_url_by_default_and_supports_opt_out(
    tmp_path: Path,
) -> None:
    default = parse_args(
        [
            "--root",
            str(tmp_path),
        ]
    )
    assert default.no_open is False
    assert default.port == 8765
    assert not hasattr(default, "api_catalog_dir")
    assert default.root == tmp_path
    assert parse_args([]).root is None

    disabled = parse_args(
        [
            "--root",
            str(tmp_path),
            "--no-open",
        ]
    )
    assert disabled.no_open is True

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--root",
                str(tmp_path),
                "--open",
            ]
        )
