from __future__ import annotations

import ast
import hashlib
import io
import tokenize
import zipfile
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from abaqus_sim_agent.api_catalog.models import (
    AbqpyApiRecord,
    ApiClassNode,
    ApiParameter,
    ApiParameterKind,
    ApiSignature,
    ApiSourceRef,
)

from .docstrings import extract_docstring

_MAX_SOURCE_BYTES = 8 * 1024 * 1024


class AbqpyExtractionError(ValueError):
    """表示 abqpy 源分发无法被安全、确定地解析。"""


@dataclass(frozen=True, slots=True)
class _SourceFile:
    module_name: str
    source_path: str
    source: str
    is_package: bool
    sha256: str


@dataclass(frozen=True, slots=True)
class _FunctionNode:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    module_name: str
    source_path: str
    class_qualified_name: str | None
    class_name: str | None


@dataclass(frozen=True, slots=True)
class _ClassDefinition:
    qualified_name: str
    class_name: str
    module_name: str
    source_path: str
    raw_bases: tuple[str, ...]
    imports: dict[str, str]
    methods: dict[str, tuple[_FunctionNode, ...]]
    node: ast.ClassDef
    source_file: _SourceFile


@dataclass(frozen=True, slots=True)
class _ParsedDistribution:
    classes: dict[str, _ClassDefinition]
    commands: tuple[_FunctionNode, ...]


def load_abqpy_records(
    source_root: str | Path,
    *,
    version: str,
    release: str,
) -> list[AbqpyApiRecord]:
    """从 abqpy 源目录或归档静态提取 API 记录。

    解析过程只读取 Python 源文本并构建 AST；不会导入 abqpy、执行模块顶层
    代码或解压归档。`version` 与 `release` 由调用方基于已固定的分发来源提供，
    避免为了发现版本而导入包。

    Args:
        source_root: abqpy wheel/ZIP、仓库根目录、`src` 目录或 `abaqus` 包目录。
        version: abqpy 分发版本，例如 `2024.10`。
        release: 该类型来源所描述的 Abaqus release，例如 `2024`。

    Returns:
        按稳定 `record_id` 排序的原始 abqpy API 记录。

    Raises:
        AbqpyExtractionError: 来源不存在、不是受支持的目录/归档、源码过大、
            编码或语法无效，或包含无法消除歧义的重复类定义。
    """
    return list(
        iter_abqpy_records(
            source_root,
            version=version,
            release=release,
        )
    )


def iter_abqpy_records(
    source_root: str | Path,
    *,
    version: str,
    release: str,
) -> Iterator[AbqpyApiRecord]:
    """惰性返回由一次完整静态 class graph 构建得到的 API 记录。"""
    normalized_version = _required_text(version, field_name="version")
    normalized_release = _required_text(release, field_name="release")
    source_files = _read_source_files(Path(source_root))
    source_hashes = {source.source_path: source.sha256 for source in source_files}
    distribution = _parse_distribution(source_files)
    class_nodes, children = _build_class_graph(distribution.classes)

    records: list[AbqpyApiRecord] = []
    for class_definition in distribution.classes.values():
        declaring_node = class_nodes[class_definition.qualified_name]
        for method_name, functions in class_definition.methods.items():
            entity_kind = "constructor" if method_name == "__init__" else "method"
            receivers = _method_receivers(
                class_definition.qualified_name,
                method_name,
                distribution.classes,
                children,
                expand=entity_kind == "method",
            )
            receiver_nodes = tuple(class_nodes[name] for name in receivers)
            public_names = tuple(
                dict.fromkeys(
                    f"{node.class_name}.{method_name}" for node in receiver_nodes
                )
            )
            aliases = tuple(
                f"{node.qualified_name}.{method_name}" for node in receiver_nodes
            )
            callable_qualified_name = f"{class_definition.qualified_name}.{method_name}"
            records.append(
                AbqpyApiRecord(
                    record_id=(f"abqpy:{normalized_version}:{callable_qualified_name}"),
                    entity_kind=entity_kind,
                    declaring_name=f"{class_definition.class_name}.{method_name}",
                    class_name=class_definition.class_name,
                    method_name=method_name,
                    public_names=public_names,
                    aliases=aliases,
                    signatures=tuple(
                        _signature(function.node, bound_method=True)
                        for function in functions
                    ),
                    documentation=tuple(
                        extract_docstring(
                            function.node, source_hashes[function.source_path]
                        )
                        for function in functions
                    ),
                    bases=declaring_node.bases,
                    class_graph=receiver_nodes,
                    source=_source_ref(
                        release=normalized_release,
                        version=normalized_version,
                        source_path=functions[0].source_path,
                    ),
                )
            )

    command_groups: dict[tuple[str, str], list[_FunctionNode]] = defaultdict(list)
    for command in distribution.commands:
        command_groups[(command.module_name, command.node.name)].append(command)
    for (module_name, function_name), functions in command_groups.items():
        qualified_name = f"{module_name}.{function_name}"
        records.append(
            AbqpyApiRecord(
                record_id=f"abqpy:{normalized_version}:{qualified_name}",
                entity_kind="command",
                declaring_name=qualified_name,
                class_name=module_name,
                method_name=function_name,
                public_names=(qualified_name,),
                signatures=tuple(
                    _signature(function.node, bound_method=False)
                    for function in functions
                ),
                documentation=tuple(
                    extract_docstring(
                        function.node, source_hashes[function.source_path]
                    )
                    for function in functions
                ),
                source=_source_ref(
                    release=normalized_release,
                    version=normalized_version,
                    source_path=functions[0].source_path,
                ),
            )
        )

    yield from sorted(records, key=lambda record: record.record_id)


def _required_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AbqpyExtractionError(f"{field_name} must not be empty")
    return normalized


def _source_ref(*, release: str, version: str, source_path: str) -> ApiSourceRef:
    return ApiSourceRef(
        kind="abqpy",
        release=release,
        version=version,
        source_path=source_path,
    )


def _read_source_files(source_root: Path) -> tuple[_SourceFile, ...]:
    try:
        resolved = source_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise AbqpyExtractionError(
            f"abqpy source does not exist: {source_root}"
        ) from exc

    if resolved.is_dir():
        return _read_directory_source(resolved)
    if not resolved.is_file() or not zipfile.is_zipfile(resolved):
        raise AbqpyExtractionError(
            f"abqpy source must be a directory, wheel, or ZIP archive: {resolved}"
        )
    return _read_archive_source(resolved)


def _read_directory_source(root: Path) -> tuple[_SourceFile, ...]:
    package_root, module_root = _locate_directory_package(root)
    files: list[_SourceFile] = []
    for candidate in sorted(package_root.rglob("*.py")):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        size = candidate.stat().st_size
        if size > _MAX_SOURCE_BYTES:
            raise AbqpyExtractionError(
                f"abqpy source file exceeds {_MAX_SOURCE_BYTES} bytes: {candidate}"
            )
        source_path = candidate.relative_to(module_root).as_posix()
        files.append(
            _source_file(
                source_path=source_path,
                payload=candidate.read_bytes(),
            )
        )
    if not files:
        raise AbqpyExtractionError(f"abqpy package contains no Python source: {root}")
    return tuple(files)


def _locate_directory_package(root: Path) -> tuple[Path, Path]:
    candidates: list[tuple[Path, Path]] = []
    if root.name == "abaqus" and (root / "__init__.py").is_file():
        candidates.append((root, root.parent))
    for module_root in (root, root / "src"):
        package_root = module_root / "abaqus"
        if (package_root / "__init__.py").is_file():
            candidates.append((package_root, module_root))
    unique = {(package.resolve(), base.resolve()) for package, base in candidates}
    if not unique:
        raise AbqpyExtractionError(
            f"could not locate an abaqus package below source root: {root}"
        )
    package_root, module_root = sorted(
        unique,
        key=lambda item: (len(item[0].parts), item[0].as_posix()),
    )[0]
    return package_root, module_root


