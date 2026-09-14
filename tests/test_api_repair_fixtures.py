from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from abaqus_sim_agent.execution import Abaqus2024Runner

_PROJECT_ROOT = Path(__file__).parent.parent
_MANIFEST = _PROJECT_ROOT / "benchmarks" / "coding" / "api_repair_cases.jsonl"
_PROVENANCE = (
    _PROJECT_ROOT / "benchmarks" / "coding" / "api_repair_runtime_observations.json"
)
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"


def _load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_api_repair_fixture_manifest_is_frozen_and_balanced() -> None:
    cases = _load_cases()
    provenance = json.loads(_PROVENANCE.read_text(encoding="utf-8"))
    assert len(cases) == 6
    assert len({case["case_id"] for case in cases}) == 6
    assert [case["split"] for case in cases].count("dev") == 3
    assert [case["split"] for case in cases].count("holdout") == 3

    for case in cases:
        fixture_dir = _PROJECT_ROOT / case["fixture_dir"]
        entry_script = fixture_dir / case["entry_script"]
        assert fixture_dir.is_dir()
        assert case["agent_visible_files"] == [case["entry_script"]]
        assert sorted(path.name for path in fixture_dir.iterdir()) == [
            case["entry_script"]
        ]
        assert (
            hashlib.sha256(entry_script.read_bytes()).hexdigest()
            == case["fixture_sha256"]
        )
        assert not (fixture_dir / case["result_file"]).exists()
        assert case["baseline_must_fail"] is True
        assert case["baseline_must_not_create_result"] is True
        assert case["repaired_solution_in_fixture"] is False
        assert case["expected_error_terms"]
        assert case["skill_coverage"]["level"] in {"none", "partial", "direct"}

    assert all(case["runtime_note_available"] for case in cases[:3])
    assert not any(case["runtime_note_available"] for case in cases[3:])
    assert (
        provenance["manifest"]["sha256"]
        == hashlib.sha256(_MANIFEST.read_bytes()).hexdigest()
    )
    observed_cases = {case["case_id"]: case for case in provenance["cases"]}
    assert {case["case_id"]: case["fixture_sha256"] for case in cases} == {
        case_id: observation["fixture_sha256"]
        for case_id, observation in observed_cases.items()
    }
    assert provenance["broken_fixture_validation"]["passed"] == 6
    assert provenance["repaired_postcondition_validation"]["passed"] == 6
    assert (
        provenance["repaired_postcondition_validation"]["solution_location"]
        == "outside_repository"
    )


@pytest.mark.abaqus
@pytest.mark.skipif(
    not _LICENSED_TESTS_ENABLED,
    reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
)
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["case_id"])
def test_broken_api_repair_fixture_fails_in_abaqus2024(
    case: dict[str, Any],
    tmp_path: Path,
) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")

    fixture_dir = _PROJECT_ROOT / case["fixture_dir"]
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    shutil.copyfile(
        fixture_dir / case["entry_script"],
        workspace / case["entry_script"],
    )

    runner = Abaqus2024Runner(
        workspace.resolve(),
        state_dir.resolve(),
        launcher=_LAUNCHER.resolve(strict=True),
    )
    result = asyncio.run(
        runner.run_cae_script(case["entry_script"], timeout_seconds=300.0)
    )

    assert not result.timed_out
    assert not (workspace / case["result_file"]).exists()
    combined_output = (result.stdout + "\n" + result.stderr).casefold()
    missing_terms = [
        term
        for term in case["expected_error_terms"]
        if term.casefold() not in combined_output
    ]
    assert not missing_terms, (
        f"missing expected error terms {missing_terms}; "
        f"exit_code={result.exit_code}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
