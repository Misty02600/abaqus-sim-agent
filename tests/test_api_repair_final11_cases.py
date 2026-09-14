from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.evaluation.api_repair import ApiRepairCaseSpec, load_case_specs
from abaqus_sim_agent.execution import Abaqus2024Runner

_ROOT = Path(__file__).parent.parent
_MANIFEST = _ROOT / "benchmarks" / "coding" / "api_repair_final11_new_cards.jsonl"
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"


def test_final11_api_repair_manifest_matches_broken_fixtures() -> None:
    cases = load_case_specs(_MANIFEST)

    assert [case.case_id for case in cases] == [
        "set_element_type_direct_faces",
        "assembly_surface_flat_edges",
    ]
    assert all(case.runtime_note_available for case in cases)
    for case in cases:
        fixture = _ROOT / case.fixture_dir / case.entry_script
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == case.fixture_sha256
        assert not (_ROOT / case.fixture_dir / case.result_file).exists()


@pytest.mark.abaqus
@pytest.mark.skipif(
    not _LICENSED_TESTS_ENABLED,
    reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
)
@pytest.mark.parametrize(
    "case",
    load_case_specs(_MANIFEST),
    ids=lambda case: case.case_id,
)
def test_final11_broken_api_repair_case_fails_in_abaqus2024(
    case: ApiRepairCaseSpec,
    tmp_path: Path,
) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")

    fixture_dir = _ROOT / case.fixture_dir
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    shutil.copyfile(
        fixture_dir / case.entry_script,
        workspace / case.entry_script,
    )
    runner = Abaqus2024Runner(
        workspace.resolve(),
        state_dir.resolve(),
        launcher=_LAUNCHER.resolve(strict=True),
    )

    result = asyncio.run(
        runner.run_cae_script(case.entry_script, timeout_seconds=300.0)
    )

    assert not (workspace / case.result_file).exists()
    combined = (result.stdout + "\n" + result.stderr).casefold()
    assert "abaqus error:" in combined
    assert all(term.casefold() in combined for term in case.expected_error_terms)
