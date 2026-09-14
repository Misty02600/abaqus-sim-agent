from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult
from abaqus_sim_agent.verification.assets import plate_tension_oracle_script
from abaqus_sim_agent.verification.run import PlateTensionRunVerifier

_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus2024"


pytestmark = [
    pytest.mark.abaqus,
    pytest.mark.skipif(
        not _LICENSED_TESTS_ENABLED,
        reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
    ),
]


def test_abaqus2024_reference_plate_closes_the_physical_loop(
    tmp_path: Path,
) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")

    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    workspace = workspace.resolve()
    state_dir = state_dir.resolve()

    resolver = WorkspaceResolver(captured_cwd=workspace)
    initial_snapshot = resolver.snapshot(workspace)
    for fixture_name in ("build_model.py", "extract_results.py"):
        shutil.copyfile(_FIXTURE_ROOT / fixture_name, workspace / fixture_name)

    launcher = _LAUNCHER.resolve(strict=True)
    runner = Abaqus2024Runner(workspace, state_dir, launcher=launcher)

    cae_result = asyncio.run(
        runner.run_cae_script(
            "build_model.py",
            timeout_seconds=600.0,
        )
    )
    _assert_execution_artifacts(
        "CAE build",
        cae_result,
        workspace,
        ("plate_tension.cae", "plate_tension.inp"),
    )
    assert not (workspace / "plate_tension.odb").exists()

    solver_result = asyncio.run(
        runner.run_solver_job(
            "plate_tension",
            input_file="plate_tension.inp",
            timeout_seconds=1_800.0,
        )
    )
    _assert_execution_artifacts(
        "Solver",
        solver_result,
        workspace,
        ("plate_tension.odb",),
    )

    extraction_result = asyncio.run(
        runner.run_abaqus_python(
            "extract_results.py",
            args=("plate_tension.odb", "results.json"),
            timeout_seconds=120.0,
        )
    )
    _assert_execution_artifacts(
        "ODB extraction",
        extraction_result,
        workspace,
        ("results.json",),
    )

    request = CodingRunRequest(
        run_id="abaqus2024-gold-integration",
        goal="Run the deterministic Abaqus 2024 plate-tension reference case.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            name="abaqus-2024",
            version="2024",
            launcher_path=launcher,
        ),
    )
    verifier = PlateTensionRunVerifier(plate_tension_oracle_script())
    attempt = asyncio.run(
        verifier.verify(
            request,
            runner,
            initial_snapshot,
            (cae_result, solver_result, extraction_result),
        )
    )

    assert attempt.passed, attempt.model_dump_json(indent=2)
    assert attempt.measurements is not None
    assert attempt.measurements.element_type_counts == {"CPS4R": 80}
    assert attempt.measurements.right_mean_u1 == pytest.approx(0.1, rel=0.02)
    assert attempt.measurements.mean_s11 == pytest.approx(210.0, rel=0.02)
    assert abs(attempt.measurements.left_sum_rf1) == pytest.approx(
        4200.0,
        rel=0.02,
    )


def _execution_failure(stage: str, result: AbaqusExecutionResult) -> str:
    return (
        f"{stage} failed despite exit_code={result.exit_code} and "
        f"timed_out={result.timed_out}.\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )


def _assert_execution_artifacts(
    stage: str,
    result: AbaqusExecutionResult,
    workspace: Path,
    expected_paths: tuple[str, ...],
) -> None:
    missing = [path for path in expected_paths if not (workspace / path).is_file()]
    if not result.succeeded or missing:
        pytest.fail(
            _execution_failure(stage, result)
            + f"\nmissing expected artifacts: {missing}",
            pytrace=False,
        )
