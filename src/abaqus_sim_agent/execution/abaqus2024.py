from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_ABAQUS_2024_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_JOB_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_ABAQUS_ERROR_MARKER = re.compile(r"^\s*\*{3}\s*ERROR\b", re.IGNORECASE)
_ABAQUS_WARNING_MARKER = re.compile(r"^\s*\*{3}\s*WARNING\b", re.IGNORECASE)
_ABAQUS_TERMINATION_MARKER = re.compile(
    r"\b(?:ABORT|ABORTED|ABORTING|TERMINATED)\b",
    re.IGNORECASE,
)
_ABAQUS_POSITIVE_ERROR_SUMMARY = re.compile(
    r"^\s*(?:THE PROGRAM HAS DISCOVERED\s+)?[1-9]\d*\s+"
    r"(?:FATAL\s+)?ERROR(?:S|\s+MESSAGES?)\b",
    re.IGNORECASE,
)
_ABAQUS_POSITIVE_WARNING_SUMMARY = re.compile(
    r"^\s*[1-9]\d*\s+WARNING\s+MESSAGES?\b",
    re.IGNORECASE,
)


class AbaqusInvocation(StrEnum):
    CAE_NOGUI = "cae_nogui"
    SOLVER_JOB = "solver_job"
    ABAQUS_PYTHON = "abaqus_python"
    TRUSTED_ABAQUS_PYTHON = "trusted_abaqus_python"


class ArtifactChangeKind(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactFingerprint(_FrozenModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArtifactChange(_FrozenModel):
    kind: ArtifactChangeKind
    path: str = Field(min_length=1)
    before: ArtifactFingerprint | None = None
    after: ArtifactFingerprint | None = None


class AbaqusDiagnostic(_FrozenModel):
    level: Literal["error", "warning"]
    source: str = Field(min_length=1)
    line: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=500)


class AbaqusExecutionResult(_FrozenModel):
    invocation: AbaqusInvocation
    command: tuple[str, ...]
    workspace: Path
    exit_code: int | None
    timed_out: bool
    duration_seconds: float = Field(ge=0)
    stdout: str
    stderr: str
    stdout_encoding: str
    stderr_encoding: str
    stdout_raw_path: Path
    stderr_raw_path: Path
    input_fingerprints: tuple[ArtifactFingerprint, ...] = ()
    artifact_changes: tuple[ArtifactChange, ...] = ()
    diagnostics: tuple[AbaqusDiagnostic, ...] = ()

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    @property
    def stage_succeeded(self) -> bool:
        abaqus_error_detected = "Abaqus Error:" in self.stdout
        python_traceback_detected = (
            self.invocation
            in {AbaqusInvocation.CAE_NOGUI, AbaqusInvocation.ABAQUS_PYTHON}
            and "Traceback (most recent call last)" in self.stderr
        )
        diagnostic_error_detected = any(
            diagnostic.level == "error" for diagnostic in self.diagnostics
        )
        return (
            self.succeeded
            and not abaqus_error_detected
            and not python_traceback_detected
            and not diagnostic_error_detected
        )


@dataclass(frozen=True, slots=True)
class ProcessOutput:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


class ProcessStrategy(Protocol):
    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> ProcessOutput: ...


TreeTerminator = Callable[[int], Awaitable[None]]


class WindowsProcessStrategy:
    """运行本地进程，并在超时时终止由该进程 PID 标识的 Windows 进程树。"""

    def __init__(self, tree_terminator: TreeTerminator | None = None) -> None:
        self._tree_terminator = tree_terminator or _terminate_windows_process_tree

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> ProcessOutput:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        if os.name == "nt":
            creationflags = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        communicate_task = asyncio.create_task(process.communicate())
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communicate_task), timeout_seconds
            )
        except asyncio.CancelledError:
            await self._tree_terminator(process.pid)
            try:
                await asyncio.wait_for(asyncio.shield(communicate_task), 10.0)
            except TimeoutError:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                communicate_task.cancel()
                await asyncio.gather(communicate_task, return_exceptions=True)
            raise
        except TimeoutError:
            timed_out = True
            await self._tree_terminator(process.pid)
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communicate_task), 10.0
                )
            except TimeoutError:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                communicate_task.cancel()
                await asyncio.gather(communicate_task, return_exceptions=True)
                stdout, stderr = b"", b""

        return ProcessOutput(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )


class Abaqus2024Runner:
    """在单一受控工作区中调用 Abaqus 2024 的三个脚本运行入口。"""

    def __init__(
        self,
        workspace: Path,
        state_dir: Path,
        *,
        launcher: Path = DEFAULT_ABAQUS_2024_LAUNCHER,
        process_strategy: ProcessStrategy | None = None,
    ) -> None:
        self.workspace = _resolve_workspace(workspace)
        self.state_dir = _resolve_state_dir(state_dir, self.workspace)
        self.launcher = Path(launcher)
        self._process_strategy = process_strategy or WindowsProcessStrategy()

    async def run_cae_script(
        self,
        script: Path | str,
        *,
        args: Sequence[str] = (),
        timeout_seconds: float = 600.0,
    ) -> AbaqusExecutionResult:
        script_path = self._resolve_input_file(script, suffix=".py")
        relative_script = script_path.relative_to(self.workspace).as_posix()
        command = [str(self.launcher), "cae", f"noGUI={relative_script}"]
        if args:
            command.extend(("--", *args))
        return await self._run(
            AbaqusInvocation.CAE_NOGUI,
            command,
            timeout_seconds=timeout_seconds,
            input_artifacts=(relative_script,),
        )

    async def run_solver_job(
        self,
        job_name: str,
        *,
        input_file: Path | str | None = None,
        timeout_seconds: float = 1_800.0,
    ) -> AbaqusExecutionResult:
        if not _JOB_NAME_PATTERN.fullmatch(job_name):
            raise ValueError(
                "job_name must start with an ASCII letter and contain only "
                "ASCII letters, digits, underscores, or hyphens (max 80 chars)"
            )

        input_path = self._resolve_input_file(
            input_file if input_file is not None else f"{job_name}.inp",
            suffix=".inp",
        )
        relative_input = input_path.relative_to(self.workspace).as_posix()
        command = [
            str(self.launcher),
            f"job={job_name}",
            f"input={relative_input}",
            "interactive",
        ]
        return await self._run(
            AbaqusInvocation.SOLVER_JOB,
            command,
            timeout_seconds=timeout_seconds,
            diagnostic_stem=job_name,
            input_artifacts=(relative_input,),
        )

    async def run_abaqus_python(
        self,
        script: Path | str,
        *,
        args: Sequence[str] = (),
        timeout_seconds: float = 300.0,
    ) -> AbaqusExecutionResult:
        script_path = self._resolve_input_file(script, suffix=".py")
        relative_script = script_path.relative_to(self.workspace).as_posix()
        command = [
            str(self.launcher),
            "python",
            relative_script,
            *args,
        ]
        return await self._run(
            AbaqusInvocation.ABAQUS_PYTHON,
            command,
            timeout_seconds=timeout_seconds,
            input_artifacts=(relative_script,),
        )

    async def run_trusted_abaqus_python(
        self,
        script: Path,
        *,
        args: Sequence[str] = (),
        workspace_artifacts: Sequence[Path | str] = (),
        timeout_seconds: float = 300.0,
    ) -> AbaqusExecutionResult:
        """运行应用自有脚本；该入口不得注册为模型工具。

        Args:
            script: 应用控制的绝对脚本路径，可位于 Agent 工作区之外。
            args: 原样传给脚本的参数，由应用代码构造。
            workspace_artifacts: 脚本将读取的工作区产物，用于执行前做路径与存在性校验。
            timeout_seconds: 最长运行秒数。

        Returns:
            包含原始日志位置和工作区产物变化的执行结果。
        """
        script_path = _resolve_trusted_script(script)
        resolved_artifacts = tuple(
            self._resolve_input_file(artifact) for artifact in workspace_artifacts
        )
        command = [str(self.launcher), "python", str(script_path), *args]
        return await self._run(
            AbaqusInvocation.TRUSTED_ABAQUS_PYTHON,
            command,
            timeout_seconds=timeout_seconds,
            input_artifacts=tuple(
                path.relative_to(self.workspace).as_posix()
                for path in resolved_artifacts
            ),
        )

    async def _run(
        self,
        invocation: AbaqusInvocation,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        diagnostic_stem: str | None = None,
        input_artifacts: Sequence[str] = (),
    ) -> AbaqusExecutionResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        before = await asyncio.to_thread(_snapshot_workspace, self.workspace)
        try:
            inputs = tuple(before[path] for path in input_artifacts)
        except KeyError as exc:
            raise ValueError(
                "runtime input is absent from the pre-execution workspace snapshot: "
                f"{exc.args[0]}"
            ) from exc
        started_at = perf_counter()
        process_output = await self._process_strategy.run(
            tuple(command),
            cwd=self.workspace,
            timeout_seconds=timeout_seconds,
        )
        duration = perf_counter() - started_at
        after = await asyncio.to_thread(_snapshot_workspace, self.workspace)

        stdout_path, stderr_path = self._persist_raw_logs(
            invocation,
            process_output.stdout,
            process_output.stderr,
        )
        stdout, stdout_encoding = _decode_output(process_output.stdout)
        stderr, stderr_encoding = _decode_output(process_output.stderr)

        diagnostics = (
            await asyncio.to_thread(
                _scan_solver_diagnostics,
                self.workspace,
                diagnostic_stem,
            )
            if diagnostic_stem is not None
            else ()
        )

        return AbaqusExecutionResult(
            invocation=invocation,
            command=tuple(command),
            workspace=self.workspace,
            exit_code=process_output.exit_code,
            timed_out=process_output.timed_out,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            stdout_encoding=stdout_encoding,
            stderr_encoding=stderr_encoding,
            stdout_raw_path=stdout_path,
            stderr_raw_path=stderr_path,
            input_fingerprints=inputs,
            artifact_changes=_artifact_delta(before, after),
            diagnostics=diagnostics,
        )

    def _resolve_input_file(
        self,
        path: Path | str,
        *,
        suffix: str | None = None,
    ) -> Path:
        unresolved = Path(path)
        candidate = (
            unresolved if unresolved.is_absolute() else self.workspace / unresolved
        )
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Abaqus input file does not exist: {candidate}"
            ) from exc
        if not _is_within(resolved, self.workspace):
            raise ValueError(f"Abaqus input file must be inside workspace: {resolved}")
        if not resolved.is_file():
            raise ValueError(f"Abaqus input path must be a file: {resolved}")
        if suffix is not None and resolved.suffix.lower() != suffix:
            raise ValueError(
                f"Abaqus input file must use the {suffix} suffix: {resolved}"
            )
        return resolved

    def _persist_raw_logs(
        self,
        invocation: AbaqusInvocation,
        stdout: bytes,
        stderr: bytes,
    ) -> tuple[Path, Path]:
        log_dir = self.state_dir / "executions" / f"{invocation.value}-{uuid4().hex}"
        log_dir.mkdir(parents=True, exist_ok=False)
        stdout_path = log_dir / "stdout.bin"
        stderr_path = log_dir / "stderr.bin"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        return stdout_path, stderr_path


def _resolve_workspace(workspace: Path) -> Path:
    try:
        resolved = Path(workspace).resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"workspace does not exist: {workspace}") from exc
    if not resolved.is_dir():
        raise ValueError(f"workspace must be a directory: {resolved}")
    try:
        str(resolved).encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"workspace path must contain only ASCII characters: {resolved}"
        ) from exc
    if not os.access(resolved, os.W_OK):
        raise PermissionError(f"workspace is not writable: {resolved}")
    return resolved


def _resolve_state_dir(state_dir: Path, workspace: Path) -> Path:
    unresolved = Path(state_dir)
    candidate = unresolved.resolve(strict=False)
    if _is_within(candidate, workspace):
        raise ValueError("state_dir must be outside the Agent workspace")
    unresolved.mkdir(parents=True, exist_ok=True)
    resolved = unresolved.resolve(strict=True)
    if _is_within(resolved, workspace):
        raise ValueError("state_dir must be outside the Agent workspace")
    if not resolved.is_dir():
        raise ValueError(f"state_dir must be a directory: {resolved}")
    return resolved


def _resolve_trusted_script(script: Path) -> Path:
    unresolved = Path(script)
    if not unresolved.is_absolute():
        raise ValueError("trusted Abaqus Python script must use an absolute path")
    try:
        resolved = unresolved.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"trusted Abaqus Python script does not exist: {script}"
        ) from exc
    if not resolved.is_file() or resolved.suffix.lower() != ".py":
        raise ValueError(f"trusted Abaqus Python script must be a .py file: {resolved}")
    try:
        str(resolved).encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"trusted Abaqus Python script path must contain only ASCII: {resolved}"
        ) from exc
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _snapshot_workspace(workspace: Path) -> dict[str, ArtifactFingerprint]:
    snapshot: dict[str, ArtifactFingerprint] = {}
    for candidate in sorted(workspace.rglob("*")):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        resolved = candidate.resolve(strict=True)
        if not _is_within(resolved, workspace):
            continue
        relative_path = candidate.relative_to(workspace).as_posix()
        digest = hashlib.sha256()
        size = 0
        with candidate.open("rb") as file_handle:
            while chunk := file_handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        snapshot[relative_path] = ArtifactFingerprint(
            path=relative_path,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )
    return snapshot


def _artifact_delta(
    before: dict[str, ArtifactFingerprint],
    after: dict[str, ArtifactFingerprint],
) -> tuple[ArtifactChange, ...]:
    changes: list[ArtifactChange] = []
    for path in sorted(before.keys() | after.keys()):
        old = before.get(path)
        new = after.get(path)
        if old is None:
            changes.append(
                ArtifactChange(
                    kind=ArtifactChangeKind.CREATED,
                    path=path,
                    after=new,
                )
            )
        elif new is None:
            changes.append(
                ArtifactChange(
                    kind=ArtifactChangeKind.DELETED,
                    path=path,
                    before=old,
                )
            )
        elif old.sha256 != new.sha256 or old.size_bytes != new.size_bytes:
            changes.append(
                ArtifactChange(
                    kind=ArtifactChangeKind.MODIFIED,
                    path=path,
                    before=old,
                    after=new,
                )
            )
    return tuple(changes)


def _decode_output(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp936", errors="replace"), "cp936"


# Design reference (not a runtime dependency):
# svd-ai-lab/sim-plugin-abaqus@4118f73841245dc7890f9d526da8d7415e455d98,
# driver.py::_scan_diagnostics (Apache-2.0). This implementation narrows the scan
# to the current solver job stem and adds Abaqus' common CP936 fallback.
def _scan_solver_diagnostics(
    workspace: Path,
    job_stem: str,
    *,
    max_diagnostics: int = 50,
    max_message_chars: int = 500,
) -> tuple[AbaqusDiagnostic, ...]:
    diagnostics: list[AbaqusDiagnostic] = []
    for suffix in (".dat", ".msg", ".sta", ".log"):
        path = workspace / f"{job_stem}{suffix}"
        if not path.is_file():
            continue
        remaining = max_diagnostics - len(diagnostics)
        if remaining <= 0:
            break
        try:
            extracted = _read_diagnostic_lines(
                path,
                workspace=workspace,
                encoding="utf-8",
                errors="strict",
                limit=remaining,
                max_message_chars=max_message_chars,
            )
        except UnicodeDecodeError:
            extracted = _read_diagnostic_lines(
                path,
                workspace=workspace,
                encoding="cp936",
                errors="replace",
                limit=remaining,
                max_message_chars=max_message_chars,
            )
        except OSError:
            continue
        diagnostics.extend(extracted)
    return tuple(diagnostics)


def _read_diagnostic_lines(
    path: Path,
    *,
    workspace: Path,
    encoding: str,
    errors: Literal["strict", "replace"],
    limit: int,
    max_message_chars: int,
) -> list[AbaqusDiagnostic]:
    diagnostics: list[AbaqusDiagnostic] = []
    with path.open("r", encoding=encoding, errors=errors) as stream:
        lines = stream.readlines()

    line_index = 0
    while line_index < len(lines):
        raw_line = lines[line_index]
        line_number = line_index + 1
        line_index += 1
        message = raw_line.strip()
        if not message:
            continue
        level = _classify_abaqus_diagnostic(raw_line)
        if level is None:
            continue

        message_lines = [message]
        if _ABAQUS_ERROR_MARKER.match(raw_line) or _ABAQUS_WARNING_MARKER.match(
            raw_line
        ):
            while line_index < len(lines):
                continuation = lines[line_index]
                if not _is_abaqus_diagnostic_continuation(continuation):
                    break
                message_lines.append(continuation.strip())
                line_index += 1

        diagnostics.append(
            AbaqusDiagnostic(
                level=level,
                source=path.relative_to(workspace).as_posix(),
                line=line_number,
                message="\n".join(message_lines)[:max_message_chars],
            )
        )
        if len(diagnostics) >= limit:
            break
    return diagnostics


def _classify_abaqus_diagnostic(
    raw_line: str,
) -> Literal["error", "warning"] | None:
    if (
        _ABAQUS_ERROR_MARKER.match(raw_line)
        or _ABAQUS_TERMINATION_MARKER.search(raw_line)
        or _ABAQUS_POSITIVE_ERROR_SUMMARY.match(raw_line)
    ):
        return "error"
    if _ABAQUS_WARNING_MARKER.match(raw_line) or _ABAQUS_POSITIVE_WARNING_SUMMARY.match(
        raw_line
    ):
        return "warning"
    return None


def _is_abaqus_diagnostic_continuation(raw_line: str) -> bool:
    message = raw_line.strip()
    if not message or not raw_line[:1].isspace():
        return False
    if message.startswith("*"):
        return False
    return _classify_abaqus_diagnostic(raw_line) is None


async def _terminate_windows_process_tree(pid: int) -> None:
    if pid <= 0:
        raise ValueError("process PID must be positive")
    if os.name != "nt":
        raise RuntimeError("Windows process-tree termination requires Windows")
    await asyncio.to_thread(_taskkill, pid)


def _taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )
