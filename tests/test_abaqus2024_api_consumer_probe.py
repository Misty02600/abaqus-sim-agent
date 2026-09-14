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
_FIXTURE = Path(__file__).parent / "fixtures" / "abaqus2024" / "api_consumer_probe.py"

pytestmark = [
    pytest.mark.abaqus,
    pytest.mark.skipif(
        not _LICENSED_TESTS_ENABLED,
        reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
    ),
]


def test_abaqus2024_api_consumer_probe_completes(tmp_path: Path) -> None:
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

    result = asyncio.run(runner.run_cae_script(_FIXTURE.name, timeout_seconds=600.0))
    output_path = workspace / "api_consumer_probe_results.json"
    if not result.succeeded or not output_path.is_file():
        pytest.fail(_execution_failure(result, output_path), pytrace=False)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["completed"] is True
    assert len(payload["cases"]) == 24
    cases = {case["name"]: case for case in payload["cases"]}

    expected_statuses = {
        "Part.setElementType.direct_faces": "error",
        "Part.setElementType.tuple_faces": "ok",
        "Part.setElementType.nested_tuple_faces": "ok",
        "Part.setElementType.direct_set": "ok",
        "Part.setElementType.tuple_set": "error",
        "Part.assignBeamSectionOrientation.direct_edges": "ok",
        "Part.assignBeamSectionOrientation.tuple_edges": "ok",
        "Part.assignBeamSectionOrientation.direct_set": "ok",
        "Part.assignBeamSectionOrientation.single_edge": "ok",
        "Assembly.Surface.side1Edges_native": "ok",
        "Assembly.Surface.side1Edges_flat_tuple": "error",
        "Assembly.Surface.side1Edges_flat_list": "error",
        "Assembly.Surface.side1Edges_tuple_native": "ok",
        "Assembly.Surface.side2Edges_native": "ok",
        "Assembly.Surface.side2Edges_flat_tuple": "error",
        "Assembly.Surface.side2Edges_flat_list": "error",
        "Assembly.Surface.side2Edges_tuple_native": "ok",
        "Model.BeamSection.valid": "ok",
        "Model.BeamSection.orientation_keyword": "error",
        "Model.BeamSection.geometric_type_keyword": "error",
        "Model.FieldOutputRequest.variables_U_RF_SF": "ok",
        "Model.FieldOutputRequest.variables_U_RF_SF_RM": "ok",
        "Model.FieldOutputRequest.variables_U_RF_SF_SM": "error",
        "Model.FieldOutputRequest.variables_U_RF_SF_RM_SM": "error",
    }
    assert {name: cases[name]["status"] for name in expected_statuses} == (
        expected_statuses
    )
    assert cases["Part.setElementType.direct_faces"]["error_type"] == "TypeError"
    assert cases["Part.setElementType.tuple_set"]["error_type"] == "TypeError"
    for side in ("side1Edges", "side2Edges"):
        assert cases[f"Assembly.Surface.{side}_native"]["observation"][
            "repository_contains_surface"
        ]
        assert cases[f"Assembly.Surface.{side}_tuple_native"]["observation"][
            "repository_contains_surface"
        ]
    for name in (
        "Assembly.Surface.side1Edges_flat_tuple",
        "Assembly.Surface.side1Edges_flat_list",
        "Assembly.Surface.side2Edges_flat_tuple",
        "Assembly.Surface.side2Edges_flat_list",
        "Model.FieldOutputRequest.variables_U_RF_SF_SM",
        "Model.FieldOutputRequest.variables_U_RF_SF_RM_SM",
    ):
        assert cases[name]["error_type"] == "AbaqusException"
    for name in (
        "Model.BeamSection.orientation_keyword",
        "Model.BeamSection.geometric_type_keyword",
    ):
        assert cases[name]["error_type"] == "TypeError"


def _execution_failure(
    result: AbaqusExecutionResult,
    output_path: Path,
) -> str:
    return (
        f"API consumer probe failed despite exit_code={result.exit_code} and "
        f"timed_out={result.timed_out}; result_exists={output_path.is_file()}.\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
