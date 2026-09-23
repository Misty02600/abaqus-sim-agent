from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

PYTHON_SYNTAX_GATE_VERSION = "python-3.10-runtime-syntax-v1"


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
class RuntimePythonSyntaxGate:
    """只在执行前解析工作区脚本；不执行代码，也不限制文件编辑。"""

    workspace: Path
    state_dir: Path
    checks: int = 0
    runtime_checks: int = 0
    denials: int = 0
    _validated_hashes: set[tuple[str, str]] = field(default_factory=set, init=False)

    def check_runtime(self, script_path: str) -> RuntimeSyntaxCheck:
        """解析本次执行的源码，按原始文件字节的 hash 缓存成功结果并记录日志。

        Args:
            script_path: 待执行的工作区 Python 文件路径。

        Returns:
            语法解析成功、语法拒绝或无法检查的结果；包含实际文件 hash 和错误定位。

        Note:
            调用方先检查权限和文件 allowlist。本方法不执行源码，不保证 API 或物理正确性。
        """
        root = self.workspace.resolve()
        self.checks += 1
        self.runtime_checks += 1
        relative = script_path.replace("\\", "/")
        digest = None
        try:
            target = (root / script_path).resolve(strict=True)
            if not target.is_relative_to(root) or target.suffix.casefold() != ".py":
                raise ValueError("runtime script must be a workspace Python file")
            relative = target.relative_to(root).as_posix()
            raw = target.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            key = (relative.casefold(), digest)
            if key in self._validated_hashes:
                result = RuntimeSyntaxCheck("clean", relative, digest, True)
            else:
                encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                source = raw.decode(encoding)
                diagnostic = _python_310_diagnostic(source, relative)
                if diagnostic is None:
                    self._validated_hashes.add(key)
                else:
                    self.denials += 1
                result = RuntimeSyntaxCheck(
                    "clean" if diagnostic is None else "denied",
                    relative,
                    digest,
                    False,
                    diagnostic,
                )
        except (OSError, UnicodeError, SyntaxError, ValueError, RuntimeError) as exc:
            result = RuntimeSyntaxCheck(
                "unavailable",
                relative,
                digest,
                False,
                error=f"{type(exc).__name__}: {exc}",
            )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "syntax_checks.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(
                json.dumps(
                    {
                        "stage": "runtime",
                        "syntax_gate_version": PYTHON_SYNTAX_GATE_VERSION,
                        **asdict(result),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
                + "\n"
            )
        return result


def _python_310_diagnostic(source: str, filename: str) -> PythonSyntaxDiagnostic | None:
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
