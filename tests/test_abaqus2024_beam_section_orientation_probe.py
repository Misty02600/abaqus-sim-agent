from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult

_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "abaqus2024"
    / "beam_section_orientation_probe.py"
)

pytestmark = [
    pytest.mark.abaqus,
    pytest.mark.skipif(
        not _LICENSED_TESTS_ENABLED,
        reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
    ),
]


def test_abaqus2024_beam_section_orientation_probe_completes(
    tmp_path: Path,
) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")

    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    shutil.copyfile(_FIXTURE, workspace / _FIXTURE.name)
    runner = Abaqus2024Runner(
        workspace,
        state_dir,
        launcher=_LAUNCHER.resolve(strict=True),
    )

    result = asyncio.run(runner.run_cae_script(_FIXTURE.name, timeout_seconds=300.0))
    output_path = workspace / "beam_section_orientation_probe_results.json"
    if not result.succeeded or not output_path.is_file():
        pytest.fail(_execution_failure(result, output_path), pytrace=False)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["completed"] is True
    assert len(payload["cases"]) == 4
    cases = {case["name"]: case for case in payload["cases"]}
    assert cases["Model.BeamSection.valid"]["status"] == "ok"
    assert cases["Model.BeamSection.valid"]["observation"]["section_names"] == [
        "Section"
    ]
    for parameter in ("orientation", "beamOrientation", "geometricType"):
        name = {
            "orientation": "orientation_keyword",
            "beamOrientation": "beam_orientation_keyword",
            "geometricType": "geometric_type_keyword",
        }[parameter]
        case = cases[f"Model.BeamSection.{name}"]
        assert case["status"] == "error"
        assert case["error_type"] == "TypeError"
        assert parameter in case["error"]


def _execution_failure(
    result: AbaqusExecutionResult,
    output_path: Path,
) -> str:
    return (
        f"BeamSection orientation probe failed despite exit_code={result.exit_code} "
        f"and timed_out={result.timed_out}; "
        f"result_exists={output_path.is_file()}.\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
