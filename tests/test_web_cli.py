from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from abaqus_sim_agent.web.cli import parse_args


def test_web_cli_opens_plain_local_url_by_default_and_supports_opt_out(
    tmp_path: Path,
) -> None:
    default = parse_args(["--root", str(tmp_path)])
    assert default.no_open is False
    assert default.port == 8765

    disabled = parse_args(["--root", str(tmp_path), "--no-open"])
    assert disabled.no_open is True

    with pytest.raises(SystemExit):
        parse_args(["--root", str(tmp_path), "--open"])
