from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult
from abaqus_sim_agent.verification.beam_section_change_run import (
    BeamSectionChangeRunVerifier,
)

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


def test_abaqus2024_beam_section_change_closes_seeded_loop(
    tmp_path: Path,
) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")

    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    base_root = _FIXTURE_ROOT / "beam_b31"
    change_root = _FIXTURE_ROOT / "beam_section_change"
    for name in ("build_model.py", "extract_results.py"):
        shutil.copyfile(base_root / name, workspace / name)
    seed_texts = {
        name: (workspace / name).read_text(encoding="utf-8")
        for name in ("build_model.py", "extract_results.py")
    }
    initial_snapshot = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    shutil.copyfile(change_root / "build_model.py", workspace / "build_model.py")

    runner = Abaqus2024Runner(
        workspace,
        state_dir,
        launcher=_LAUNCHER.resolve(strict=True),
    )
    cae_result = asyncio.run(
        runner.run_cae_script("build_model.py", timeout_seconds=600.0)
    )
    _assert_execution_artifacts(
        "CAE build",
        cae_result,
        workspace,
        ("beam_tension.cae", "beam_tension.inp"),
    )
    solver_result = asyncio.run(
        runner.run_solver_job(
            "beam_tension",
            input_file="beam_tension.inp",
            timeout_seconds=1_800.0,
        )
    )
    _assert_execution_artifacts(
        "Solver",
        solver_result,
        workspace,
        ("beam_tension.odb",),
    )
    extraction_result = asyncio.run(
        runner.run_abaqus_python(
            "extract_results.py",
            args=("beam_tension.odb", "results.json"),
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
        run_id="abaqus2024-beam-section-change-integration",
        goal="Change the verified B31 beam profile from 10 x 10 to 20 x 10 mm.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(launcher_path=_LAUNCHER.resolve(strict=True)),
    )
    attempt = asyncio.run(
        BeamSectionChangeRunVerifier(
            source_run_id="deterministic-beam-source",
            source_run_verified=True,
            seed_texts=seed_texts,
        ).verify(
            request,
            runner,
            initial_snapshot,
            (cae_result, solver_result, extraction_result),
        )
    )

    assert attempt.passed, attempt.model_dump_json(indent=2)
    assert attempt.measurements is not None
    assert attempt.verification is not None
    assert attempt.execution_provenance is not None
    assert attempt.change_report is not None
    assert attempt.measurements.profile_a == pytest.approx(20.0)
    assert attempt.measurements.profile_b == pytest.approx(10.0)
    assert attempt.measurements.right_mean_u1 == pytest.approx(0.1, rel=0.02)
    assert attempt.measurements.left_sum_rf1 == pytest.approx(-42_000.0, rel=0.02)
    assert attempt.measurements.right_sum_rf1 == pytest.approx(42_000.0, rel=0.02)
    assert attempt.measurements.mean_sf1 == pytest.approx(42_000.0, rel=0.02)
    assert attempt.verification.new_requirement_passed
    assert attempt.verification.regression_passed
    assert attempt.execution_provenance.passed
    assert attempt.change_report.build_model_patch
    assert attempt.change_report.extract_results_patch == ""


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
