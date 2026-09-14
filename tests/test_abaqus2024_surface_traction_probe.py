from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog import ApiRuntimeNote
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult

_PROJECT_ROOT = Path(__file__).parent.parent
_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "abaqus2024"
    / "surface_traction_region_probe.py"
)
_EVIDENCE = (
    _PROJECT_ROOT
    / "benchmarks"
    / "api_catalog"
    / "abaqus_surface_traction_region_runtime_observation.json"
)
_RUNTIME_NOTES = (
    _PROJECT_ROOT / "benchmarks" / "api_catalog" / "abaqus_2024_runtime_notes.jsonl"
)
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"


def test_surface_traction_region_observation_and_note_provenance() -> None:
    evidence = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    fixture_digest = hashlib.sha256(_FIXTURE.read_bytes()).hexdigest()

    assert evidence["runtime"]["release"] == "2024"
    assert evidence["reproduction"]["fixture"] == (
        "tests/fixtures/abaqus2024/surface_traction_region_probe.py"
    )
    assert evidence["reproduction"]["fixture_sha256"] == fixture_digest
    assert evidence["observations"]["assembly_set_region"]["status"] == "error"
    assert evidence["observations"]["assembly_surface_region"] == {
        "status": "ok",
        "load_created": True,
        "surface_created": True,
        "surface_side": "SIDE1",
    }

    note = next(
        note
        for note in (
            ApiRuntimeNote.model_validate_json(line)
            for line in _RUNTIME_NOTES.read_text(encoding="utf-8").splitlines()
            if line
        )
        if note.qualified_name == "Model.SurfaceTraction"
    )
    assert note.verified
    assert note.applies_to_parameters == ("region",)
    assert note.evidence_ref == (
        "benchmarks/api_catalog/abaqus_surface_traction_region_runtime_observation.json"
    )
    assert note.source.source_path == note.evidence_ref
    assert "Assembly.Surface" in note.guidance
    assert "Assembly.Set" in note.guidance


@pytest.mark.abaqus
@pytest.mark.skipif(
    not _LICENSED_TESTS_ENABLED,
    reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
)
def test_abaqus2024_surface_traction_region_probe(tmp_path: Path) -> None:
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
    output_path = workspace / "surface_traction_region_probe_results.json"
    if not result.succeeded or not output_path.is_file():
        pytest.fail(_execution_failure(result, output_path), pytrace=False)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["completed"] is True
    assert payload["cases"]["assembly_set_region"]["status"] == "error"
    assert payload["cases"]["assembly_surface_region"] == {
        "status": "ok",
        "load_created": True,
        "surface_created": True,
        "surface_side": "SIDE1",
    }


def _execution_failure(result: AbaqusExecutionResult, output_path: Path) -> str:
    return (
        f"surface-traction region probe failed with exit_code={result.exit_code}; "
        f"timed_out={result.timed_out}; result_exists={output_path.is_file()}.\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
