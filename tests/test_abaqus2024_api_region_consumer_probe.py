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
    Path(__file__).parent / "fixtures" / "abaqus2024" / "api_region_consumer_probe.py"
)

pytestmark = [
    pytest.mark.abaqus,
    pytest.mark.skipif(
        not _LICENSED_TESTS_ENABLED,
        reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
    ),
]


def test_abaqus2024_api_region_consumer_probe_completes(tmp_path: Path) -> None:
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
    result = asyncio.run(runner.run_cae_script(_FIXTURE.name, timeout_seconds=900.0))
    output_path = workspace / "api_region_consumer_probe_results.json"
    if not result.succeeded or not output_path.is_file():
        pytest.fail(_execution_failure(result, output_path), pytrace=False)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["completed"] is True
    assert len(payload["cases"]) == 28
    cases = {case["name"]: case for case in payload["cases"]}
    expected_errors = {
        "Part.setElementType.solid_direct_array": "TypeError",
        "Part.setElementType.solid_tuple_set": "TypeError",
        "Part.setElementType.beam_direct_array": "TypeError",
        "Part.setElementType.beam_tuple_set": "TypeError",
        "Part.SectionAssignment.tuple_set": "AbaqusException",
        "Part.SectionAssignment.raw_faces": "TypeError",
        "Model.DisplacementBC.region_raw_vertices": "TypeError",
        "Model.ConcentratedForce.region_raw_vertices": "TypeError",
        "Assembly.Surface.side1Faces_flat_tuple": "AbaqusException",
        "Assembly.Surface.side1Faces_flat_list": "AbaqusException",
        "Assembly.Surface.side2Faces_flat_tuple": "AbaqusException",
        "Assembly.Surface.side2Faces_flat_list": "AbaqusException",
    }
    assert {
        name: cases[name]["error_type"] for name in expected_errors
    } == expected_errors
    assert all(
        case["status"] == "ok"
        for name, case in cases.items()
        if name not in expected_errors
    )
    assert cases["Model.DisplacementBC.region_assembly_set"]["observation"][
        "bc_names"
    ] == ["ProbeBC"]
    assert cases["Model.ConcentratedForce.region_assembly_set"]["observation"][
        "load_names"
    ] == ["ProbeLoad"]
    for side in ("side1Faces", "side2Faces"):
        for selected_kind in ("native", "tuple_native"):
            name = f"Assembly.Surface.{side}_{selected_kind}"
            assert cases[name]["observation"]["repository_contains_surface"]


def _execution_failure(
    result: AbaqusExecutionResult,
    output_path: Path,
) -> str:
    return (
        f"API region consumer probe failed despite exit_code={result.exit_code} and "
        f"timed_out={result.timed_out}; result_exists={output_path.is_file()}.\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
