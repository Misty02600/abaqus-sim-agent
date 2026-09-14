from __future__ import annotations

import ast
import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai.capabilities import AbstractCapability, WrapToolExecuteHandler
from pydantic_ai.exceptions import SkipToolExecution
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai_harness.guardrails import GuardrailResult, ToolCallInfo

from abaqus_sim_agent.coding.persistence import write_text_atomic

if TYPE_CHECKING:
    from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies

EDIT_SCOPE_POLICY_VERSION = "single-reference-edit-v1"
_READ_TOOLS = frozenset(
    {
        "read_file",
        "list_directory",
        "search_files",
        "find_files",
        "file_info",
        "lookup_abaqus_api",
        "search_abaqus_docs",
        "load_capability",
    }
)
_DECISIONS = frozenset(
    {
        "final_result_NeedsInputDecision",
        "final_result_ReadyForVerificationDecision",
        "final_result_FailedDecision",
    }
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _lf(source: str) -> str:
    return source.replace("\r\n", "\n").replace("\r", "\n")


def _expression_span(source: str, start: int, end: int) -> ast.expr:
    tree = ast.parse(source, feature_version=(3, 10))
    # Python AST line numbers count LF, not every Unicode splitlines separator.
    lines = source.split("\n")

    def offset(line: int, column: int) -> int:
        return sum(len(value) + 1 for value in lines[: line - 1]) + len(
            lines[line - 1].encode("utf-8")[:column].decode("utf-8")
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.expr) and node.end_lineno is not None:
            if (
                offset(node.lineno, node.col_offset),
                offset(node.end_lineno, node.end_col_offset or 0),
            ) == (start, end):
                return node
    raise ValueError("edit span must select exactly one complete Python expression")


def _is_reference(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Attribute):
        return _is_reference(node.value)
    if isinstance(node, ast.Subscript):
        return (
            _is_reference(node.value)
            and isinstance(node.slice, ast.Constant)
            and type(node.slice.value) in (str, int)
        )
    return False


class ReferenceEditScope(BaseModel):
    """由可信调用方冻结一个文件的表达式编辑范围，不包含正确答案。

    Args:
        path: 工作区内现有 Python 文件的规范 ASCII 相对路径。
        baseline_source: 初始文件的 UTF-8 原文，包含原始换行。
        baseline_sha256: 初始文件原始 bytes 的 SHA-256。
        start: LF 规范化原文中的零基字符偏移，包含此位置。
        end: LF 规范化原文中的零基字符偏移，不包含此位置。

    Note:
        只允许 Name/Attribute/常量下标组成的引用表达式；不证明引用存在、
        API 正确或属性访问无副作用。范围外文本（含注释）保持不变，
        Harness 的平台换行转换除外。合同不能由模型或检索结果扩权。
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    policy_version: Literal["single-reference-edit-v1"] = EDIT_SCOPE_POLICY_VERSION
    path: str
    baseline_source: str = Field(min_length=1)
    baseline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_scope(self) -> ReferenceEditScope:
        parts = self.path.split("/")
        if (
            not self.path.isascii()
            or any(part in ("", ".", "..") for part in parts)
            or any(char in self.path for char in "\\:\x00")
            or PurePosixPath(self.path).suffix != ".py"
        ):
            raise ValueError("scope path must be a canonical relative ASCII .py path")
        if _sha256(self.baseline_source.encode("utf-8")) != self.baseline_sha256:
            raise ValueError("baseline SHA-256 does not match baseline_source bytes")
        source = _lf(self.baseline_source)
        if not self.start < self.end <= len(source):
            raise ValueError("invalid expression span")
        try:
            _expression_span(source, self.start, self.end)
        except SyntaxError as exc:
            raise ValueError(
                "scope baseline must have valid Python 3.10 syntax"
            ) from exc
        return self

    def validate_candidate(self, candidate: str) -> None:
        source = _lf(self.baseline_source)
        prefix, suffix = source[: self.start], source[self.end :]
        if (
            not candidate.startswith(prefix)
            or not candidate.endswith(suffix)
            or len(candidate) < len(prefix) + len(suffix)
        ):
            raise ValueError("candidate changes text outside the authorized expression")
        end = len(candidate) - len(suffix)
        node = _expression_span(candidate, self.start, end)
        if not _is_reference(node):
            raise ValueError(
                "replacement must be a reference without calls or computed indices"
            )


class _EditCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    policy_version: Literal["single-reference-edit-v1"] = EDIT_SCOPE_POLICY_VERSION
    contract_sha256: str
    current_sha256: str
    pending: bool = False


@dataclass(slots=True)
class ReferenceEditGate:
    workspace: Path
    state_dir: Path
    scope: ReferenceEditScope
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _checkpoint: _EditCheckpoint | None = field(default=None, init=False)

    def _target(self, raw_path: str) -> Path:
        # Reject aliases, reparse points and hardlinks rather than trusting resolve alone.
        if raw_path != self.scope.path:
            raise ValueError(
                "only the exact authorized relative file path may be edited"
            )
        target = self.workspace
        for part in self.scope.path.split("/"):
            target = target / part
            if target.is_symlink() or target.is_junction():
                raise ValueError("edit target must not traverse a link or junction")
        if not target.resolve(strict=True).is_relative_to(
            self.workspace.resolve(strict=True)
        ):
            raise ValueError("edit target escapes workspace")
        if not target.is_file() or target.stat().st_nlink != 1:
            raise ValueError("edit target must be an existing regular single-link file")
        return target

    def prepare(self, *, resume: bool) -> None:
        """在 service 的 OS run lock 内加载或建立检查点；未决写入不自动恢复。"""
        target = self._target(self.scope.path)
        digest = _sha256(target.read_bytes())
        contract = _sha256(self.scope.model_dump_json().encode("utf-8"))
        path = self.state_dir / "edit_scope_state.json"
        if resume:
            checkpoint = _EditCheckpoint.model_validate_json(path.read_bytes())
            if checkpoint.contract_sha256 != contract or checkpoint.pending:
                raise ValueError("edit scope contract changed or a write is unresolved")
            if checkpoint.current_sha256 != digest:
                raise ValueError(
                    "edit scope baseline drift: current file changed outside this run"
                )
        else:
            if path.exists():
                raise ValueError("edit scope state already exists")
            if digest != self.scope.baseline_sha256:
                raise ValueError("edit scope baseline drift before first write")
            checkpoint = _EditCheckpoint(
                contract_sha256=contract, current_sha256=digest
            )
        self._save(checkpoint)

    def _save(self, checkpoint: _EditCheckpoint) -> None:
        write_text_atomic(
            self.state_dir / "edit_scope_state.json", checkpoint.model_dump_json()
        )
        self._checkpoint = checkpoint

    def check(self, call: ToolCallInfo) -> tuple[GuardrailResult, str | None]:
        try:
            checkpoint = self._checkpoint
            if checkpoint is None or checkpoint.pending:
                raise ValueError(
                    "edit scope checkpoint unavailable or write unresolved"
                )
            raw_path = call.args.get("path")
            if not isinstance(raw_path, str):
                raise ValueError("missing edit path")
            target = self._target(raw_path)
            raw = target.read_bytes()
            if _sha256(raw) != checkpoint.current_sha256:
                raise ValueError(
                    "edit scope baseline drift; do not overwrite external changes"
                )
            current = _lf(raw.decode("utf-8"))
            expected = call.args.get("expected_hash")
            if (
                expected is not None
                and expected != _sha256(current.encode("utf-8"))[:12]
            ):
                raise ValueError("stale expected_hash")
            if call.name == "write_file":
                candidate = call.args.get("content")
            elif call.name == "edit_file":
                old, new = call.args.get("old_text"), call.args.get("new_text")
                if (
                    not isinstance(old, str)
                    or not isinstance(new, str)
                    or current.count(old) != 1
                ):
                    raise ValueError("edit must match exactly once")
                candidate = current.replace(old, new, 1)
            else:
                raise ValueError("unrecognized write entrypoint")
            if not isinstance(candidate, str) or "\r" in candidate:
                raise ValueError("candidate must use LF text")
            self.scope.validate_candidate(candidate)
        except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
            payload = json.dumps(
                {
                    "error_type": "EditScopeDenied",
                    "policy_version": EDIT_SCOPE_POLICY_VERSION,
                    "edit_applied": False,
                    "reason": str(exc),
                }
            )
            return GuardrailResult.block(payload), None
        return GuardrailResult.allow(), candidate

    async def execute(self, call: ToolCallInfo, handler: WrapToolExecuteHandler) -> Any:
        async with self.lock:
            verdict, candidate = self.check(call)
            if verdict.action != "allow":
                raise SkipToolExecution(verdict.message)
            assert self._checkpoint is not None
            assert candidate is not None
            settled = self._checkpoint
            self._save(settled.model_copy(update={"pending": True}))
            try:
                result = await handler(dict(call.args))
            except BaseException:
                # A failed tool may have already touched disk. Only unchanged bytes settle safely.
                try:
                    if (
                        _sha256(self._target(self.scope.path).read_bytes())
                        == settled.current_sha256
                    ):
                        self._save(settled)
                except (OSError, ValueError):
                    pass  # Preserve the original failure and leave the write unresolved.
                raise
            raw = self._target(self.scope.path).read_bytes()
            if _lf(raw.decode("utf-8")) != candidate:
                raise RuntimeError(
                    "file tool result differs from the checked candidate"
                )
            self._save(settled.model_copy(update={"current_sha256": _sha256(raw)}))
            return result


class ReferenceEditCapability(AbstractCapability["CodingAgentDependencies"]):
    """用原生执行 hook 包住候选检查、文件工具与检查点更新。"""

    async def wrap_tool_execute(
        self,
        ctx: RunContext[CodingAgentDependencies],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        del tool_def
        if call.tool_name in _READ_TOOLS | _DECISIONS:
            return await handler(args)
        if call.tool_name not in {"write_file", "edit_file"}:
            raise SkipToolExecution(
                "Pure edit scope prohibits this tool and all execution."
            )
        gate = ctx.deps.edit_scope_gate
        if gate is None:
            raise SkipToolExecution("Required edit scope gate is unavailable.")
        return await gate.execute(
            ToolCallInfo(
                name=call.tool_name, args=args, tool_call_id=call.tool_call_id
            ),
            handler,
        )
