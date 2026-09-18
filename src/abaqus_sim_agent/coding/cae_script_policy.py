from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BlockedSolverMethod = Literal["submit", "waitForCompletion"]
_BLOCKED_SOLVER_METHODS: frozenset[BlockedSolverMethod] = frozenset(
    {"submit", "waitForCompletion"}
)


@dataclass(frozen=True, slots=True)
class CaeSolverLifecycleCall:
    method: BlockedSolverMethod
    line: int
    column: int


def find_cae_solver_lifecycle_calls(
    workspace: Path,
    script_path: str,
) -> tuple[CaeSolverLifecycleCall, ...]:
    """查找 CAE 建模脚本中直接启动或等待 Solver 的方法调用。

    Args:
        workspace: CAE 脚本必须归属的工作区。
        script_path: 模型请求执行的脚本路径。

    Returns:
        AST 中直接出现的 ``.submit(...)`` 和 ``.waitForCompletion(...)``
        调用位置。无法读取或解析的脚本交给既有 runtime 错误路径处理。

    Note:
        该检查面向常见误写，不尝试识别 ``getattr``、``exec`` 或运行时
        重绑定等动态绕行，也不构成 Python 沙箱。
    """
    resolved = _resolve_workspace_script(workspace, script_path)
    if resolved is None:
        return ()
    try:
        with tokenize.open(resolved) as stream:
            source = stream.read()
        tree = ast.parse(
            source,
            filename=script_path,
            mode="exec",
            feature_version=(3, 10),
        )
    except (OSError, SyntaxError, UnicodeError, ValueError):
        return ()

    visitor = _CaeSolverLifecycleVisitor()
    visitor.visit(tree)
    return tuple(visitor.calls)


class _CaeSolverLifecycleVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[CaeSolverLifecycleCall] = []

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Attribute):
            method = function.attr
            if method in _BLOCKED_SOLVER_METHODS:
                self.calls.append(
                    CaeSolverLifecycleCall(
                        method=method,
                        line=node.lineno,
                        column=node.col_offset + 1,
                    )
                )
        self.generic_visit(node)


def _resolve_workspace_script(workspace: Path, script_path: str) -> Path | None:
    root = workspace.resolve()
    unresolved = Path(script_path)
    candidate = unresolved if unresolved.is_absolute() else root / unresolved
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved
