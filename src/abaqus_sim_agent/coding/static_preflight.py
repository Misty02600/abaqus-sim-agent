from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import perf_counter, sleep
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.contracts import StaticPreflightBinding
from abaqus_sim_agent.execution import ProcessStrategy, WindowsProcessStrategy

STATIC_PREFLIGHT_POLICY_VERSION = "abqpy-runtime-feedback-v1"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StaticPreflightDiagnostic(_FrozenModel):
    severity: Literal["error", "warning", "information"]
    rule: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4_000)
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    message_truncated: bool = False


class StaticPreflightReport(_FrozenModel):
    status: Literal["clean", "diagnostics", "timeout", "unavailable", "changed"]
    script_path: str = Field(min_length=1)
    script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine: Literal["basedpyright"] = "basedpyright"
    engine_version: str = Field(min_length=1)
    policy_version: Literal["abqpy-runtime-feedback-v1"] = (
        STATIC_PREFLIGHT_POLICY_VERSION
    )
    api_source: Literal["abqpy"] = "abqpy"
    api_source_version: str = Field(min_length=1)
    api_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(ge=0)
    exit_code: int | None
    timed_out: bool
    diagnostics: tuple[StaticPreflightDiagnostic, ...] = ()
    raw_diagnostic_count: int = Field(default=0, ge=0)
    omitted_diagnostic_count: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, min_length=1, max_length=2_000)
    stdout_raw_path: Path
    stderr_raw_path: Path


@dataclass(frozen=True, slots=True)
class PreparedAbqpySource:
    root: Path
    config_path: Path
    wheel_sha256: str
    version: str
    basedpyright_version: str

    def binding(self) -> StaticPreflightBinding:
        return StaticPreflightBinding(
            engine_version=self.basedpyright_version,
            api_source_version=self.version,
            api_source_sha256=self.wheel_sha256,
        )