def _read_archive_source(archive: Path) -> tuple[_SourceFile, ...]:
    with zipfile.ZipFile(archive) as distribution:
        infos = [info for info in distribution.infolist() if not info.is_dir()]
        package_prefix, module_prefix = _locate_archive_package(infos, archive)
        files: list[_SourceFile] = []
        seen_paths: set[str] = set()
        for info in sorted(infos, key=lambda item: item.filename):
            parts = _safe_archive_parts(info.filename, archive)
            if not _is_below(parts, package_prefix) or not info.filename.endswith(
                ".py"
            ):
                continue
            if info.file_size > _MAX_SOURCE_BYTES:
                raise AbqpyExtractionError(
                    f"abqpy source member exceeds {_MAX_SOURCE_BYTES} bytes: "
                    f"{archive}!/{info.filename}"
                )
            source_path = PurePosixPath(*parts[len(module_prefix) :]).as_posix()
            if source_path in seen_paths:
                raise AbqpyExtractionError(
                    f"duplicate abqpy source path in archive: {source_path}"
                )
            seen_paths.add(source_path)
            files.append(
                _source_file(
                    source_path=source_path,
                    payload=distribution.read(info),
                )
            )
    if not files:
        raise AbqpyExtractionError(
            f"abqpy archive contains no Python source: {archive}"
        )
    return tuple(files)


