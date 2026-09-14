from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ProbeAdoptionTrace,
    ProbeConclusionTrace,
    ProbeTrace,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.probe import ProbeProtocolState
from abaqus_sim_agent.execution import AbaqusExecutionResult, AbaqusInvocation


def _request(tmp_path: Path) -> CodingRunRequest:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    return CodingRunRequest(
        run_id="probe-test",
        goal="Test a runtime candidate and adopt it into production.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(launcher_path=(tmp_path / "abq2024.bat").resolve()),
    )


def _execution(request: CodingRunRequest, script: str) -> AbaqusExecutionResult:
    return AbaqusExecutionResult(
        invocation=AbaqusInvocation.CAE_NOGUI,
        command=("abq2024.bat", "cae", f"noGUI={script}"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="PROBE DONE\n",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
    )


def test_validated_probe_blocks_until_changed_target_runs(tmp_path: Path) -> None:
    request = _request(tmp_path)
    (request.workspace / "probe.py").write_text("print('probe')\n", encoding="utf-8")
    target = request.workspace / "build_model.py"
    target.write_text("old production\n", encoding="utf-8")
    state = ProbeProtocolState(
        request,
        allowed_target_scripts=frozenset(("build_model.py",)),
    )

    assessment = state.register_diagnostic_execution(
        "probe.py",
        _execution(request, "probe.py"),
        runtime_stage_succeeded=True,
    )

    assert assessment["probe_id"] == "probe-001"
    assert state.before_solver_execution() is not None
    recorded = state.record_result(
        status="validated",
        claim="The candidate call creates a side-aware Surface.",
        evidence="The probe created the expected runtime object.",
        target_script="build_model.py",
    )
    assert recorded["status"] == "recorded"
    diagnostic_block = state.before_script_execution(
        purpose="diagnostic",
        script_path="probe.py",
    )
    assert diagnostic_block is not None
    assert diagnostic_block["blocked"]
    unchanged_block = state.before_script_execution(
        purpose="production",
        script_path="build_model.py",
    )
    assert unchanged_block is not None
    assert unchanged_block["blocked"]

    target.write_text("new production\n", encoding="utf-8")
    assert (
        state.before_script_execution(
            purpose="production",
            script_path="build_model.py",
        )
        is None
    )
    adoption = state.register_production_execution(
        "build_model.py",
        _execution(request, "build_model.py"),
        runtime_stage_succeeded=True,
    )

    assert adoption is not None
    assert adoption["production_stage_succeeded"] is True
    assert state.before_solver_execution() is None
    assert state.unresolved_feedback() is None
    traces = [
        TypeAdapter(ProbeTrace).validate_json(line)
        for line in (request.state_dir / "probe_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert isinstance(traces[0], ProbeConclusionTrace)
    assert isinstance(traces[1], ProbeAdoptionTrace)
    assert traces[0].probe_id == traces[1].probe_id == "probe-001"
    assert traces[0].target_sha256_before != traces[1].target_sha256_after


def test_probe_must_be_concluded_before_another_runtime_call(tmp_path: Path) -> None:
    request = _request(tmp_path)
    (request.workspace / "probe.py").write_text("print('probe')\n", encoding="utf-8")
    state = ProbeProtocolState(request)
    state.register_diagnostic_execution(
        "probe.py",
        _execution(request, "probe.py"),
        runtime_stage_succeeded=False,
    )

    pending_block = state.before_script_execution(
        purpose="diagnostic",
        script_path="probe.py",
    )
    assert pending_block is not None
    assert pending_block["blocked"]
    invalid = state.record_result(
        status="validated",
        claim="The candidate works.",
        evidence="The diagnostic stage actually failed.",
        target_script="build_model.py",
    )
    assert invalid["status"] == "invalid"
    recorded = state.record_result(
        status="rejected",
        claim="The candidate call is rejected.",
        evidence="The diagnostic runtime stage failed before creating the object.",
        target_script=None,
    )
    assert recorded["status"] == "recorded"
    assert state.unresolved_feedback() is None


def test_validated_probe_rejects_nonproduction_target(tmp_path: Path) -> None:
    request = _request(tmp_path)
    for name in ("probe.py", "build_model.py", "probe_next.py"):
        (request.workspace / name).write_text(f"print({name!r})\n", encoding="utf-8")
    state = ProbeProtocolState(
        request,
        allowed_target_scripts=frozenset(("build_model.py",)),
    )
    state.register_diagnostic_execution(
        "probe.py",
        _execution(request, "probe.py"),
        runtime_stage_succeeded=True,
    )

    invalid = state.record_result(
        status="validated",
        claim="A side-aware region is required.",
        evidence="The runtime accepted the Surface candidate.",
        target_script="probe_next.py",
    )

    assert invalid["status"] == "invalid"
    assert "build_model.py" in str(invalid["error"])
    assert state.pending_assessment is not None
