from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from collections.abc import Callable
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from types import ModuleType
from typing import Protocol, cast

from anyio import to_thread

HARNESS_WINDOWS_NEWLINE_HASH_PATCH_VERSION = "harness-0.24.0-windows-newline-hash-v1"
HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION = "harness-0.24.0-0.31.0-windows-shell-tree-v2"
HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION = (
    "harness-0.24.0-0.31.0-windows-jsonl-append-v2"
)
_NEWLINE_HASH_AFFECTED_HARNESS_VERSIONS = frozenset({"0.24.0"})
_SHELL_TREE_AFFECTED_HARNESS_VERSIONS = frozenset({"0.24.0", "0.31.0"})
_JSONL_APPEND_AFFECTED_HARNESS_VERSIONS = frozenset({"0.24.0", "0.31.0"})
_HASH_PATCH_MARKER = "_abaqus_sim_agent_newline_hash_patch_version"
_SHELL_PATCH_MARKER = "_abaqus_sim_agent_shell_tree_patch_version"
_JSONL_PATCH_MARKER = "_abaqus_sim_agent_jsonl_append_patch_version"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_JSONL_APPEND_LOCK = threading.Lock()
_JSONL_PATCH_INSTALL_LOCK = threading.Lock()


class _ContentHashModule(Protocol):
    _content_hash: Callable[[str], str]


def apply_harness_windows_newline_hash_patch(
    *,
    platform_name: str | None = None,
    harness_version: str | None = None,
    toolset_module: ModuleType | None = None,
) -> str | None:
    """为受影响的 Harness 版本统一 Windows 文本换行 hash。

    Harness 0.24.0 的 ``write_file`` 可能按内存中的 LF 文本返回 hash，
    而 ``read_file`` 会按落盘后的 CRLF 原始字节计算 hash，导致未发生并发
    修改时也触发 stale edit。本补丁只在 Windows 和精确版本 0.24.0 下替换
    上游模块的私有 ``_content_hash``，升级后自动失效。

    Args:
        platform_name: 用于测试的平台名；默认读取 ``os.name``。
        harness_version: 用于测试的 Harness 版本；默认读取安装包元数据。
        toolset_module: 用于测试的上游工具模块；默认延迟导入真实模块。

    Returns:
        已启用时返回补丁版本；环境不匹配或包不存在时返回 ``None``。
    """
    if not _is_affected_windows_harness(
        platform_name,
        harness_version,
        _NEWLINE_HASH_AFFECTED_HARNESS_VERSIONS,
    ):
        return None

    if toolset_module is None:
        toolset_module = import_module("pydantic_ai_harness.filesystem._toolset")

    existing_patch = getattr(toolset_module, _HASH_PATCH_MARKER, None)
    if existing_patch == HARNESS_WINDOWS_NEWLINE_HASH_PATCH_VERSION:
        return HARNESS_WINDOWS_NEWLINE_HASH_PATCH_VERSION
    if existing_patch is not None:
        return None

    def canonical_content_hash(content: str) -> str:
        canonical = content.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    content_hash_module = cast(_ContentHashModule, toolset_module)
    content_hash_module._content_hash = canonical_content_hash
    setattr(
        toolset_module,
        _HASH_PATCH_MARKER,
        HARNESS_WINDOWS_NEWLINE_HASH_PATCH_VERSION,
    )
    return HARNESS_WINDOWS_NEWLINE_HASH_PATCH_VERSION


