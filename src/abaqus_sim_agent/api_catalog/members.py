from __future__ import annotations

import ast
import hashlib
import zipfile
from collections.abc import Sequence
from email.parser import BytesParser
from pathlib import Path

from .abqpy import (
    AbqpyExtractionError,
    _ClassDefinition,
    _parse_distribution,
    _read_source_files,
)
from .models import ApiMemberCard, ApiSourceRef


def load_abqpy_member_cards(
    wheel: str | Path,
    *,
    version: str,
    release: str,
    runtime_release: str,
    owners: Sequence[str],
) -> tuple[ApiMemberCard, ...]:
    """从已核对版本的 wheel 提取显式 owner 的静态、公开注解成员。

    Note:
        不导入或执行源码。按静态 C3 顺序处理继承和遮蔽；未知基类、歧义 owner、
        不一致 MRO 或条件类体会拒绝构建，不猜测动态属性。注解及注释是 stub
        来源证据，不是 Abaqus 运行验证，也不把类体占位值作为运行默认值。

    Args:
        wheel: 包含唯一 abqpy METADATA 的 wheel 文件。
        version: 必须与 METADATA 精确相同的分发版本。
        release: 必须与分发年份相同的 Abaqus release。
        runtime_release: Catalog 目标 release，仅用于标记匹配关系。
        owners: 非空、显式的类名或完整类限定名；不扩展到未请求的子类。

    Raises:
        AbqpyExtractionError: 分发身份、版本或静态继承关系无法确定。
    """
    path = Path(wheel).resolve(strict=True)
    if not owners or path.suffix != ".whl" or not zipfile.is_zipfile(path):
        raise AbqpyExtractionError("members require a wheel and explicit owners")
    digest_before = hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as archive:
        metadata = [
            item
            for item in archive.infolist()
            if item.filename.endswith(".dist-info/METADATA")
        ]
        if len(metadata) != 1 or metadata[0].file_size > 1024 * 1024:
            raise AbqpyExtractionError("wheel requires one bounded METADATA entry")
        info = BytesParser().parsebytes(archive.read(metadata[0]))
        if info.get_all("Name") != ["abqpy"] or info.get_all("Version") != [version]:
            raise AbqpyExtractionError("abqpy wheel identity/version mismatch")
    if not version[:4].isdigit() or release != version[:4]:
        raise AbqpyExtractionError("member release must match the wheel version year")
    classes = _parse_distribution(_read_source_files(path)).classes
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest_before:
        raise AbqpyExtractionError("abqpy wheel changed during member extraction")
    selected = tuple(dict.fromkeys(_owner_name(name, classes) for name in owners))
    mros: dict[str, tuple[str, ...]] = {"object": ("object",)}
    grouped: dict[tuple[str, str], list[str]] = {}
    declarations: dict[tuple[str, str], ast.AnnAssign] = {}
    for owner in selected:
        seen: set[str] = set()
        for ancestor in _linearize(owner, classes, mros, ()):
            if ancestor == "object":
                continue
            for name, statement in _bindings(classes[ancestor]).items():
                if name in seen:
                    continue
                seen.add(name)
                if name.startswith("_") or not isinstance(statement, ast.AnnAssign):
                    continue
                key = (ancestor, name)
                declarations[key] = statement
                grouped.setdefault(key, []).append(owner)
    result: list[ApiMemberCard] = []
    for (ancestor, name), receivers in sorted(grouped.items()):
        declaration = declarations[(ancestor, name)]
        definition = classes[ancestor]
        public_names = tuple(
            dict.fromkeys(
                f"{classes[receiver].class_name}.{name}" for receiver in receivers
            )
        )
        result.append(
            ApiMemberCard(
                card_id=f"api-member:{version}:{ancestor}.{name}",
                canonical_name=public_names[0],
                declaring_name=f"{definition.class_name}.{name}",
                member_name=name,
                public_names=public_names,
                aliases=tuple(
                    dict.fromkeys(
                        (
                            f"{ancestor}.{name}",
                            *(f"{receiver}.{name}" for receiver in receivers),
                        )
                    )
                ),
                annotation=ast.unparse(declaration.annotation),
                description=_member_comment(definition, declaration.lineno),
                source=ApiSourceRef(
                    kind="abqpy",
                    release=release,
                    version=version,
                    source_path=definition.source_path,
                ),
                source_line=declaration.lineno,
                source_end_line=declaration.end_lineno or declaration.lineno,
                source_sha256=definition.source_file.sha256,
                distribution_sha256=digest_before,
                release_alignment="matched"
                if runtime_release == release
                else "mismatch",
            )
        )
    return tuple(result)


def _owner_name(name: str, classes: dict[str, _ClassDefinition]) -> str:
    if name in classes:
        return name
    matches = [key for key, value in classes.items() if value.class_name == name]
    if len(matches) != 1:
        raise AbqpyExtractionError(f"unknown or ambiguous member owner: {name}")
    return matches[0]


def _base_name(
    raw: str, definition: _ClassDefinition, classes: dict[str, _ClassDefinition]
) -> str:
    root, separator, tail = raw.partition(".")
    if root in definition.imports:
        candidate = definition.imports[root] + (f".{tail}" if separator else "")
    else:
        candidate = f"{definition.module_name}.{raw}"
        if raw == "object" and candidate not in classes:
            return "object"
    if candidate not in classes:
        raise AbqpyExtractionError(
            f"unresolved member base: {definition.qualified_name}: {raw}"
        )
    return candidate


def _linearize(
    name: str,
    classes: dict[str, _ClassDefinition],
    cache: dict[str, tuple[str, ...]],
    stack: tuple[str, ...],
) -> tuple[str, ...]:
    if name in cache:
        return cache[name]
    if name in stack:
        raise AbqpyExtractionError(f"cyclic member inheritance: {name}")
    definition = classes[name]
    bases = tuple(
        _base_name(raw, definition, classes) for raw in definition.raw_bases
    ) or ("object",)
    if len(bases) != len(set(bases)):
        raise AbqpyExtractionError(f"duplicate member bases: {name}")
    sequences = [
        list(_linearize(base, classes, cache, (*stack, name))) for base in bases
    ]
    sequences.append(list(bases))
    merged: list[str] = [name]
    while sequences := [sequence for sequence in sequences if sequence]:
        head = next(
            (
                sequence[0]
                for sequence in sequences
                if all(sequence[0] not in other[1:] for other in sequences)
            ),
            None,
        )
        if head is None:
            raise AbqpyExtractionError(f"inconsistent member MRO: {name}")
        merged.append(head)
        for sequence in sequences:
            if sequence[0] == head:
                sequence.pop(0)
    cache[name] = tuple(merged)
    return cache[name]


def _bindings(definition: _ClassDefinition) -> dict[str, ast.stmt]:
    bindings: dict[str, ast.stmt] = {}
    for statement in definition.node.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            bindings[statement.target.id] = statement
        elif isinstance(
            statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            bindings[statement.name] = statement
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                for node in ast.walk(target):
                    if isinstance(node, ast.Name):
                        bindings[node.id] = statement
        elif not isinstance(statement, (ast.Expr, ast.Pass)):
            raise AbqpyExtractionError(
                f"unsupported static member class body: {definition.qualified_name}"
            )
    return bindings


def _member_comment(definition: _ClassDefinition, line: int) -> str:
    lines = definition.source_file.source.splitlines()
    collected: list[str] = []
    for text in reversed(lines[: line - 1]):
        stripped = text.strip()
        if not stripped.startswith("#:"):
            break
        collected.append(stripped[2:].strip())
    return " ".join(reversed(collected))
