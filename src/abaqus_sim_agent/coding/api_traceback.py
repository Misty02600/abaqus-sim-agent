from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from abaqus_sim_agent.api_catalog import ApiCatalog

_FRAME_PATTERN = re.compile(
    r'^\s*File\s+"(?P<path>[^"]+)",\s+line\s+(?P<line>\d+)',
    re.MULTILINE,
)
_ATTRIBUTE_ERROR_PATTERN = re.compile(
    r"AttributeError:\s*['\"](?P<type>[A-Za-z_][A-Za-z0-9_]*)['\"]"
    r"\s+object has no attribute\s+['\"](?P<attribute>[A-Za-z_][A-Za-z0-9_]*)['\"]"
)
_TYPE_ERROR_PARAMETER_PATTERNS = (
    re.compile(
        r"TypeError:\s*(?P<parameter>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\[[^\]\r\n]+\])*\s*;"
    ),
    re.compile(
        r"unexpected keyword argument\s+['\"](?P<parameter>[A-Za-z_][A-Za-z0-9_]*)['\"]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:keyword error on|required argument)\s+['\"]?(?P<parameter>[A-Za-z_][A-Za-z0-9_]*)['\"]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:TypeError:\s*)?关键字错误\s*[:：]\s*['\"]?"
        r"(?P<parameter>[A-Za-z_][A-Za-z0-9_]*)['\"]?"
    ),
)

_ARRAY_TYPES = {
    "edges": "EdgeArray",
    "faces": "FaceArray",
    "vertices": "VertexArray",
    "cells": "CellArray",
}
_ARRAY_OWNERS = frozenset({"Part", "PartInstance"})
_ABAQUS_EXCEPTION_MARKERS = (
    "AbaqusException:",
    "Feature creation failed",
    "特征创建失败",
    "Invalid variables are specified in an output request",
    "无效的区域自变量",
)
_ODB_RUNTIME_BEHAVIOR_PATTERNS = (
    (
        re.compile(
            r"AttributeError:\s*['\"]FieldOutput['\"] object has no attribute "
            r"['\"]position['\"]"
        ),
        "FieldOutput.getSubset",
    ),
    (
        re.compile(
            r"AttributeError:\s*['\"]FieldValue['\"] object has no attribute "
            r"['\"]componentLabels['\"]"
        ),
        "FieldOutput.getSubset",
    ),
    (
        re.compile(r"Slice for an FieldValue Sequence\s+is not supported"),
        "FieldOutput.getSubset",
    ),
    (
        re.compile(
            r"AttributeError:\s*['\"]OdbMeshNodeArray['\"] object has no "
            r"attribute ['\"](?:label|findAt)['\"]"
        ),
        "openOdb",
    ),
)


@dataclass(frozen=True, slots=True)
class TracebackApiResolution:
    qualified_query: str
    error_parameter: str | None
    call_keywords: tuple[str, ...]
    error_kind: Literal[
        "attribute_error", "type_error", "abaqus_exception", "runtime_failure"
    ]


def resolve_odb_runtime_behavior_query(stderr: str) -> str | None:
    """把已验证的 ODB 容器错误映射到承载行为说明的精确 Card。"""
    return next(
        (
            query
            for pattern, query in _ODB_RUNTIME_BEHAVIOR_PATTERNS
            if pattern.search(stderr)
        ),
        None,
    )


def resolve_traceback_api_query(
    stderr: str,
    *,
    workspace: Path,
    catalog: ApiCatalog,
) -> str | None:
    """从运行时异常中保守解析唯一、精确的 Abaqus API 查询名。

    AttributeError 直接使用异常给出的运行时类型。TypeError 只分析最后一个
    工作区内 traceback frame 对应的完整脚本；任何路径、读取或 AST 不确定性
    都会放弃提示。
    """
    resolution = resolve_traceback_api_context(
        stderr,
        workspace=workspace,
    )
    if resolution is None:
        return None
    return _exact_catalog_query(resolution.qualified_query, catalog)


