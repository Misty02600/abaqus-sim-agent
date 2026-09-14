from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult
from abaqus_sim_agent.verification.assets import plate_hole_change_oracle_script
from abaqus_sim_agent.verification.plate_change_run import PlateHoleChangeRunVerifier

_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus2024"
_CHANGE_ROOT = _FIXTURE_ROOT / "plate_hole_change"


pytestmark = [
    pytest.mark.abaqus,
    pytest.mark.skipif(
        not _LICENSED_TESTS_ENABLED,
        reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
    ),
]


def test_reference_plate_hole_change_passes_new_and_regression_gates(
    tmp_path: Path,
) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    seed_texts: dict[str, str] = {}
    for name in ("build_model.py", "extract_results.py"):
        source = _FIXTURE_ROOT / name
        destination = workspace / name
        shutil.copyfile(source, destination)
        seed_texts[name] = source.read_text(encoding="utf-8")
    workspace = workspace.resolve()
    state_dir = state_dir.resolve()
    resolver = WorkspaceResolver(captured_cwd=workspace)
    initial_snapshot = resolver.snapshot(workspace)
    shutil.copyfile(_CHANGE_ROOT / "build_model.py", workspace / "build_model.py")

    launcher = _LAUNCHER.resolve(strict=True)
    runner = Abaqus2024Runner(workspace, state_dir, launcher=launcher)
    cae_result = asyncio.run(
        runner.run_cae_script("build_model.py", timeout_seconds=600)
    )
    _assert_execution(
        "CAE build", cae_result, workspace, ("plate_tension.cae", "plate_tension.inp")
    )
    solver_result = asyncio.run(
        runner.run_solver_job(
            "plate_tension",
            input_file="plate_tension.inp",
            timeout_seconds=1_800,
        )
    )
    _assert_execution("Solver", solver_result, workspace, ("plate_tension.odb",))
    extraction_result = asyncio.run(
        runner.run_abaqus_python(
            "extract_results.py",
            args=("plate_tension.odb", "results.json"),
            timeout_seconds=120,
        )
    )
    _assert_execution("ODB extraction", extraction_result, workspace, ("results.json",))

    request = CodingRunRequest(
        run_id="abaqus2024-plate-hole-change-integration",
        goal="Apply and verify the deterministic plate-hole change reference.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(launcher_path=launcher),
    )
    verifier = PlateHoleChangeRunVerifier(
        plate_hole_change_oracle_script(),
        source_run_id="verified-base-fixture",
        source_run_verified=True,
        seed_texts=seed_texts,
    )
    attempt = asyncio.run(
        verifier.verify(
            request,
            runner,
            initial_snapshot,
            (cae_result, solver_result, extraction_result),
        )
    )

    assert attempt.passed, attempt.model_dump_json(indent=2)
    assert attempt.verification is not None
    assert attempt.verification.new_requirement_passed
    assert attempt.verification.regression_passed
    assert attempt.verification.extractor_preserved
    assert attempt.measurements is not None
    assert attempt.measurements.boundary_loop_count == 2
    assert attempt.measurements.hole_boundary_edge_count >= 24


def _assert_execution(
    stage: str,
    result: AbaqusExecutionResult,
    workspace: Path,
    expected_paths: tuple[str, ...],
) -> None:
    missing = [path for path in expected_paths if not (workspace / path).is_file()]
    if not result.succeeded or missing:
        pytest.fail(
            f"{stage} failed; exit_code={result.exit_code}; missing={missing}\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}",
            pytrace=False,
        )