def apply_harness_windows_shell_tree_patch(
    *,
    platform_name: str | None = None,
    harness_version: str | None = None,
    shell_toolset_class: type[object] | None = None,
    tree_terminator: Callable[[int], None] | None = None,
) -> str | None:
    """修复已确认受影响的 Harness 版本在 Windows 上调用 ``os.killpg`` 的错误。

    上游 ``ShellToolset`` 的超时和后台清理路径使用 POSIX process-group
    API；Windows 不提供 ``os.getpgid``/``os.killpg``，因此超时会把整个
    Agent run 变成基础设施异常。本补丁只替换已复现版本的私有终止方法，
    使用 Windows ``taskkill /T /F`` 终止准确 PID 的进程树。

    Args:
        platform_name: 用于测试的平台名；默认读取 ``os.name``。
        harness_version: 用于测试的 Harness 版本；默认读取安装包元数据。
        shell_toolset_class: 用于测试的 ShellToolset 类；默认延迟导入真实类。
        tree_terminator: 用于测试的同步进程树终止函数；默认调用 ``taskkill``。

    Returns:
        已启用时返回补丁版本；环境不匹配或存在其他补丁时返回 ``None``。
    """
    if not _is_affected_windows_harness(
        platform_name,
        harness_version,
        _SHELL_TREE_AFFECTED_HARNESS_VERSIONS,
    ):
        return None

    if shell_toolset_class is None:
        shell_module = import_module("pydantic_ai_harness.shell._toolset")
        shell_toolset_class = cast(type[object], shell_module.ShellToolset)

    existing_patch = getattr(shell_toolset_class, _SHELL_PATCH_MARKER, None)
    if existing_patch == HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION:
        return HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION
    if existing_patch is not None:
        return None

    terminate_tree = tree_terminator or _terminate_windows_process_tree

    async def kill_process_group(_self: object, proc: object) -> None:
        pid = getattr(proc, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            return
        await to_thread.run_sync(terminate_tree, pid)

    setattr(  # noqa: B010 - version-scoped patch of an upstream private method
        shell_toolset_class,
        "_kill_process_group",
        kill_process_group,
    )
    setattr(
        shell_toolset_class,
        _SHELL_PATCH_MARKER,
        HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION,
    )
    return HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION


def apply_harness_windows_jsonl_append_patch(
    *,
    platform_name: str | None = None,
    harness_version: str | None = None,
    file_step_store_class: type[object] | None = None,
) -> str | None:
    """串行化已确认受影响的 Harness 版本在 Windows 上的 JSONL 追加。

    同一模型响应中的并行工具会从多个worker thread打开同一
    ``tool_effects.jsonl``。Windows append handle不能保证这些文本写入彼此
    不覆盖，已复现完整记录被截成 ``ll}``，导致后续resume无法解析。
    本补丁只包装已复现版本的event/tool-effect同步追加方法；不同进程仍由
    CodingRun state的OS锁隔离。

    Args:
        platform_name: 用于测试的平台名；默认读取 ``os.name``。
        harness_version: 用于测试的Harness版本；默认读取安装包元数据。
        file_step_store_class: 用于测试的FileStepStore类；默认延迟导入。

    Returns:
        已启用时返回补丁版本；环境不匹配或存在其他补丁时返回``None``。
    """
    if not _is_affected_windows_harness(
        platform_name,
        harness_version,
        _JSONL_APPEND_AFFECTED_HARNESS_VERSIONS,
    ):
        return None
    if file_step_store_class is None:
        store_module = import_module("pydantic_ai_harness.step_persistence._store")
        file_step_store_class = cast(type[object], store_module.FileStepStore)

    with _JSONL_PATCH_INSTALL_LOCK:
        return _install_jsonl_append_patch(file_step_store_class)


def _install_jsonl_append_patch(
    file_step_store_class: type[object],
) -> str | None:
    existing_patch = getattr(file_step_store_class, _JSONL_PATCH_MARKER, None)
    if existing_patch == HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION:
        return HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION
    if existing_patch is not None:
        return None

    original_event_append = cast(
        Callable[[object, object], None],
        getattr(file_step_store_class, "_sync_append_event"),  # noqa: B009
    )
    original_effect_append = cast(
        Callable[[object, object], None],
        getattr(file_step_store_class, "_sync_record_tool_effect"),  # noqa: B009
    )

    setattr(  # noqa: B010 - version-scoped patch of upstream private methods
        file_step_store_class,
        "_sync_append_event",
        _serialized_jsonl_append(original_event_append),
    )
    setattr(  # noqa: B010 - version-scoped patch of upstream private methods
        file_step_store_class,
        "_sync_record_tool_effect",
        _serialized_jsonl_append(original_effect_append),
    )
    setattr(
        file_step_store_class,
        _JSONL_PATCH_MARKER,
        HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION,
    )
    return HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION


def _serialized_jsonl_append(
    original: Callable[[object, object], None],
) -> Callable[[object, object], None]:
    def serialized(self: object, value: object) -> None:
        with _JSONL_APPEND_LOCK:
            original(self, value)

    return serialized


def _is_affected_windows_harness(
    platform_name: str | None,
    harness_version: str | None,
    affected_versions: frozenset[str],
) -> bool:
    if (os.name if platform_name is None else platform_name) != "nt":
        return False
    if harness_version is None:
        try:
            harness_version = version("pydantic-ai-harness")
        except PackageNotFoundError:
            return False
    return harness_version in affected_versions


def _terminate_windows_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )
