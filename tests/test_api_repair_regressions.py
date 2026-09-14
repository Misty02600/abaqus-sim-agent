from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from abaqus_sim_agent.evaluation.api_repair import (
    _validate_case_fixtures,
    build_dataset,
    load_case_specs,
)
from abaqus_sim_agent.execution import Abaqus2024Runner

_PROJECT_ROOT = Path(__file__).parent.parent
_REGRESSION_MANIFEST = (
    _PROJECT_ROOT / "benchmarks" / "coding" / "api_repair_regression_cases.jsonl"
)
_FROZEN_MANIFEST = _PROJECT_ROOT / "benchmarks" / "coding" / "api_repair_cases.jsonl"
_PROVENANCE = (
    _PROJECT_ROOT
    / "benchmarks"
    / "coding"
    / "api_repair_regression_runtime_observations.json"
)
_AGENT_EVIDENCE = (
    _PROJECT_ROOT
    / "benchmarks"
    / "coding"
    / "api_repair_regression_evaluation_20260824.json"
)
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"


def _load_raw_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_api_repair_regressions_are_separate_from_frozen_benchmark() -> None:
    frozen_ids = {case["case_id"] for case in _load_raw_cases(_FROZEN_MANIFEST)}
    cases = load_case_specs(_REGRESSION_MANIFEST)

    assert [case.case_id for case in cases] == [
        "base_solid_box",
        "part_instance_set",
        "beam_section_invalid_orientation",
    ]
    assert not frozen_ids.intersection(case.case_id for case in cases)
    assert all(case.split == "dev" for case in cases)
    assert not any(case.runtime_note_available for case in cases)
    assert len(build_dataset(cases).cases) == 3
    _validate_case_fixtures(_PROJECT_ROOT, cases)


def test_regression_provenance_matches_checked_in_fixtures() -> None:
    cases = _load_raw_cases(_REGRESSION_MANIFEST)
    provenance = json.loads(_PROVENANCE.read_text(encoding="utf-8"))

    assert (
        provenance["manifest"]["sha256"]
        == hashlib.sha256(_REGRESSION_MANIFEST.read_bytes()).hexdigest()
    )
    observed = {item["case_id"]: item for item in provenance["cases"]}
    assert set(observed) == {case["case_id"] for case in cases}
    for case in cases:
        entry = _PROJECT_ROOT / case["fixture_dir"] / case["entry_script"]
        digest = hashlib.sha256(entry.read_bytes()).hexdigest()
        assert digest == case["fixture_sha256"]
        assert digest == observed[case["case_id"]]["fixture_sha256"]
        assert not (_PROJECT_ROOT / case["fixture_dir"] / case["result_file"]).exists()
    assert provenance["broken_fixture_validation"]["passed"] == 3
    assert provenance["repaired_postcondition_validation"]["passed"] == 3
    assert (
        provenance["repaired_postcondition_validation"]["solution_location"]
        == "outside_repository"
    )


def test_regression_agent_evidence_preserves_routing_and_limitations() -> None:
    evidence = json.loads(_AGENT_EVIDENCE.read_text(encoding="utf-8"))
    result = evidence["result"]
    exact_signature = evidence["automatic_exact_signature_result"]
    cases = {case["case_id"]: case for case in result["cases"]}

    assert result["passes"] == 2
    assert result["attempts"] == 2
    assert result["stale_hash_conflicts"] == 0
    assert cases["base_solid_box"]["automatic_lookup"] == {
        "query": "Part.BaseSolidBox",
        "status": "not_found",
        "counted": False,
        "alternatives": [
            "Part.BaseSolidExtrude",
            "Part.BaseSolidRevolve",
            "Part.BaseSolidSweep",
        ],
        "observed_adoption": (
            "The model explicitly selected ConstrainedSketch plus "
            "Part.BaseSolidExtrude after receiving the alternatives."
        ),
    }
    assert cases["part_instance_set"]["automatic_lookup"] is None
    assert evidence["deterministic_and_licensed_coverage"][
        "faces_error_rejects_edge_only_note_before_budget"
    ]
    assert evidence["interpretation"]["limits"][0].startswith(
        "Each Product-Agent case ran once"
    )
    assert exact_signature["case_id"] == "beam_section_invalid_orientation"
    assert exact_signature["passes"] == exact_signature["attempts"] == 1
    assert exact_signature["runtime_note_available"] is False
    assert exact_signature["api_lookup_calls"] == 1
    assert exact_signature["delivered_runtime_notes"] == 0
    assert exact_signature["automatic_lookup"] == {
        "query": "Model.BeamSection",
        "status": "found",
        "card_id": ("api:2024:abaqus.Section.SectionModel.SectionModel.BeamSection"),
        "payload_bytes": 1299,
        "delivered_note_ids": [],
        "counted": True,
    }
    assert exact_signature["verified_result"] == {
        "case_id": "beam_section_invalid_orientation",
        "section_name": "ProbeBeamSection",
        "material": "Steel",
        "profile": "RectProfile",
        "integration": "DURING_ANALYSIS",
    }
    assert evidence["deterministic_and_licensed_coverage"][
        "beam_section_signature_without_runtime_note_tested"
    ]


@pytest.mark.abaqus
@pytest.mark.skipif(
    not _LICENSED_TESTS_ENABLED,
    reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
)
@pytest.mark.parametrize(
    "case",
    _load_raw_cases(_REGRESSION_MANIFEST),
    ids=lambda case: case["case_id"],
)
def test_broken_api_repair_regression_fails_in_abaqus2024(
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
