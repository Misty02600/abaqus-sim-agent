from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

import abaqus_sim_agent.execution.abaqus2024 as runner_module
from abaqus_sim_agent.execution.abaqus2024 import (
    Abaqus2024Runner,
    AbaqusInvocation,
    ArtifactChangeKind,
    ProcessOutput,
    WindowsProcessStrategy,
)


class FakeProcessStrategy:
    def __init__(
        self,
        output: ProcessOutput,
        effect: Callable[[Path], None] | None = None,
    ) -> None:
        self.output = output
        self.effect = effect
        self.calls: list[tuple[tuple[str, ...], Path, float | None]] = []

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float | None,
    ) -> ProcessOutput:
        self.calls.append((tuple(command), cwd, timeout_seconds))
        if self.effect is not None:
            self.effect(cwd)
        return self.output


def make_runner(
    tmp_path: Path,
    strategy: FakeProcessStrategy,
) -> tuple[Abaqus2024Runner, Path, Path]:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    runner = Abaqus2024Runner(
        workspace,
        state_dir,
        launcher=Path(r"C:\fake\abq2024.bat"),
        process_strategy=strategy,
    )
    return runner, workspace.resolve(), state_dir.resolve()


@pytest.mark.asyncio
async def test_runtime_defaults_to_no_process_timeout(tmp_path: Path) -> None:
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "build_model.py").write_text("print('build')\n", encoding="utf-8")

    await runner.run_cae_script("build_model.py")

    assert strategy.calls[0][2] is None


@pytest.mark.asyncio
async def test_cae_run_uses_workspace_and_persists_raw_logs(tmp_path: Path) -> None:
    stdout = "建模完成".encode("cp936")
    stderr = b"warning\n"

    def create_input(cwd: Path) -> None:
        (cwd / "plate.inp").write_bytes(b"*HEADING\n")

    strategy = FakeProcessStrategy(
        ProcessOutput(exit_code=0, stdout=stdout, stderr=stderr),
        create_input,
    )
    runner, workspace, state_dir = make_runner(tmp_path, strategy)
    (workspace / "build_model.py").write_text("print('build')\n", encoding="utf-8")

    result = await runner.run_cae_script("build_model.py", timeout_seconds=42)

    assert result.invocation is AbaqusInvocation.CAE_NOGUI
    assert result.succeeded
    assert strategy.calls == [
        (
            (
                str(Path(r"C:\fake\abq2024.bat")),
                "cae",
                "noGUI=build_model.py",
            ),
            workspace,
            42,
        )
    ]
    assert result.stdout == "建模完成"
    assert result.stdout_encoding == "cp936"
    assert result.stderr == "warning\n"
    assert result.stderr_encoding == "utf-8"
    assert result.stdout_raw_path.read_bytes() == stdout
    assert result.stderr_raw_path.read_bytes() == stderr
    assert result.stdout_raw_path.is_relative_to(state_dir)
    assert not result.stdout_raw_path.is_relative_to(workspace)
    assert [item.path for item in result.input_fingerprints] == ["build_model.py"]

    assert len(result.artifact_changes) == 1
    created = result.artifact_changes[0]
    assert created.kind is ArtifactChangeKind.CREATED
    assert created.path == "plate.inp"
    assert created.before is None
    assert created.after is not None
    assert created.after.size_bytes == len(b"*HEADING\n")


@pytest.mark.asyncio
async def test_solver_and_python_entrypoints_build_expected_commands(
    tmp_path: Path,
) -> None:
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "plate.inp").write_text("*HEADING\n", encoding="ascii")
    (workspace / "extract.py").write_text("print('done')\n", encoding="ascii")

    solver_result = await runner.run_solver_job("plate", timeout_seconds=60)
    python_result = await runner.run_abaqus_python(
        "extract.py",
        args=("plate.odb", "results.json"),
        timeout_seconds=30,
    )

    launcher = str(Path(r"C:\fake\abq2024.bat"))
    assert solver_result.command == (
        launcher,
        "job=plate",
        "input=plate.inp",
        "interactive",
    )
    assert python_result.command == (
        launcher,
        "python",
        "extract.py",
        "plate.odb",
        "results.json",
    )
    assert [item.path for item in solver_result.input_fingerprints] == ["plate.inp"]
    assert [item.path for item in python_result.input_fingerprints] == ["extract.py"]
    assert all(len(item.sha256) == 64 for item in solver_result.input_fingerprints)
    assert all(call[1] == workspace for call in strategy.calls)


@pytest.mark.asyncio
async def test_nested_runtime_inputs_use_posix_fingerprint_paths(
    tmp_path: Path,
) -> None:
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, workspace, _ = make_runner(tmp_path, strategy)
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "build.py").write_text("print('build')\n", encoding="ascii")
    (nested / "plate.inp").write_text("*HEADING\n", encoding="ascii")
    (nested / "extract.py").write_text("print('extract')\n", encoding="ascii")

    cae = await runner.run_cae_script("nested/build.py")
    solver = await runner.run_solver_job("plate", input_file="nested/plate.inp")
    extract = await runner.run_abaqus_python("nested/extract.py")

    assert [item.path for item in cae.input_fingerprints] == ["nested/build.py"]
    assert [item.path for item in solver.input_fingerprints] == ["nested/plate.inp"]
    assert [item.path for item in extract.input_fingerprints] == ["nested/extract.py"]


@pytest.mark.asyncio
async def test_solver_extracts_bounded_job_specific_diagnostics(tmp_path: Path) -> None:
    long_message = "***WARNING: " + ("x" * 600)

    def create_solver_logs(cwd: Path) -> None:
        (cwd / "plate.dat").write_text(
            "normal line\n"
            "***ERROR: missing property definition. The elements have\n"
            "           been identified in element set ErrElemMissingSection.\n"
            f"{long_message}\n",
            encoding="utf-8",
        )
        (cwd / "plate.msg").write_bytes("作业已中止 ABORTED\n".encode("cp936"))
        (cwd / "plate.sta").write_text(
            "THE ANALYSIS HAS COMPLETED SUCCESSFULLY\n",
            encoding="ascii",
        )
        (cwd / "plate.log").write_text(
            "Abaqus JOB plate TERMINATED\n",
            encoding="ascii",
        )
        (cwd / "other.dat").write_text(
            "***ERROR: unrelated job\n",
            encoding="utf-8",
        )

    strategy = FakeProcessStrategy(
        ProcessOutput(exit_code=1, stdout=b"", stderr=b""),
        create_solver_logs,
    )
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "plate.inp").write_text("*HEADING\n", encoding="ascii")

    result = await runner.run_solver_job("plate")

    assert [item.model_dump() for item in result.diagnostics] == [
        {
            "level": "error",
            "source": "plate.dat",
            "line": 2,
            "message": (
                "***ERROR: missing property definition. The elements have\n"
                "been identified in element set ErrElemMissingSection."
            ),
        },
        {
            "level": "warning",
            "source": "plate.dat",
            "line": 4,
            "message": long_message[:500],
        },
        {
            "level": "error",
            "source": "plate.msg",
            "line": 1,
            "message": "作业已中止 ABORTED",
        },
        {
            "level": "error",
            "source": "plate.log",
            "line": 1,
            "message": "Abaqus JOB plate TERMINATED",
        },
    ]


@pytest.mark.asyncio
async def test_solver_ignores_error_and_warning_words_in_normal_status_lines(
    tmp_path: Path,
) -> None:
    def create_solver_log(cwd: Path) -> None:
        (cwd / "plate.msg").write_text(
            "CONTACT FORCE ERROR TOLERANCE FOR CONVERT SDI=YES 1.00\n"
            "0 WARNING MESSAGES DURING USER INPUT PROCESSING\n"
            "0 WARNING MESSAGES DURING ANALYSIS\n"
            "0 ANALYSIS WARNINGS ARE NUMERICAL PROBLEM MESSAGES\n"
            "0 ERROR MESSAGES\n",
            encoding="utf-8",
        )

    strategy = FakeProcessStrategy(
        ProcessOutput(exit_code=0, stdout=b"", stderr=b""),
        create_solver_log,
    )
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "plate.inp").write_text("*HEADING\n", encoding="ascii")

    result = await runner.run_solver_job("plate")

    assert result.diagnostics == ()


@pytest.mark.asyncio
async def test_solver_keeps_explicit_and_positive_summary_diagnostics(
    tmp_path: Path,
) -> None:
    def create_solver_log(cwd: Path) -> None:
        (cwd / "plate.dat").write_text(
            "***WARNING: explicit warning\n"
            "2 WARNING MESSAGES DURING ANALYSIS\n"
            "THE PROGRAM HAS DISCOVERED 3 FATAL ERRORS\n"
            "** EXECUTION IS TERMINATED **\n",
            encoding="utf-8",
        )

    strategy = FakeProcessStrategy(
        ProcessOutput(exit_code=1, stdout=b"", stderr=b""),
        create_solver_log,
    )
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "plate.inp").write_text("*HEADING\n", encoding="ascii")

    result = await runner.run_solver_job("plate")

    assert [(item.level, item.line, item.message) for item in result.diagnostics] == [
        ("warning", 1, "***WARNING: explicit warning"),
        ("warning", 2, "2 WARNING MESSAGES DURING ANALYSIS"),
        ("error", 3, "THE PROGRAM HAS DISCOVERED 3 FATAL ERRORS"),
        ("error", 4, "** EXECUTION IS TERMINATED **"),
    ]


@pytest.mark.asyncio
async def test_solver_diagnostic_count_is_limited(tmp_path: Path) -> None:
    def create_solver_log(cwd: Path) -> None:
        (cwd / "plate.msg").write_text(
            "\n".join(f"***WARNING: repeated {index}" for index in range(60)),
            encoding="utf-8",
        )

    strategy = FakeProcessStrategy(
        ProcessOutput(exit_code=1, stdout=b"", stderr=b""),
        create_solver_log,
    )
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "plate.inp").write_text("*HEADING\n", encoding="ascii")

    result = await runner.run_solver_job("plate")

    assert len(result.diagnostics) == 50
    assert result.diagnostics[-1].message == "***WARNING: repeated 49"


@pytest.mark.asyncio
async def test_trusted_python_can_run_an_oracle_outside_agent_workspace(
    tmp_path: Path,
) -> None:
    strategy = FakeProcessStrategy(
        ProcessOutput(exit_code=0, stdout=b"verified", stderr=b"")
    )
    runner, workspace, _ = make_runner(tmp_path, strategy)
    oracle = tmp_path / "hidden_oracle.py"
    odb = workspace / "plate.odb"
    oracle.write_text("print('verify')\n", encoding="ascii")
    odb.write_bytes(b"odb")

    result = await runner.run_trusted_abaqus_python(
        oracle.resolve(),
        args=("plate.odb",),
        workspace_artifacts=(odb,),
    )

    assert result.invocation is AbaqusInvocation.TRUSTED_ABAQUS_PYTHON
    assert result.command == (
        str(Path(r"C:\fake\abq2024.bat")),
        "python",
        str(oracle.resolve()),
        "plate.odb",
    )
    assert strategy.calls[0][1] == workspace


@pytest.mark.asyncio
async def test_trusted_python_validates_declared_workspace_artifacts(
    tmp_path: Path,
) -> None:
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, _, _ = make_runner(tmp_path, strategy)
    oracle = tmp_path / "hidden_oracle.py"
    outside_odb = tmp_path / "outside.odb"
    oracle.write_text("pass\n", encoding="ascii")
    outside_odb.write_bytes(b"odb")

    with pytest.raises(ValueError, match="inside workspace"):
        await runner.run_trusted_abaqus_python(
            oracle.resolve(),
            args=(str(outside_odb),),
            workspace_artifacts=(outside_odb,),
        )

    assert strategy.calls == []


@pytest.mark.asyncio
async def test_artifact_delta_records_created_modified_and_deleted_files(
    tmp_path: Path,
) -> None:
    def mutate_workspace(cwd: Path) -> None:
        (cwd / "modified.dat").write_bytes(b"new")
        (cwd / "deleted.msg").unlink()
        (cwd / "created.odb").write_bytes(b"odb")

    strategy = FakeProcessStrategy(
        ProcessOutput(exit_code=0, stdout=b"", stderr=b""),
        mutate_workspace,
    )
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "extract.py").write_text("pass\n", encoding="ascii")
    (workspace / "modified.dat").write_bytes(b"old")
    (workspace / "deleted.msg").write_bytes(b"gone")

    result = await runner.run_abaqus_python("extract.py")

    changes = {change.path: change for change in result.artifact_changes}
    assert changes["created.odb"].kind is ArtifactChangeKind.CREATED
    assert changes["created.odb"].after is not None
    assert changes["modified.dat"].kind is ArtifactChangeKind.MODIFIED
    assert changes["modified.dat"].before is not None
    assert changes["modified.dat"].after is not None
    assert changes["modified.dat"].before.sha256 != changes["modified.dat"].after.sha256
    assert changes["deleted.msg"].kind is ArtifactChangeKind.DELETED
    assert changes["deleted.msg"].before is not None
    assert changes["deleted.msg"].after is None


