from __future__ import annotations

import json
from pathlib import Path

import pytest

from abaqus_sim_agent.evaluation.api_repair import (
    DEFAULT_MANIFEST,
    _create_case_workspace,
    _load_api_catalog,
    _task_instructions,
    _validate_case_fixtures,
    build_dataset,
    load_case_specs,
)


def test_checked_in_api_repair_suite_has_frozen_dev_and_holdout_cases() -> None:
    cases = load_case_specs()

    assert len(cases) == 6
    assert {case.split for case in cases} == {"dev", "holdout"}
    assert sum(case.split == "dev" for case in cases) == 3
    assert sum(case.split == "holdout" for case in cases) == 3
    assert all(case.baseline_must_fail for case in cases)
    assert all(case.baseline_must_not_create_result for case in cases)
    assert not any(case.repaired_solution_in_fixture for case in cases)
    assert sum(case.runtime_note_available for case in cases) == 3
    _validate_case_fixtures(Path(__file__).parents[1], cases)

    dataset = build_dataset(cases)
    assert len(dataset.cases) == 6


def test_api_repair_workspace_seeds_only_agent_visible_files(tmp_path: Path) -> None:
    case = load_case_specs()[0]
    resolved = _create_case_workspace(
        tmp_path / "runs",
        "repair-case",
        Path(__file__).parents[1],
        case,
    )

    assert {path.name for path in resolved.workspace.iterdir()} == {
        Path(item).name for item in case.agent_visible_files
    }
    assert resolved.initial_snapshot.by_path().keys() == set(case.agent_visible_files)
    assert not (resolved.workspace / case.result_file).exists()


def test_task_instructions_replace_plate_contract() -> None:
    case = load_case_specs()[0]

    instructions = _task_instructions(case)

    assert case.entry_script in instructions
    assert case.result_file in instructions
    assert "run_cae_script" in instructions
    assert "do not return needs_input" in " ".join(instructions.split())
    assert "plate_thickness" not in instructions


def test_off_mode_rejects_catalog_and_catalog_modes_require_one(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="must not receive"):
        _load_api_catalog(tmp_path, "off", tmp_path / "catalog")
    for mode in ("full", "automatic_verified_only", "automatic_exact_signature"):
        with pytest.raises(ValueError, match="requires"):
            _load_api_catalog(tmp_path, mode, None)


def test_manifest_is_jsonl_with_matching_fixture_digests() -> None:
    raw_cases = [
        json.loads(line)
        for line in DEFAULT_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert [item["case_id"] for item in raw_cases] == [
        case.case_id for case in load_case_specs()
    ]