def resolve_traceback_api_context(
    stderr: str,
    *,
    workspace: Path,
    runtime_failed: bool = False,
    input_fingerprints: Mapping[str, str] | None = None,
) -> TracebackApiResolution | None:
    """解析供运行时修复路由使用的 API 与参数上下文。

    与兼容包装 ``resolve_traceback_api_query`` 不同，本函数保留不存在的
    AttributeError 限定名，并暴露 TypeError 指向的参数和源码中的显式关键
    字。这使调用方能先过滤参数级 runtime note，再决定是否登记查询和消耗
    查询预算。

    Args:
        stderr: 已解码的原始错误输出。
        workspace: 对应执行的工作区。
        runtime_failed: 宿主是否已确认非超时运行失败；允许缺少异常类型的
            CAE输出继续按frame解析，默认关闭以保留纯文本调用方行为。
        input_fingerprints: 本次执行输入的相对路径到SHA-256映射。无异常类型
            的分支必须匹配该快照，并且frame只能定位到唯一调用。
    """
    attribute_query = _attribute_error_query(stderr)
    if attribute_query is not None:
        return TracebackApiResolution(
            qualified_query=attribute_query,
            error_parameter=None,
            call_keywords=(),
            error_kind="attribute_error",
        )
    has_type_error = "TypeError:" in stderr
    has_abaqus_exception = any(marker in stderr for marker in _ABAQUS_EXCEPTION_MARKERS)
    untyped_failure = not has_type_error and not has_abaqus_exception
    if untyped_failure and not (runtime_failed and input_fingerprints):
        return None

    frame = _last_workspace_frame(stderr, workspace)
    if frame is None:
        return None
    script_path, line_number = frame
    try:
        raw = script_path.read_bytes()
        if untyped_failure:
            assert input_fingerprints is not None
            relative = script_path.relative_to(workspace.resolve()).as_posix()
            if input_fingerprints.get(relative) != hashlib.sha256(raw).hexdigest():
                return None
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        tree = ast.parse(source, filename=str(script_path))
    except (OSError, SyntaxError, UnicodeError, LookupError):
        return None

    inferred_types = _infer_assignment_types(tree, before_line=line_number)
    calls = _frame_calls(tree, line_number, require_unique_statement=untyped_failure)
    error_parameter = _type_error_parameter(stderr) if has_type_error else None
    selected = _select_call(calls, error_parameter)
    if selected is None:
        return None
    if (
        untyped_failure
        and isinstance(selected.func, ast.Attribute)
        and _expression_type(selected.func.value, inferred_types) is None
    ):
        return None
    query = _call_query(selected, inferred_types)
    if query is None:
        return None
    return TracebackApiResolution(
        qualified_query=query,
        error_parameter=error_parameter,
        call_keywords=tuple(
            keyword.arg for keyword in selected.keywords if keyword.arg is not None
        ),
        error_kind="type_error"
        if has_type_error
        else "runtime_failure"
        if untyped_failure
        else "abaqus_exception",
    )


def _attribute_error_query(stderr: str) -> str | None:
    matches = tuple(_ATTRIBUTE_ERROR_PATTERN.finditer(stderr))
    if not matches:
        return None
    match = matches[-1]
    return f"{match.group('type')}.{match.group('attribute')}"


def _exact_catalog_query(query: str, catalog: ApiCatalog) -> str | None:
    result = catalog.lookup(query)
    if result.status != "found" or len(result.cards) != 1:
        return None
    return query


def _last_workspace_frame(
    stderr: str,
    workspace: Path,
) -> tuple[Path, int] | None:
    try:
        root = workspace.resolve(strict=True)
    except OSError:
        return None
    if not root.is_dir():
        return None

    selected: tuple[Path, int] | None = None
    for match in _FRAME_PATTERN.finditer(stderr):
        raw_path = Path(match.group("path"))
        candidate = raw_path if raw_path.is_absolute() else root / raw_path
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            if not raw_path.is_absolute():
                return None
            continue
        if not _is_within(resolved, root):
            continue
        if not resolved.is_file() or resolved.suffix.lower() != ".py":
            return None
        selected = (resolved, int(match.group("line")))
    return selected


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _type_error_parameter(stderr: str) -> str | None:
    for pattern in _TYPE_ERROR_PARAMETER_PATTERNS:
        matches = tuple(pattern.finditer(stderr))
        if matches:
            return matches[-1].group("parameter")
    return None


def _infer_assignment_types(
    tree: ast.AST,
    *,
    before_line: int,
) -> dict[str, str]:
    inferred: dict[str, str] = {}
    assignments = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and node.lineno <= before_line
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for assignment in assignments:
        value = assignment.value
        if value is None:
            continue
        value_type = _expression_type(value, inferred)
        if value_type is None:
            continue
        targets = (
            assignment.targets
            if isinstance(assignment, ast.Assign)
            else [assignment.target]
        )
        for target in targets:
            if isinstance(target, ast.Name):
                inferred[target.id] = value_type
    return inferred


def _expression_type(node: ast.AST, inferred: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return inferred.get(node.id)

    if isinstance(node, ast.Subscript):
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "mdb"
            and node.value.attr == "models"
        ):
            return "Model"
        if isinstance(node.value, ast.Attribute):
            owner_type = _expression_type(node.value.value, inferred)
            if owner_type == "Model" and node.value.attr == "parts":
                return "Part"
            if owner_type == "Assembly" and node.value.attr in {
                "instances",
                "allInstances",
            }:
                return "PartInstance"
        return None

    if isinstance(node, ast.Attribute):
        owner_type = _expression_type(node.value, inferred)
        if owner_type == "Model" and node.attr == "rootAssembly":
            return "Assembly"
        if owner_type in _ARRAY_OWNERS:
            return _ARRAY_TYPES.get(node.attr)
        return None

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        owner_type = _expression_type(node.func.value, inferred)
        if owner_type == "Model" and node.func.attr == "Part":
            return "Part"
        if owner_type == "Assembly" and node.func.attr == "Instance":
            return "PartInstance"
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "mdb"
            and node.func.attr == "Model"
        ):
            return "Model"
    return None


def _frame_calls(
    tree: ast.AST, line_number: int, *, require_unique_statement: bool = False
) -> tuple[ast.Call, ...]:
    statements = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
        and node.lineno <= line_number <= (node.end_lineno or node.lineno)
    ]
    if not statements:
        return ()
    if require_unique_statement:
        shortest = min(
            (node.end_lineno or node.lineno) - node.lineno for node in statements
        )
        statements = [
            node
            for node in statements
            if (node.end_lineno or node.lineno) - node.lineno == shortest
        ]
        if len(statements) != 1:
            return ()
    statement = min(
        statements,
        key=lambda node: (
            (node.end_lineno or node.lineno) - node.lineno,
            -node.col_offset,
        ),
    )
    return tuple(
        sorted(
            (
                node
                for node in ast.walk(statement)
                if isinstance(node, ast.Call)
                and (
                    not require_unique_statement
                    or node.lineno <= line_number <= (node.end_lineno or node.lineno)
                )
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
    )


def _select_call(
    calls: tuple[ast.Call, ...],
    parameter: str | None,
) -> ast.Call | None:
    if parameter is not None:
        matching = tuple(
            call
            for call in calls
            if any(keyword.arg == parameter for keyword in call.keywords)
        )
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            return None
    return calls[0] if len(calls) == 1 else None


def _call_query(call: ast.Call, inferred: dict[str, str]) -> str | None:
    function = call.func
    if isinstance(function, ast.Attribute):
        receiver_type = _expression_type(function.value, inferred)
        if receiver_type is not None:
            return f"{receiver_type}.{function.attr}"
        if function.attr[:1].isupper():
            return f"{function.attr}.__init__"
        return None
    if isinstance(function, ast.Name) and function.id[:1].isupper():
        return f"{function.id}.__init__"
    return None
