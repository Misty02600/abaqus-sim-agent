from __future__ import annotations

import ast
import hashlib
import json
import re
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

ADVISORY_STATIC_PREFLIGHT_POLICY_VERSION = "abqpy-advisory-high-confidence-v2"
ENFORCED_STATIC_PREFLIGHT_POLICY_VERSION = "abqpy-enforced-verified-v1"
_IGNORED_RULES = frozenset({"reportWildcardImportFromLibrary"})
_JOB_WRITE_INPUT_OVERRIDE_ID = "licensed-abq2024-job-write-input-v1"
_B31_PREFLIGHT_EVIDENCE = (
    "benchmarks/coding/beam_tension_b31_abqpy_preflight_evaluation_20260829.json"
)
_API_REPAIR_REGRESSION_EVIDENCE = (
    "benchmarks/coding/api_repair_regression_evaluation_20260824.json"
)
_API_REPAIR_FINAL11_EVIDENCE = (
    "benchmarks/coding/api_repair_final13_evaluation_20260828.json"
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StaticPreflightDiagnostic(_FrozenModel):
    severity: Literal["error", "warning", "information"]
    rule: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4_000)
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    disposition: Literal["advisory", "block", "warn"] = "advisory"
    policy_rule_id: str | None = Field(default=None, min_length=1)
    evidence_refs: tuple[str, ...] = ()
    callable_name: str | None = Field(default=None, min_length=1)


