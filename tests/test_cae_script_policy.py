import asyncio
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.cae_script_policy import (
    find_cae_solver_lifecycle_calls,
)
from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.runtime_tools import _run_cae_tool
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult


def test_finds_direct_solver_lifecycle_calls(tmp_path: Path) -> None:
    script = tmp_path / "build_model.py"
    script.write_text(
        "mdb.jobs['Job-1'].submit(consistencyChecking=OFF)\njob.waitForCompletion()\n",
        encoding="utf-8",
    )

    calls = find_cae_solver_lifecycle_calls(tmp_path, script.name)

    assert [call.method for call in calls] == ["submit", "waitForCompletion"]
    assert all(call.line >= 1 and call.column >= 1 for call in calls)


def test_allows_job_creation_input_writing_and_non_code_text(tmp_path: Path) -> None:
    script = tmp_path / "build_model.py"
    script.write_text(
        "# job.submit() must remain disabled\n"
        "note = 'job.waitForCompletion()'\n"
        "job = mdb.Job(name='Job-1', model='Model-1')\n"
        "input_job = mdb.JobFromInputFile(name='Job-2', inputFileName='x.inp')\n"
        "job.writeInput()\n",
        encoding="utf-8",
    )

    assert find_cae_solver_lifecycle_calls(tmp_path, script.name) == ()


def test_dynamic_method_lookup_is_outside_minimal_policy(tmp_path: Path) -> None:
    script = tmp_path / "build_model.py"
    script.write_text("getattr(job, 'submit')()\n", encoding="utf-8")

    assert find_cae_solver_lifecycle_calls(tmp_path, script.name) == ()


def test_cae_tool_denies_solver_submit_before_budget_and_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    (workspace / "build_model.py").write_text(
        "job = mdb.Job(name='Job-1', model='Model-1')\n"
        "job.submit()\n"
        "job.waitForCompletion()\n",
        encoding="utf-8",
    )
    request = CodingRunRequest(
        run_id="cae-policy-test",
        goal="Build the model without submitting the Solver job.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
    )

    async def fail_if_executed(
        runner: Abaqus2024Runner,
        script: str,
        *,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 180.0,
    ) -> AbaqusExecutionResult:
        del runner, script, args, timeout_seconds
        raise AssertionError("blocked CAE script reached the Abaqus runner")

    monkeypatch.setattr(Abaqus2024Runner, "run_cae_script", fail_if_executed)
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(workspace, state_dir),
    )

    result = asyncio.run(
        _run_cae_tool(
            dependencies,
            "build_model.py",
            purpose="production",
            probe_protocol_enabled=False,
            probe_result=None,
            probe_target_scripts=None,
            api_catalog_mode="off",
        )
    )

    assert result["error_type"] == "CaeSolverLifecycleDenied"
    assert result["blocked"] is True
    assert result["runtime_executed"] is False
    assert result["budget_consumed"] is False
    assert result["calls"] == [
        {"method": "submit", "line": 2, "column": 1},
        {"method": "waitForCompletion", "line": 3, "column": 1},
    ]
    assert dependencies.abaqus_calls == 0
    assert dependencies.solver_calls == 0
