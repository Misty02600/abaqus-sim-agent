from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from abaqus_sim_agent.api_catalog import ApiRuntimeNote
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult

_PROJECT_ROOT = Path(__file__).parent.parent
_FIXTURE = Path(__file__).parent / "fixtures" / "abaqus2024" / "container_probe.py"
_EVIDENCE = (
    _PROJECT_ROOT
    / "benchmarks"
    / "api_catalog"
    / "abaqus_2024_container_observations.json"
)
_RUNTIME_NOTES = (
    _PROJECT_ROOT / "benchmarks" / "api_catalog" / "abaqus_2024_runtime_notes.jsonl"
)
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"


def test_container_observation_provenance_and_runtime_notes() -> None:
    evidence = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    fixture_digest = hashlib.sha256(_FIXTURE.read_bytes()).hexdigest()

    assert evidence["runtime"]["release"] == "2024"
    assert evidence["reproduction"]["fixture"] == (
        "tests/fixtures/abaqus2024/container_probe.py"
    )
    assert evidence["reproduction"]["fixture_sha256"] == fixture_digest
    assert evidence["observations"]["Assembly.Set"]["native_sequence"] == {
        "status": "ok",
        "created_edge_count": 2,
    }
    assert evidence["observations"]["Part.setMeshControls"]["direct_face_array"] == {
        "status": "ok",
        "elem_shape": "QUAD",
        "technique": "STRUCTURED",
    }

    notes = {
        note.qualified_name: note
        for note in (
            ApiRuntimeNote.model_validate_json(line)
            for line in _RUNTIME_NOTES.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    for qualified_name in (
        "Assembly.Set",
        "Part.seedEdgeByNumber",
        "Part.setMeshControls",
    ):
        note = notes[qualified_name]
        assert note.verified
        assert note.evidence_ref == (
            "benchmarks/api_catalog/abaqus_2024_container_observations.json"
        )
        assert note.source.source_path == note.evidence_ref

    assert notes["Part.seedEdgeByNumber"].error_fingerprint is None
    assert "flat tuple or list" in notes["Assembly.Set"].guidance
    assert "regions=(faces,)" in notes["Part.setMeshControls"].guidance


@pytest.mark.abaqus
@pytest.mark.skipif(
    not _LICENSED_TESTS_ENABLED,
    reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
)
def test_abaqus2024_container_probe_matches_verified_observations(
    tmp_path: Path,
) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")

    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    shutil.copyfile(_FIXTURE, workspace / _FIXTURE.name)

    runner = Abaqus2024Runner(
        workspace.resolve(),
        state_dir.resolve(),
        launcher=_LAUNCHER.resolve(strict=True),
    )
    result = asyncio.run(runner.run_cae_script(_FIXTURE.name, timeout_seconds=300.0))
    output_path = workspace / "container_probe_results.json"
    if not result.succeeded or not output_path.is_file():
        pytest.fail(_execution_failure(result, output_path), pytrace=False)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    _assert_probe_observations(payload)


def _assert_probe_observations(payload: dict[str, Any]) -> None:
    assert payload["schema_version"] == "1"
    assert payload["completed"] is True
    cases = {case["name"]: case for case in payload["cases"]}

    native_set = cases["Assembly.Set.native_sequence"]
    assert native_set["status"] == "ok"
    assert native_set["observation"]["created_edge_count"] == 2
    assert native_set["observation"]["input"]["type_name"] == "Sequence"
    for name in (
        "Assembly.Set.flat_edge_tuple",
        "Assembly.Set.flat_edge_list",
    ):
        assert cases[name]["status"] == "error"
        assert cases[name]["error_type"] == "AbaqusException"

    accepted_seeds = {
        "Part.seedEdgeByNumber.native_sequence": 3,
        "Part.seedEdgeByNumber.flat_edge_tuple": 4,
        "Part.seedEdgeByNumber.flat_edge_list": 5,
    }
    for name, expected_number in accepted_seeds.items():
        case = cases[name]
        assert case["status"] == "ok"
        assert case["observation"]["requested_number"] == expected_number
        assert case["observation"]["bottom_number"] == expected_number
        assert case["observation"]["top_number"] == expected_number

    nested_seed = cases["Part.seedEdgeByNumber.tuple_native_sequence"]
    assert nested_seed["status"] == "error"
    assert nested_seed["error_type"] == "TypeError"

    direct_mesh = cases["Part.setMeshControls.direct_face_array"]
    assert direct_mesh["status"] == "ok"
    assert direct_mesh["observation"]["elem_shape"] == "QUAD"
    assert direct_mesh["observation"]["technique"] == "STRUCTURED"
    nested_mesh = cases["Part.setMeshControls.tuple_face_array"]
    assert nested_mesh["status"] == "error"
    assert nested_mesh["error_type"] == "TypeError"


def _execution_failure(result: AbaqusExecutionResult, output_path: Path) -> str:
    return (
        f"container probe failed despite exit_code={result.exit_code} and "
        f"timed_out={result.timed_out}; result_exists={output_path.is_file()}.\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