class StaticPreflightReport(_FrozenModel):
    status: Literal["clean", "diagnostics", "timeout", "unavailable", "changed"]
    script_path: str = Field(min_length=1)
    script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine: Literal["basedpyright"] = "basedpyright"
    engine_version: str = Field(min_length=1)
    mode: Literal["advisory", "enforced_verified"] = "advisory"
    policy_version: Literal[
        "abqpy-advisory-high-confidence-v2",
        "abqpy-enforced-verified-v1",
    ] = ADVISORY_STATIC_PREFLIGHT_POLICY_VERSION
    api_source: Literal["abqpy"] = "abqpy"
    api_source_version: str = Field(min_length=1)
    api_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(ge=0)
    exit_code: int | None
    timed_out: bool
    diagnostics: tuple[StaticPreflightDiagnostic, ...] = ()
    ignored_diagnostic_count: int = Field(default=0, ge=0)
    applied_override_ids: tuple[str, ...] = ()
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

    def binding(
        self,
        mode: Literal["advisory", "enforced_verified"] = "advisory",
    ) -> StaticPreflightBinding:
        if mode == "advisory":
            return StaticPreflightBinding(
                engine_version=self.basedpyright_version,
                api_source_version=self.version,
                api_source_sha256=self.wheel_sha256,
            )
        return StaticPreflightBinding(
            mode="enforced_verified",
            policy_version=ENFORCED_STATIC_PREFLIGHT_POLICY_VERSION,
            syntax_gate_version="python-3.10-production-syntax-v1",
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
        mode: Literal["advisory", "enforced_verified"] = "advisory",
        process_strategy: ProcessStrategy | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("static preflight timeout must be greater than zero")
        self.source = source
        self.binding = source.binding(mode)
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
                mode=self.binding.mode,
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
                    mode=self.binding.mode,
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
    blockers = [
        diagnostic.model_dump(mode="json")
        for diagnostic in report.diagnostics
        if diagnostic.disposition == "block"
    ]
    warnings = [
        diagnostic.model_dump(mode="json")
        for diagnostic in report.diagnostics
        if diagnostic.disposition == "warn"
    ]
    advisory_diagnostics = [
        diagnostic.model_dump(mode="json")
        for diagnostic in report.diagnostics
        if diagnostic.disposition == "advisory"
    ]
    return {
        "status": report.status,
        "script_path": report.script_path,
        "script_sha256": report.script_sha256,
        "engine": report.engine,
        "engine_version": report.engine_version,
        "mode": report.mode,
        "policy_version": report.policy_version,
        "api_source": report.api_source,
        "api_source_version": report.api_source_version,
        "diagnostics": [
            diagnostic.model_dump(mode="json") for diagnostic in report.diagnostics
        ],
        "blockers": blockers,
        "warnings": warnings,
        "advisory_diagnostics": advisory_diagnostics,
        "ignored_diagnostic_count": report.ignored_diagnostic_count,
        "applied_override_ids": list(report.applied_override_ids),
        "advisory": report.mode == "advisory",
        "enforced": report.mode == "enforced_verified",
        "runtime_executed": False,
        "budget_consumed": False,
        "guidance": (
            "Apply every verified blocker to the production script before "
            "requesting vendor runtime again. The model cannot override a blocker; "
            "warnings are non-blocking and remain subject to licensed runtime."
            if report.mode == "enforced_verified"
            else (
                "The host has already filtered raw abqpy noise through a pinned "
                "high-confidence policy. Review every reported item and apply all "
                "independent supported repairs in one edit before vendor execution. "
                "Use an unchanged repeat only when a specific exact-Catalog or prior "
                "licensed-runtime fact contradicts a diagnostic."
            )
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
        "mode": binding.mode,
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
    raw_diagnostics = payload.get("generalDiagnostics")
    if not isinstance(raw_diagnostics, list):
        return StaticPreflightReport(
            status="unavailable",
            error="BasedPyright JSON did not contain generalDiagnostics",
            **common,
        )
    blockers: list[StaticPreflightDiagnostic] = []
    warnings: list[StaticPreflightDiagnostic] = []
    advisories: list[StaticPreflightDiagnostic] = []
    ignored = 0
    applied_overrides: list[str] = []
    for item in raw_diagnostics:
        if not isinstance(item, dict):
            ignored += 1
            continue
        rule = item.get("rule")
        if rule in _IGNORED_RULES:
            ignored += 1
            continue
        diagnostic = _diagnostic_from_json(item)
        if diagnostic is None:
            ignored += 1
            continue
        decision = _diagnostic_policy(
            diagnostic,
            mode=binding.mode,
            script_text=script_text,
        )
        if decision.disposition == "ignore":
            ignored += 1
            if (
                decision.override_id is not None
                and decision.override_id not in applied_overrides
            ):
                applied_overrides.append(decision.override_id)
            continue
        classified = diagnostic.model_copy(
            update={
                "disposition": decision.disposition,
                "policy_rule_id": decision.policy_rule_id,
                "evidence_refs": decision.evidence_refs,
                "callable_name": decision.callable_name,
            }
        )
        target = {
            "block": blockers,
            "warn": warnings,
            "advisory": advisories,
        }[decision.disposition]
        target.append(classified)
    if binding.mode == "enforced_verified":
        kept_blockers = blockers[:20]
        warning_limit = min(3, max(0, 20 - len(kept_blockers)))
        kept_warnings = warnings[:warning_limit]
        diagnostics = (*kept_blockers, *kept_warnings)
        dropped = (
            len(blockers)
            - len(kept_blockers)
            + len(warnings)
            - len(kept_warnings)
            + len(advisories)
        )
    else:
        diagnostics = tuple(advisories[:20])
        dropped = len(advisories) - len(diagnostics) + len(blockers) + len(warnings)
    return StaticPreflightReport(
        status="diagnostics" if diagnostics else "clean",
        diagnostics=tuple(diagnostics),
        ignored_diagnostic_count=ignored + dropped,
        applied_override_ids=tuple(applied_overrides),
        **common,
    )


@dataclass(frozen=True, slots=True)
class _DiagnosticDecision:
    disposition: Literal["advisory", "block", "warn", "ignore"]
    policy_rule_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    callable_name: str | None = None
    override_id: str | None = None


def _diagnostic_policy(
    diagnostic: StaticPreflightDiagnostic,
    *,
    mode: Literal["advisory", "enforced_verified"],
    script_text: str,
) -> _DiagnosticDecision:
    message = diagnostic.message
    callable_name = _callable_at_line(script_text, diagnostic.line)
    if diagnostic.rule == "reportAttributeAccessIssue":
        if 'attribute "writeInput"' in message and (
            'class "Job"' in message or 'class "JobFromInputFile"' in message
        ):
            return _DiagnosticDecision(
                disposition="ignore",
                override_id=_JOB_WRITE_INPUT_OVERRIDE_ID,
            )
        if mode == "advisory":
            return _DiagnosticDecision(
                disposition="advisory",
                policy_rule_id="advisory-attribute-access-v1",
                callable_name=callable_name,
            )
        match = re.search(
            r'Cannot access attribute "(?P<attribute>[^"]+)" for class "(?P<class>[^"]+)"',
            message,
        )
        verified_attributes = {
            ("Part", "BaseSolidBox"): (
                "verified-attribute-part-base-solid-box-v1",
                _API_REPAIR_REGRESSION_EVIDENCE,
            ),
            ("PartInstance", "Set"): (
                "verified-attribute-part-instance-set-v1",
                _API_REPAIR_REGRESSION_EVIDENCE,
            ),
            ("Part", "seedPartByNumber"): (
                "verified-attribute-part-seed-part-by-number-v1",
                _B31_PREFLIGHT_EVIDENCE,
            ),
            ("Model", "writeInput"): (
                "verified-attribute-model-write-input-v1",
                _B31_PREFLIGHT_EVIDENCE,
            ),
        }
        verified = None
        if match is not None:
            key = (match.group("class"), match.group("attribute"))
            verified = verified_attributes.get(key)
        if verified is not None:
            rule_id, evidence_ref = verified
            return _DiagnosticDecision(
                disposition="block",
                policy_rule_id=rule_id,
                evidence_refs=(evidence_ref,),
                callable_name=callable_name,
            )
        return _DiagnosticDecision(
            disposition="warn",
            policy_rule_id="unverified-attribute-access-warning-v1",
            callable_name=callable_name,
        )
    if diagnostic.rule == "reportCallIssue":
        actionable_prefixes = (
            "No parameter named ",
            "Arguments missing for parameters ",
        )
        if not message.startswith(actionable_prefixes):
            return _DiagnosticDecision(disposition="ignore")
        if mode == "advisory":
            return _DiagnosticDecision(
                disposition="advisory",
                policy_rule_id="advisory-call-contract-v1",
                callable_name=callable_name,
            )
        terminal = callable_name.rsplit(".", maxsplit=1)[-1] if callable_name else None
        parameter_match = re.match(r'No parameter named "(?P<name>[^"]+)"', message)
        parameter_name = (
            parameter_match.group("name") if parameter_match is not None else None
        )
        if terminal == "BeamSection" and parameter_name in {
            "orientation",
            "beamOrientation",
            "geometricType",
            "createStepName",
        }:
            return _DiagnosticDecision(
                disposition="block",
                policy_rule_id="verified-model-beam-section-keywords-v1",
                evidence_refs=(
                    _B31_PREFLIGHT_EVIDENCE,
                    "benchmarks/api_catalog/abaqus_2024_beam_section_orientation_observations.json",
                ),
                callable_name=callable_name,
            )
        if terminal == "assignBeamSectionOrientation" and (
            parameter_name == "orientation"
            or message.startswith("Arguments missing for parameters ")
        ):
            return _DiagnosticDecision(
                disposition="block",
                policy_rule_id="verified-beam-orientation-call-contract-v1",
                evidence_refs=(
                    _B31_PREFLIGHT_EVIDENCE,
                    "benchmarks/api_catalog/abaqus_2024_api_consumer_observations.json",
                ),
                callable_name=callable_name,
            )
        return _DiagnosticDecision(
            disposition="warn",
            policy_rule_id="unverified-call-contract-warning-v1",
            callable_name=callable_name,
        )
    if diagnostic.rule == "reportArgumentType":
        if (
            'parameter "region"' in message
            and 'function "assignBeamSectionOrientation"' in message
        ):
            return _DiagnosticDecision(
                disposition="ignore",
                override_id="licensed-abq2024-beam-orientation-region-v1",
            )
        direct_face_region = (
            message.startswith('Argument of type "list[Face]"')
            and 'parameter "regions"' in message
            and 'function "setElementType"' in message
        )
        flat_surface_edges = (
            message.startswith('Argument of type "tuple[Edge')
            and 'parameter "side1Edges"' in message
            and 'function "Surface"' in message
        )
        if mode == "advisory":
            if not (direct_face_region or flat_surface_edges):
                return _DiagnosticDecision(disposition="ignore")
            return _DiagnosticDecision(
                disposition="advisory",
                policy_rule_id="advisory-verified-container-binding-v1",
                callable_name=callable_name,
            )
        if direct_face_region:
            return _DiagnosticDecision(
                disposition="block",
                policy_rule_id="verified-set-element-type-direct-face-v1",
                evidence_refs=(_API_REPAIR_FINAL11_EVIDENCE,),
                callable_name=callable_name,
            )
        if flat_surface_edges:
            return _DiagnosticDecision(
                disposition="block",
                policy_rule_id="verified-surface-flat-edge-tuple-v1",
                evidence_refs=(_API_REPAIR_FINAL11_EVIDENCE,),
                callable_name=callable_name,
            )
        if not any(
            marker in message
            for marker in ('function "setElementType"', 'function "Surface"')
        ):
            return _DiagnosticDecision(disposition="ignore")
        return _DiagnosticDecision(
            disposition="warn",
            policy_rule_id="swig-container-type-warning-v1",
            callable_name=callable_name,
        )
    return _DiagnosticDecision(disposition="ignore")


def _callable_at_line(script_text: str, line: int) -> str | None:
    try:
        tree = ast.parse(script_text, mode="exec", feature_version=(3, 10))
    except (SyntaxError, TypeError, ValueError):
        return None
    candidates: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        end_line = node.end_lineno or node.lineno
        if node.lineno <= line <= end_line:
            candidates.append(node)
    if not candidates:
        return None
    call = min(
        candidates,
        key=lambda node: (
            (node.end_lineno or node.lineno) - node.lineno,
            (node.end_col_offset or node.col_offset) - node.col_offset,
        ),
    )
    return _dotted_name(call.func)


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted_name(node.value)
        return f"{owner}.{node.attr}" if owner is not None else node.attr
    return None


def _diagnostic_from_json(
    item: dict[object, object],
) -> StaticPreflightDiagnostic | None:
    severity = item.get("severity")
    message = item.get("message")
    rule = item.get("rule")
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
    if not isinstance(line, int) or not isinstance(character, int):
        return None
    return StaticPreflightDiagnostic(
        severity=normalized_severity,
        rule=rule,
        message=message[:4_000],
        line=line + 1,
        column=character + 1,
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