def prepare_abqpy_source(wheel: Path, cache_root: Path) -> PreparedAbqpySource:
    wheel_path = wheel.resolve(strict=True)
    if not wheel_path.is_file() or wheel_path.suffix.casefold() != ".whl":
        raise ValueError("abqpy static source must be a wheel file")
    try:
        basedpyright_version = version("basedpyright")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "BasedPyright is required for static preflight; install the "
            "project static-preflight extra"
        ) from exc
    root = cache_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    wheel_sha256 = _sha256(wheel_path)
    with zipfile.ZipFile(wheel_path) as archive:
        package_version = _wheel_version(archive)
        _validate_wheel_entries(archive)
        target = root / f"abqpy-{package_version}-{wheel_sha256[:12]}"
        manifest = {
            "schema_version": "1",
            "package": "abqpy",
            "version": package_version,
            "wheel_sha256": wheel_sha256,
        }
        if target.is_symlink():
            raise ValueError("prepared abqpy source must not be a symlink")
        if target.exists():
            if not target.resolve().is_relative_to(root):
                raise ValueError("prepared abqpy source escaped its cache root")
            _validate_prepared_source(target, manifest)
        else:
            temporary = root / f".{target.name}-{uuid4().hex[:10]}"
            temporary.mkdir(parents=False, exist_ok=False)
            try:
                archive.extractall(temporary)
                (temporary / "static_source.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                _write_pyright_config(temporary, target)
                _publish_prepared_source(
                    temporary,
                    target,
                    root=root,
                    manifest=manifest,
                )
            except BaseException:
                _remove_preparation_directory(temporary, root)
                raise
    config_path = target / "pyrightconfig.json"
    if not (target / "abaqus").is_dir() or not config_path.is_file():
        raise ValueError("prepared abqpy source is incomplete")
    return PreparedAbqpySource(
        root=target,
        config_path=config_path,
        wheel_sha256=wheel_sha256,
        version=package_version,
        basedpyright_version=basedpyright_version,
    )


class AbqpyBasedPyrightPreflight:
    def __init__(
        self,
        source: PreparedAbqpySource,
        state_dir: Path,
        *,
        process_strategy: ProcessStrategy | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("static preflight timeout must be greater than zero")
        self.source = source
        self.binding = source.binding()
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self._process_strategy = process_strategy or WindowsProcessStrategy()
        self._sequence = _latest_invocation_sequence(
            self.state_dir / "static_preflight"
        )

    def script_key(self, workspace: Path, script_path: str) -> tuple[str, str]:
        relative, resolved = _resolve_script(workspace, script_path)
        return relative, _sha256(resolved)

    async def inspect(
        self,
        workspace: Path,
        script_path: str,
    ) -> StaticPreflightReport:
        relative, resolved = _resolve_script(workspace, script_path)
        script_sha256 = _sha256(resolved)
        self._sequence += 1
        invocation_dir = self.state_dir / "static_preflight" / f"{self._sequence:03d}"
        invocation_dir.mkdir(parents=True, exist_ok=False)
        stdout_path = invocation_dir / "stdout.bin"
        stderr_path = invocation_dir / "stderr.bin"
        command = (
            sys.executable,
            "-m",
            "basedpyright",
            "--project",
            str(self.source.config_path),
            "--pythonpath",
            sys.executable,
            str(resolved),
            "--outputjson",
        )
        started = perf_counter()
        output = await self._process_strategy.run(
            command,
            cwd=workspace,
            timeout_seconds=self.timeout_seconds,
        )
        duration = perf_counter() - started
        stdout_path.write_bytes(output.stdout)
        stderr_path.write_bytes(output.stderr)
        try:
            script_bytes = resolved.read_bytes()
            script_changed = hashlib.sha256(script_bytes).hexdigest() != script_sha256
        except OSError:
            script_changed = True
            script_bytes = b""
        if script_changed:
            report = StaticPreflightReport(
                status="changed",
                script_path=relative,
                script_sha256=script_sha256,
                policy_version=self.binding.policy_version,
                engine_version=self.source.basedpyright_version,
                api_source_version=self.source.version,
                api_source_sha256=self.source.wheel_sha256,
                duration_seconds=duration,
                exit_code=output.exit_code,
                timed_out=output.timed_out,
                error="Production script changed during static preflight",
                stdout_raw_path=stdout_path,
                stderr_raw_path=stderr_path,
            )
        else:
            try:
                script_text = script_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                report = StaticPreflightReport(
                    status="unavailable",
                    script_path=relative,
                    script_sha256=script_sha256,
                    policy_version=self.binding.policy_version,
                    engine_version=self.source.basedpyright_version,
                    api_source_version=self.source.version,
                    api_source_sha256=self.source.wheel_sha256,
                    duration_seconds=duration,
                    exit_code=output.exit_code,
                    timed_out=output.timed_out,
                    error=f"production script is not UTF-8: {exc}",
                    stdout_raw_path=stdout_path,
                    stderr_raw_path=stderr_path,
                )
            else:
                report = _report_from_output(
                    relative=relative,
                    script_sha256=script_sha256,
                    source=self.source,
                    binding=self.binding,
                    script_text=script_text,
                    duration_seconds=duration,
                    exit_code=output.exit_code,
                    timed_out=output.timed_out,
                    stdout=output.stdout,
                    stderr=output.stderr,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )
        with (self.state_dir / "static_preflight.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(report.model_dump_json() + "\n")
        return report


def _latest_invocation_sequence(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return max(
        (
            int(path.name)
            for path in directory.iterdir()
            if path.is_dir() and path.name.isascii() and path.name.isdecimal()
        ),
        default=0,
    )


def static_preflight_payload(report: StaticPreflightReport) -> dict[str, object]:
    return {
        **report.model_dump(
            mode="json", exclude={"stdout_raw_path", "stderr_raw_path"}
        ),
        "non_blocking": True,
        "guidance": (
            "Static analysis hints, not confirmed runtime failures. Prioritize the actual "
            "execution result; do not change successful code merely to clear diagnostics. "
            "At most 20 diagnostics and 4000 characters per message are shown; "
            "omissions and truncated messages are marked. The complete analyzer output "
            "is retained in the host state."
        ),
    }


def _report_from_output(
    *,
    relative: str,
    script_sha256: str,
    source: PreparedAbqpySource,
    binding: StaticPreflightBinding,
    script_text: str,
    duration_seconds: float,
    exit_code: int | None,
    timed_out: bool,
    stdout: bytes,
    stderr: bytes,
    stdout_path: Path,
    stderr_path: Path,
) -> StaticPreflightReport:
    common = {
        "script_path": relative,
        "script_sha256": script_sha256,
        "policy_version": binding.policy_version,
        "engine_version": source.basedpyright_version,
        "api_source_version": source.version,
        "api_source_sha256": source.wheel_sha256,
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_raw_path": stdout_path,
        "stderr_raw_path": stderr_path,
    }
    if timed_out:
        return StaticPreflightReport(
            status="timeout",
            error="BasedPyright static preflight timed out",
            **common,
        )
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = stderr.decode("utf-8", errors="replace").strip()
        return StaticPreflightReport(
            status="unavailable",
            error=(error or f"invalid BasedPyright JSON: {type(exc).__name__}")[:2_000],
            **common,
        )
    if not isinstance(payload, dict) or exit_code not in {0, 1}:
        return StaticPreflightReport(
            status="unavailable",
            error="Invalid BasedPyright result or exit status",
            **common,
        )
    raw_diagnostics = payload.get("generalDiagnostics")
    if not isinstance(raw_diagnostics, list):
        return StaticPreflightReport(
            status="unavailable",
            error="BasedPyright JSON did not contain generalDiagnostics",
            **common,
        )
    diagnostics = []
    for item in raw_diagnostics:
        diagnostic = _diagnostic_from_json(item) if isinstance(item, dict) else None
        if diagnostic is None:
            return StaticPreflightReport(
                status="unavailable",
                error="Malformed BasedPyright diagnostic",
                **common,
            )
        if diagnostic.severity in {"error", "warning"}:
            diagnostics.append(diagnostic)
    diagnostics.sort(key=lambda diagnostic: diagnostic.severity != "error")
    kept = tuple(diagnostics[:20])
    return StaticPreflightReport(
        status="diagnostics" if diagnostics else "clean",
        diagnostics=kept,
        raw_diagnostic_count=len(raw_diagnostics),
        omitted_diagnostic_count=len(raw_diagnostics) - len(kept),
        **common,
    )


def _diagnostic_from_json(
    item: dict[object, object],
) -> StaticPreflightDiagnostic | None:
    severity = item.get("severity")
    message = item.get("message")
    rule = item.get("rule") or "unknown"
    raw_range = item.get("range")
    if (
        not isinstance(message, str)
        or not isinstance(rule, str)
        or not isinstance(raw_range, dict)
    ):
        return None
    if severity == "error":
        normalized_severity: Literal["error", "warning", "information"] = "error"
    elif severity == "warning":
        normalized_severity = "warning"
    elif severity == "information":
        normalized_severity = "information"
    else:
        return None
    start = raw_range.get("start")
    if not isinstance(start, dict):
        return None
    line = start.get("line")
    character = start.get("character")
    if (
        not isinstance(line, int)
        or not isinstance(character, int)
        or line < 0
        or character < 0
        or not message
    ):
        return None
    return StaticPreflightDiagnostic(
        severity=normalized_severity,
        rule=rule,
        message=message[:4_000],
        line=line + 1,
        column=character + 1,
        message_truncated=len(message) > 4_000,
    )


def _resolve_script(workspace: Path, script_path: str) -> tuple[str, Path]:
    root = workspace.resolve(strict=True)
    candidate = (root / script_path).resolve(strict=True)
    if not candidate.is_file() or not candidate.is_relative_to(root):
        raise ValueError("static preflight script must stay within the workspace")
    if candidate.is_symlink() or candidate.suffix.casefold() != ".py":
        raise ValueError("static preflight requires a non-symlink Python script")
    return candidate.relative_to(root).as_posix(), candidate


def _wheel_version(archive: zipfile.ZipFile) -> str:
    metadata_paths = [
        name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
    ]
    if len(metadata_paths) != 1:
        raise ValueError("abqpy wheel must contain exactly one METADATA file")
    metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
    if metadata.get("Name", "").casefold() != "abqpy":
        raise ValueError("static source wheel is not abqpy")
    package_version = metadata.get("Version")
    if not package_version:
        raise ValueError("abqpy wheel metadata is missing Version")
    return package_version


def _validate_wheel_entries(archive: zipfile.ZipFile) -> None:
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        windows_path = PureWindowsPath(info.filename)
        if (
            path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in path.parts
        ):
            raise ValueError("abqpy wheel contains an unsafe path")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise ValueError("abqpy wheel must not contain symlinks")


def _validate_prepared_source(target: Path, expected: dict[str, str]) -> None:
    manifest_path = target / "static_source.json"
    if not manifest_path.is_file():
        raise ValueError("prepared abqpy source is missing its manifest")
    actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    if actual != expected:
        raise ValueError("prepared abqpy source manifest does not match the wheel")


def _publish_prepared_source(
    temporary: Path,
    target: Path,
    *,
    root: Path,
    manifest: dict[str, str],
) -> None:
    for attempt in range(5):
        try:
            temporary.replace(target)
            return
        except (FileExistsError, PermissionError):
            if target.is_dir() and not target.is_symlink():
                _validate_prepared_source(target, manifest)
                _remove_preparation_directory(temporary, root)
                return
            if attempt == 4:
                raise
            sleep(0.05 * (attempt + 1))


def _write_pyright_config(temporary: Path, final_target: Path) -> None:
    config = {
        "typeCheckingMode": "standard",
        "pythonVersion": "3.10",
        "pythonPlatform": "Windows",
        "useLibraryCodeForTypes": True,
        "extraPaths": [str(final_target)],
        "reportWildcardImportFromLibrary": "none",
    }
    (temporary / "pyrightconfig.json").write_text(
        json.dumps(config, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _remove_preparation_directory(path: Path, root: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise RuntimeError("refusing to clean static source outside its cache root")
    shutil.rmtree(resolved, ignore_errors=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