def _locate_archive_package(
    infos: list[zipfile.ZipInfo],
    archive: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidates: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for info in infos:
        parts = _safe_archive_parts(info.filename, archive)
        if len(parts) < 2 or parts[-2:] != ("abaqus", "__init__.py"):
            continue
        package_prefix = parts[:-1]
        module_prefix = package_prefix[:-1]
        candidates.append((package_prefix, module_prefix))
    if not candidates:
        raise AbqpyExtractionError(
            f"could not locate an abaqus package in archive: {archive}"
        )
    return sorted(
        set(candidates),
        key=lambda item: (len(item[0]), "/".join(item[0])),
    )[0]


def _safe_archive_parts(filename: str, archive: Path) -> tuple[str, ...]:
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts:
        raise AbqpyExtractionError(
            f"unsafe source member in abqpy archive: {archive}!/{filename}"
        )
    return tuple(part for part in path.parts if part not in {"", "."})


def _is_below(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(parts) >= len(prefix) and parts[: len(prefix)] == prefix


def _source_file(*, source_path: str, payload: bytes) -> _SourceFile:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(payload).readline)
        source = payload.decode(encoding)
    except (LookupError, SyntaxError, UnicodeDecodeError) as exc:
        raise AbqpyExtractionError(
            f"could not decode abqpy source: {source_path}"
        ) from exc
    module_name, is_package = _module_name(source_path)
    return _SourceFile(
        module_name=module_name,
        source_path=source_path,
        source=source,
        is_package=is_package,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _module_name(source_path: str) -> tuple[str, bool]:
    path = PurePosixPath(source_path)
    if path.suffix != ".py" or not path.parts or path.parts[0] != "abaqus":
        raise AbqpyExtractionError(f"invalid abaqus source path: {source_path}")
    is_package = path.name == "__init__.py"
    module_parts = path.parts[:-1] if is_package else (*path.parts[:-1], path.stem)
    return ".".join(module_parts), is_package


def _parse_distribution(source_files: tuple[_SourceFile, ...]) -> _ParsedDistribution:
    classes: dict[str, _ClassDefinition] = {}
    commands: list[_FunctionNode] = []
    for source_file in source_files:
        try:
            tree = ast.parse(
                source_file.source,
                filename=source_file.source_path,
                type_comments=True,
            )
        except SyntaxError as exc:
            location = f":{exc.lineno}" if exc.lineno is not None else ""
            raise AbqpyExtractionError(
                f"invalid Python syntax in {source_file.source_path}{location}: "
                f"{exc.msg}"
            ) from exc
        imports = _imports_for_module(
            tree,
            module_name=source_file.module_name,
            is_package=source_file.is_package,
        )
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                commands.append(
                    _FunctionNode(
                        node=statement,
                        module_name=source_file.module_name,
                        source_path=source_file.source_path,
                        class_qualified_name=None,
                        class_name=None,
                    )
                )
            elif isinstance(statement, ast.ClassDef):
                _collect_class(
                    statement,
                    source_file=source_file,
                    imports=imports,
                    parent_qualified_name=None,
                    classes=classes,
                )
    return _ParsedDistribution(classes=classes, commands=tuple(commands))


def _imports_for_module(
    tree: ast.Module,
    *,
    module_name: str,
    is_package: bool,
) -> dict[str, str]:
    imports: dict[str, str] = {}
    package_name = module_name if is_package else module_name.rpartition(".")[0]
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                bound_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                imports[bound_name] = alias.name if alias.asname else bound_name
        elif isinstance(statement, ast.ImportFrom):
            imported_module = _resolve_import_module(
                package_name,
                module=statement.module,
                level=statement.level,
            )
            for alias in statement.names:
                if alias.name == "*":
                    continue
                bound_name = alias.asname or alias.name
                imports[bound_name] = (
                    f"{imported_module}.{alias.name}" if imported_module else alias.name
                )
    return imports


def _resolve_import_module(
    package_name: str,
    *,
    module: str | None,
    level: int,
) -> str:
    if level == 0:
        return module or ""
    package_parts = package_name.split(".") if package_name else []
    parents_to_remove = level - 1
    if parents_to_remove > len(package_parts):
        return module or ""
    base_parts = package_parts[: len(package_parts) - parents_to_remove]
    if module:
        base_parts.extend(module.split("."))
    return ".".join(base_parts)


def _collect_class(
    node: ast.ClassDef,
    *,
    source_file: _SourceFile,
    imports: dict[str, str],
    parent_qualified_name: str | None,
    classes: dict[str, _ClassDefinition],
) -> None:
    qualified_name = (
        f"{parent_qualified_name}.{node.name}"
        if parent_qualified_name is not None
        else f"{source_file.module_name}.{node.name}"
    )
    if qualified_name in classes:
        raise AbqpyExtractionError(
            f"duplicate abqpy class definition: {qualified_name}"
        )
    methods: dict[str, list[_FunctionNode]] = defaultdict(list)
    nested_classes: list[ast.ClassDef] = []
    for statement in node.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[statement.name].append(
                _FunctionNode(
                    node=statement,
                    module_name=source_file.module_name,
                    source_path=source_file.source_path,
                    class_qualified_name=qualified_name,
                    class_name=node.name,
                )
            )
        elif isinstance(statement, ast.ClassDef):
            nested_classes.append(statement)
    classes[qualified_name] = _ClassDefinition(
        qualified_name=qualified_name,
        class_name=node.name,
        module_name=source_file.module_name,
        source_path=source_file.source_path,
        raw_bases=tuple(ast.unparse(base) for base in node.bases),
        imports=dict(imports),
        methods={name: tuple(definitions) for name, definitions in methods.items()},
        node=node,
        source_file=source_file,
    )
    for nested_class in nested_classes:
        _collect_class(
            nested_class,
            source_file=source_file,
            imports=imports,
            parent_qualified_name=qualified_name,
            classes=classes,
        )


def _build_class_graph(
    classes: dict[str, _ClassDefinition],
) -> tuple[dict[str, ApiClassNode], dict[str, tuple[str, ...]]]:
    simple_names: dict[str, list[str]] = defaultdict(list)
    for qualified_name, definition in classes.items():
        simple_names[definition.class_name].append(qualified_name)

    nodes: dict[str, ApiClassNode] = {}
    mutable_children: dict[str, set[str]] = defaultdict(set)
    for qualified_name, definition in classes.items():
        resolved_bases = tuple(
            _resolve_base(base, definition, classes, simple_names)
            for base in definition.raw_bases
        )
        nodes[qualified_name] = ApiClassNode(
            qualified_name=qualified_name,
            class_name=definition.class_name,
            bases=resolved_bases,
            public_names=(definition.class_name,),
        )
        for base in resolved_bases:
            if base in classes:
                mutable_children[base].add(qualified_name)
    children = {
        name: tuple(sorted(descendants))
        for name, descendants in mutable_children.items()
    }
    return nodes, children


def _resolve_base(
    raw_base: str,
    definition: _ClassDefinition,
    classes: dict[str, _ClassDefinition],
    simple_names: dict[str, list[str]],
) -> str:
    root, separator, remainder = raw_base.partition(".")
    imported = definition.imports.get(root)
    if imported is not None:
        candidate = f"{imported}.{remainder}" if separator else imported
        if candidate in classes:
            return candidate
    same_module = f"{definition.module_name}.{raw_base}"
    if same_module in classes:
        return same_module
    matches = simple_names.get(raw_base, [])
    if len(matches) == 1:
        return matches[0]
    return raw_base


def _method_receivers(
    declaring_class: str,
    method_name: str,
    classes: dict[str, _ClassDefinition],
    children: dict[str, tuple[str, ...]],
    *,
    expand: bool,
) -> tuple[str, ...]:
    if not expand:
        return (declaring_class,)
    receivers = [declaring_class]
    pending = deque(children.get(declaring_class, ()))
    visited = {declaring_class}
    while pending:
        candidate = pending.popleft()
        if candidate in visited:
            continue
        visited.add(candidate)
        if method_name in classes[candidate].methods:
            continue
        receivers.append(candidate)
        pending.extend(children.get(candidate, ()))
    return tuple(receivers)


def _signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    bound_method: bool,
) -> ApiSignature:
    parameters = _parameters(node, bound_method=bound_method)
    return_annotation = ast.unparse(node.returns) if node.returns is not None else None
    signature_text = _signature_text(
        node.name,
        parameters,
        return_annotation=return_annotation,
        asynchronous=isinstance(node, ast.AsyncFunctionDef),
    )
    return ApiSignature(
        text=signature_text,
        parameters=parameters,
        return_annotation=return_annotation,
        overload=_has_decorator(node, "overload"),
    )


def _parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    bound_method: bool,
) -> tuple[ApiParameter, ...]:
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    default_start = len(positional) - len(arguments.defaults)
    positional_defaults: list[ast.expr | None] = [None] * default_start + list(
        arguments.defaults
    )
    parameters: list[ApiParameter] = []
    for index, (argument, default) in enumerate(
        zip(positional, positional_defaults, strict=True)
    ):
        kind = (
            "positional_only"
            if index < len(arguments.posonlyargs)
            else "positional_or_keyword"
        )
        parameters.append(_parameter(argument, kind=kind, default=default))

    is_static = _has_decorator(node, "staticmethod")
    if (
        bound_method
        and not is_static
        and parameters
        and parameters[0].name in {"self", "cls"}
    ):
        parameters.pop(0)

    if arguments.vararg is not None:
        parameters.append(
            _parameter(arguments.vararg, kind="var_positional", default=None)
        )
    for argument, default in zip(
        arguments.kwonlyargs,
        arguments.kw_defaults,
        strict=True,
    ):
        parameters.append(_parameter(argument, kind="keyword_only", default=default))
    if arguments.kwarg is not None:
        parameters.append(_parameter(arguments.kwarg, kind="var_keyword", default=None))
    return tuple(parameters)


def _parameter(
    argument: ast.arg,
    *,
    kind: ApiParameterKind,
    default: ast.expr | None,
) -> ApiParameter:
    annotation = ast.unparse(argument.annotation) if argument.annotation else None
    default_text = ast.unparse(default) if default is not None else None
    return ApiParameter(
        name=argument.arg,
        kind=kind,
        annotation=annotation,
        default=default_text,
        required=default is None and kind not in {"var_positional", "var_keyword"},
    )


def _signature_text(
    name: str,
    parameters: tuple[ApiParameter, ...],
    *,
    return_annotation: str | None,
    asynchronous: bool,
) -> str:
    parts: list[str] = []
    positional_only_count = sum(
        parameter.kind == "positional_only" for parameter in parameters
    )
    has_var_positional = any(
        parameter.kind == "var_positional" for parameter in parameters
    )
    inserted_keyword_separator = False
    for index, parameter in enumerate(parameters):
        if (
            parameter.kind == "keyword_only"
            and not has_var_positional
            and not inserted_keyword_separator
        ):
            parts.append("*")
            inserted_keyword_separator = True
        parts.append(_format_parameter(parameter))
        if positional_only_count and index + 1 == positional_only_count:
            parts.append("/")
    prefix = "async " if asynchronous else ""
    result = f"{prefix}{name}({', '.join(parts)})"
    if return_annotation is not None:
        result = f"{result} -> {return_annotation}"
    return result


def _format_parameter(parameter: ApiParameter) -> str:
    prefix = {
        "var_positional": "*",
        "var_keyword": "**",
    }.get(parameter.kind, "")
    result = f"{prefix}{parameter.name}"
    if parameter.annotation is not None:
        result = f"{result}: {parameter.annotation}"
    if parameter.default is not None:
        separator = " = " if parameter.annotation is not None else "="
        result = f"{result}{separator}{parameter.default}"
    return result


def _has_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator_name: str,
) -> bool:
    return any(
        _decorator_tail(decorator) == decorator_name
        for decorator in node.decorator_list
    )


def _decorator_tail(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Call):
        return _decorator_tail(decorator.func)
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return None
