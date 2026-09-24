from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic_ai_harness.filesystem import Replacement
from pydantic_ai_harness.guardrails import GuardrailResult, ToolCallInfo

from abaqus_sim_agent.coding.contracts import RuntimeScriptPolicy

PYTHON_SYNTAX_GATE_VERSION = "python-3.10-production-syntax-v1"


@dataclass(frozen=True, slots=True)
class PythonSyntaxDiagnostic:
    message: str
    line: int
    column: int
    end_line: int | None = None
    end_column: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSyntaxCheck:
    status: Literal["clean", "denied", "unavailable"]
    script_path: str
    script_sha256: str | None
    cache_hit: bool
    diagnostic: PythonSyntaxDiagnostic | None = None
    error: str | None = None


@dataclass(slots=True)
class ProductionPythonSyntaxGate:
    """在文件写入和 vendor runtime 前强制执行 Abaqus Python 3.10 语法门。"""

    workspace: Path
    state_dir: Path
    runtime_script_policy: RuntimeScriptPolicy
    checks: int = 0
    edit_checks: int = 0
    runtime_checks: int = 0
    denials: int = 0
    _validated_hashes: set[tuple[str, str]] = field(default_factory=set, init=False)
    _targets_by_resolved_path: dict[str, str] = field(
        default_factory=dict,
        init=False,
    )

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve(strict=True)
        self.state_dir = self.state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        policy = self.runtime_script_policy
        if (
            policy.allowed_cae_scripts is None
            or policy.allowed_abaqus_python_scripts is None
        ):
            raise ValueError(
                "enforced Python syntax gate requires explicit CAE and Abaqus "
                "Python runtime allowlists"
            )
        targets = (
            *policy.allowed_cae_scripts,
            *policy.allowed_abaqus_python_scripts,
        )
        if not targets:
            raise ValueError("enforced Python syntax gate requires a production script")
        for relative in targets:
            resolved = (self.workspace / relative).resolve()
            key = str(resolved).casefold()
            existing = self._targets_by_resolved_path.get(key)
            if existing is not None and existing.casefold() != relative.casefold():
                raise ValueError("production syntax targets resolve to the same path")
            self._targets_by_resolved_path[key] = relative

    def guard(self, call: ToolCallInfo) -> GuardrailResult:
        """检查一次 Harness 文件写操作的完整候选内容。

        Args:
            call: 已通过工具 schema 校验、但尚未执行的文件工具调用。

        Returns:
            候选语法有效或调用不属于生产脚本时允许执行；语法无效时阻断，
            原文件保持不变并把结构化诊断返回模型。
        """
        if call.name not in {"write_file", "edit_file"}:
            return GuardrailResult.allow()
        raw_path = call.args.get("path")
        if not isinstance(raw_path, str):
            return GuardrailResult.allow()
        relative = self._production_target(raw_path)
        if relative is None:
            return GuardrailResult.allow()
        candidate = self._candidate_content(call, relative)
        if candidate is None:
            return GuardrailResult.allow()
        self.checks += 1
        self.edit_checks += 1
        digest = _sha256_text(candidate)
        diagnostic = _python_310_diagnostic(candidate, relative)
        if diagnostic is None:
            self._validated_hashes.add((relative.casefold(), digest))
            self._record(
                stage="edit",
                operation=call.name,
                relative=relative,
                digest=digest,
                status="clean",
                cache_hit=False,
                diagnostic=None,
                error=None,
            )
            return GuardrailResult.allow()
        self.denials += 1
        self._record(
            stage="edit",
            operation=call.name,
            relative=relative,
            digest=digest,
            status="denied",
            cache_hit=False,
            diagnostic=diagnostic,
            error=None,
        )
        return GuardrailResult.block(
            json.dumps(
                {
                    "error_type": "PythonSyntaxEditDenied",
                    "script_path": relative,
                    "python_version": "3.10",
                    "syntax_gate_version": PYTHON_SYNTAX_GATE_VERSION,
                    "diagnostic": _diagnostic_payload(diagnostic),
                    "edit_applied": False,
                    "budget_consumed": False,
                    "guidance": (
                        "Submit a syntactically valid complete candidate for the "
                        "same production file."
                    ),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )

    def check_runtime(self, script_path: str) -> RuntimeSyntaxCheck:
        """复核当前生产脚本 hash，阻止外部修改绕过编辑门。

        Args:
            script_path: 模型请求进入 CAE 或 Abaqus Python 的相对脚本路径。

        Returns:
            绑定当前文件 hash 的 clean、denied 或 unavailable 结果。
        """
        relative = self._production_target(script_path)
        if relative is None:
            return RuntimeSyntaxCheck(
                status="unavailable",
                script_path=script_path.replace("\\", "/"),
                script_sha256=None,
                cache_hit=False,
                error="runtime script is outside the production syntax targets",
            )
        raw_target = self.workspace / relative
        resolved = raw_target.resolve()
        try:
            if not resolved.is_file() or raw_target.is_symlink():
                raise FileNotFoundError("production script is missing or is a symlink")
            source = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self.checks += 1
            self.runtime_checks += 1
            error = f"{type(exc).__name__}: {exc}"
            self._record(
                stage="runtime",
                operation="syntax_recheck",
                relative=relative,
                digest=None,
                status="unavailable",
                cache_hit=False,
                diagnostic=None,
                error=error,
            )
            return RuntimeSyntaxCheck(
                status="unavailable",
                script_path=relative,
                script_sha256=None,
                cache_hit=False,
                error=error,
            )
        digest = _sha256_text(source)
        key = (relative.casefold(), digest)
        cache_hit = key in self._validated_hashes
        diagnostic = None if cache_hit else _python_310_diagnostic(source, relative)
        self.checks += 1
        self.runtime_checks += 1
        if diagnostic is not None:
            self.denials += 1
            self._record(
                stage="runtime",
                operation="syntax_recheck",
                relative=relative,
                digest=digest,
                status="denied",
                cache_hit=False,
                diagnostic=diagnostic,
                error=None,
            )
            return RuntimeSyntaxCheck(
                status="denied",
                script_path=relative,
                script_sha256=digest,
                cache_hit=False,
                diagnostic=diagnostic,
            )
        self._validated_hashes.add(key)
        self._record(
            stage="runtime",
            operation="syntax_recheck",
            relative=relative,
            digest=digest,
            status="clean",
            cache_hit=cache_hit,
            diagnostic=None,
            error=None,
        )
        return RuntimeSyntaxCheck(
            status="clean",
            script_path=relative,
            script_sha256=digest,
            cache_hit=cache_hit,
        )

    def _production_target(self, raw_path: str) -> str | None:
        try:
            resolved = (self.workspace / raw_path).resolve()
        except (OSError, RuntimeError):
            return None
        if not resolved.is_relative_to(self.workspace):
            return None
        return self._targets_by_resolved_path.get(str(resolved).casefold())

    def _candidate_content(self, call: ToolCallInfo, relative: str) -> str | None:
        if call.name == "write_file":
            content = call.args.get("content")
            return content if isinstance(content, str) else None
        resolved = (self.workspace / relative).resolve()
        try:
            with resolved.open(encoding="utf-8", newline="") as stream:
                current = stream.read()
        except (OSError, UnicodeError):
            return None
        old_text = call.args.get("old_text")
        new_text = call.args.get("new_text")
        expected_hash = call.args.get("expected_hash")
        if expected_hash is not None and expected_hash != _short_hash(current):
            return None
        replacements = call.args.get("replacements")
        if replacements is None:
            replacements = [{"old_text": old_text, "new_text": new_text}]
        elif old_text is not None or new_text is not None:
            return None
        if not isinstance(replacements, list) or not replacements:
            return None
        for replacement in replacements:
            if isinstance(replacement, Replacement):
                old, new = replacement.old_text, replacement.new_text
            elif isinstance(replacement, dict):
                old, new = replacement.get("old_text"), replacement.get("new_text")
            else:
                return None
            if (
                not isinstance(old, str)
                or not old
                or not isinstance(new, str)
                or current.count(old) != 1
            ):
                return None  # Harness rejects invalid replacement sets before writing.
            current = current.replace(old, new, 1)
        return current

    def _record(
        self,
        *,
        stage: Literal["edit", "runtime"],
        operation: str,
        relative: str,
        digest: str | None,
        status: Literal["clean", "denied", "unavailable"],
        cache_hit: bool,
        diagnostic: PythonSyntaxDiagnostic | None,
        error: str | None,
    ) -> None:
        payload = {
            "schema_version": "1",
            "syntax_gate_version": PYTHON_SYNTAX_GATE_VERSION,
            "python_version": "3.10",
            "stage": stage,
            "operation": operation,
            "script_path": relative,
            "script_sha256": digest,
            "status": status,
            "cache_hit": cache_hit,
            "diagnostic": (
                _diagnostic_payload(diagnostic) if diagnostic is not None else None
            ),
            "error": error,
        }
        with (self.state_dir / "syntax_checks.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")


def _python_310_diagnostic(
    source: str,
    filename: str,
) -> PythonSyntaxDiagnostic | None:
    try:
        ast.parse(source, filename=filename, mode="exec", feature_version=(3, 10))
    except SyntaxError as exc:
        return PythonSyntaxDiagnostic(
            message=exc.msg,
            line=max(1, exc.lineno or 1),
            column=max(1, exc.offset or 1),
            end_line=exc.end_lineno,
            end_column=exc.end_offset,
        )
    except (TypeError, ValueError) as exc:
        return PythonSyntaxDiagnostic(message=str(exc), line=1, column=1)
    return None


def _diagnostic_payload(diagnostic: PythonSyntaxDiagnostic) -> dict[str, object]:
    return {
        "message": diagnostic.message,
        "line": diagnostic.line,
        "column": diagnostic.column,
        "end_line": diagnostic.end_line,
        "end_column": diagnostic.end_column,
    }


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _short_hash(content: str) -> str:
    return _sha256_text(content)[:12]