@pytest.mark.asyncio
async def test_timeout_is_returned_as_a_failed_execution(tmp_path: Path) -> None:
    strategy = FakeProcessStrategy(
        ProcessOutput(
            exit_code=1,
            stdout=b"partial",
            stderr=b"timed out",
            timed_out=True,
        )
    )
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "extract.py").write_text("pass\n", encoding="ascii")

    result = await runner.run_abaqus_python("extract.py", timeout_seconds=0.5)

    assert result.timed_out
    assert not result.succeeded
    assert result.exit_code == 1
    assert result.stdout_raw_path.read_bytes() == b"partial"


@pytest.mark.asyncio
async def test_runner_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, _, _ = make_runner(tmp_path, strategy)
    outside_script = tmp_path / "outside.py"
    outside_script.write_text("pass\n", encoding="ascii")

    with pytest.raises(ValueError, match="inside workspace"):
        await runner.run_cae_script(outside_script)

    assert strategy.calls == []


@pytest.mark.parametrize(
    "job_name",
    ["1plate", "plate model", "a" * 81],
)
@pytest.mark.asyncio
async def test_solver_rejects_invalid_job_names(
    tmp_path: Path,
    job_name: str,
) -> None:
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, _, _ = make_runner(tmp_path, strategy)

    with pytest.raises(ValueError, match="job_name"):
        await runner.run_solver_job(job_name)

    assert strategy.calls == []


def test_runner_rejects_non_ascii_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "模型"
    workspace.mkdir()
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))

    with pytest.raises(ValueError, match="ASCII"):
        Abaqus2024Runner(
            workspace,
            tmp_path / "state",
            process_strategy=strategy,
        )


def test_runner_rejects_state_directory_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = workspace / ".agent-state"
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))

    with pytest.raises(ValueError, match="outside"):
        Abaqus2024Runner(workspace, state_dir, process_strategy=strategy)

    assert not state_dir.exists()


@pytest.mark.asyncio
async def test_windows_strategy_terminates_the_exact_spawned_process_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    terminated_pids: list[int] = []
    release = runner_module.asyncio.Event()

    class HangingProcess:
        pid = 43210
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await release.wait()
            self.returncode = 1
            return b"partial stdout", b"terminated"

        async def wait(self) -> int:
            return self.returncode or 0

        def kill(self) -> None:
            self.returncode = 1
            release.set()

    process = HangingProcess()
    spawn_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    async def fake_spawn(*command: str, **kwargs: object) -> HangingProcess:
        spawn_calls.append((command, kwargs))
        return process

    async def terminate_tree(pid: int) -> None:
        terminated_pids.append(pid)
        process.returncode = 1
        release.set()

    monkeypatch.setattr(
        runner_module,
        "os",
        SimpleNamespace(name="nt"),
    )
    monkeypatch.setattr(
        runner_module.asyncio,
        "create_subprocess_exec",
        fake_spawn,
    )
    strategy = WindowsProcessStrategy(tree_terminator=terminate_tree)

    result = await strategy.run(
        ("abq2024.bat", "python", "script.py"),
        cwd=tmp_path,
        timeout_seconds=0.001,
    )

    assert terminated_pids == [43210]
    assert result.timed_out
    assert result.stdout == b"partial stdout"
    assert spawn_calls[0][0] == ("abq2024.bat", "python", "script.py")
    assert spawn_calls[0][1]["cwd"] == tmp_path
    assert spawn_calls[0][1]["creationflags"] == (
        runner_module._CREATE_NEW_PROCESS_GROUP | runner_module._CREATE_NO_WINDOW
    )


@pytest.mark.asyncio
async def test_windows_strategy_reports_spawned_process_id_before_waiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_pids: list[int] = []

    class ImmediateProcess:
        pid = 24680
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"ok", b""

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:
            self.returncode = 1

    async def fake_spawn(*_command: str, **_kwargs: object) -> ImmediateProcess:
        return ImmediateProcess()

    monkeypatch.setattr(runner_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        runner_module.asyncio,
        "create_subprocess_exec",
        fake_spawn,
    )
    strategy = WindowsProcessStrategy()

    result = await strategy.run_with_start_callback(
        ("abq2024.bat", "job=demo"),
        cwd=tmp_path,
        timeout_seconds=60.0,
        process_started=observed_pids.append,
    )

    assert observed_pids == [24680]
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_windows_strategy_terminates_process_tree_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    terminated_pids: list[int] = []
    started = runner_module.asyncio.Event()
    release = runner_module.asyncio.Event()

    class HangingProcess:
        pid = 54321
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            started.set()
            await release.wait()
            self.returncode = 1
            return b"", b"cancelled"

        async def wait(self) -> int:
            return self.returncode or 0

        def kill(self) -> None:
            self.returncode = 1
            release.set()

    process = HangingProcess()

    async def fake_spawn(*_command: str, **_kwargs: object) -> HangingProcess:
        return process

    async def terminate_tree(pid: int) -> None:
        terminated_pids.append(pid)
        process.returncode = 1
        release.set()

    monkeypatch.setattr(runner_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        runner_module.asyncio,
        "create_subprocess_exec",
        fake_spawn,
    )
    strategy = WindowsProcessStrategy(tree_terminator=terminate_tree)
    task = runner_module.asyncio.create_task(
        strategy.run(
            ("abq2024.bat", "python", "script.py"),
            cwd=tmp_path,
            timeout_seconds=60.0,
        )
    )
    await started.wait()

    task.cancel()
    with pytest.raises(runner_module.asyncio.CancelledError):
        await task

    assert terminated_pids == [54321]


def test_taskkill_uses_exact_pid_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> None:
        calls.append((command, kwargs))

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    runner_module._taskkill(2468)

    assert calls[0][0] == ["taskkill", "/PID", "2468", "/T", "/F"]
    assert "shell" not in calls[0][1]


@pytest.mark.skipif(os.name != "nt", reason="Windows batch command discovery")
@pytest.mark.asyncio
async def test_named_abaqus_command_uses_path_without_installation_configuration(
    tmp_path, monkeypatch
):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "abq2024.bat").write_text(
        "@echo off\necho named-command:%1\n", encoding="ascii"
    )
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ.get("PATH", ""))
    result = await WindowsProcessStrategy().run(
        ("abq2024", "fixture"), cwd=tmp_path, timeout_seconds=5
    )
    assert result.exit_code == 0
    assert b"named-command:fixture" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows batch command discovery")
@pytest.mark.asyncio
async def test_missing_named_abaqus_command_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module.shutil, "which", lambda command: None)
    with pytest.raises(FileNotFoundError, match="abq2024 is not available"):
        await WindowsProcessStrategy().run(
            ("abq2024", "cae"), cwd=tmp_path, timeout_seconds=5
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("mechanics", [False, True])
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ()),
        ({"cpus": 4}, ("cpus=4",)),
        ({"memory": "50%"}, ("memory=50%",)),
        ({"cpus": 2, "memory": "1.5 GB"}, ("cpus=2", "memory=1.5 gb")),
    ],
)
async def test_solver_resources_reach_command_and_saved_evidence(
    tmp_path, background, mechanics, options, expected
):
    from abaqus_sim_agent.evaluation.public_mechanics_runner import (
        PublicMechanicsRunner,
    )

    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, workspace, state = make_runner(tmp_path, strategy)
    if mechanics:
        runner = PublicMechanicsRunner(
            workspace, state, launcher=runner.launcher, process_strategy=strategy
        )
    (workspace / "plate.inp").write_text("*HEADING\n", encoding="ascii")
    if background:
        execution_id = runner.submit_solver_job(
            "plate", input_file="plate.inp", **options
        )
        result = await runner.wait_solver_job(execution_id)
    else:
        result = await runner.run_solver_job("plate", input_file="plate.inp", **options)
    assert result.command == (
        str(runner.launcher),
        "job=plate",
        "input=plate.inp",
        "interactive",
        *expected,
    )
    assert strategy.calls[0][0] == result.command
    assert all(argument in result.model_dump_json() for argument in expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize(
    "options",
    [
        {"cpus": 0},
        {"cpus": -1},
        {"cpus": True},
        {"cpus": 1.5},
        {"cpus": "2"},
        {"memory": "0 gb"},
        {"memory": "101%"},
        {"memory": "2 gb & echo bad"},
        {"memory": "NaN gb"},
        {"memory": 512},
    ],
)
async def test_invalid_solver_resources_do_not_start_process(
    tmp_path, background, options
):
    strategy = FakeProcessStrategy(ProcessOutput(exit_code=0, stdout=b"", stderr=b""))
    runner, workspace, _ = make_runner(tmp_path, strategy)
    (workspace / "plate.inp").write_text("*HEADING\n", encoding="ascii")
    if background:
        with pytest.raises(ValueError, match="validation error"):
            runner.submit_solver_job("plate", input_file="plate.inp", **options)
    else:
        with pytest.raises(ValueError, match="validation error"):
            await runner.run_solver_job("plate", input_file="plate.inp", **options)
    assert not strategy.calls
    assert not runner._solver_tasks
