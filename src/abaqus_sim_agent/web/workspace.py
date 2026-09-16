from __future__ import annotations

from pathlib import Path, PurePosixPath

from .contracts import WebError, WorkspaceDirectory, WorkspaceEntry, WorkspaceSearch
from .controller import RunController
from .storage import plain_path

MAX_DIRECTORY_ENTRIES = 500
MAX_SEARCH_SCAN = 20_000
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
TEXT_PREVIEW_SUFFIXES = {
    ".csv",
    ".dat",
    ".inp",
    ".json",
    ".log",
    ".md",
    ".msg",
    ".mtl",
    ".obj",
    ".py",
    ".rpy",
    ".sta",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
IMAGE_PREVIEW_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _parts(value: str) -> tuple[str, ...]:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized:
        return ()
    path = PurePosixPath(normalized)
    parts = path.parts
    if (
        path.is_absolute()
        or value.startswith(("/", "\\"))
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    ):
        raise WebError("unsafe_path", "工作区路径必须是安全的相对路径", 400)
    return parts


def workspace_path(
    controller: RunController,
    run_id: str,
    relative_path: str,
    *,
    directory: bool | None = None,
) -> tuple[Path, Path]:
    registration = controller.registry.load(run_id)
    root = plain_path(registration.request.workspace, directory=True).resolve()
    candidate = plain_path(root.joinpath(*_parts(relative_path)), directory=directory)
    if not candidate.exists():
        raise WebError("not_found", "工作区路径不存在", 404)
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise WebError("unsafe_path", "工作区路径越界", 400) from exc
    return root, candidate


def _entry(root: Path, path: Path) -> WorkspaceEntry:
    safe = plain_path(path)
    kind = "directory" if safe.is_dir() else "file"
    return WorkspaceEntry(
        relative_path=safe.relative_to(root).as_posix(),
        name=safe.name,
        kind=kind,
        size_bytes=safe.stat().st_size if kind == "file" else None,
    )


def list_workspace(
    controller: RunController, run_id: str, relative_path: str
) -> WorkspaceDirectory:
    root, directory = workspace_path(controller, run_id, relative_path, directory=True)
    entries: list[WorkspaceEntry] = []
    truncated = False
    for index, child in enumerate(
        sorted(
            directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
        )
    ):
        if index >= MAX_DIRECTORY_ENTRIES:
            truncated = True
            break
        if child.is_symlink() or child.is_junction():
            continue
        entries.append(_entry(root, child))
    return WorkspaceDirectory(
        run_id=run_id,
        path=directory.relative_to(root).as_posix() if directory != root else "",
        items=tuple(entries),
        truncated=truncated,
    )


def search_workspace(
    controller: RunController, run_id: str, query: str, limit: int
) -> WorkspaceSearch:
    registration = controller.registry.load(run_id)
    root = plain_path(registration.request.workspace, directory=True).resolve()
    needle = query.strip().lower()
    if not needle:
        return WorkspaceSearch(run_id=run_id, query=query, items=())
    matches: list[WorkspaceEntry] = []
    pending = [root]
    scanned = 0
    truncated = False
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if child.is_symlink() or child.is_junction():
                continue
            scanned += 1
            if scanned > MAX_SEARCH_SCAN:
                truncated = True
                pending.clear()
                break
            entry = _entry(root, child)
            if entry.kind == "directory":
                pending.append(child)
                continue
            if needle in entry.relative_path.lower():
                matches.append(entry)
                if len(matches) >= limit:
                    truncated = True
                    pending.clear()
                    break
    return WorkspaceSearch(
        run_id=run_id,
        query=query,
        items=tuple(matches),
        truncated=truncated,
    )


def workspace_preview(
    controller: RunController, run_id: str, relative_path: str
) -> tuple[Path, str]:
    _root, file = workspace_path(controller, run_id, relative_path, directory=False)
    if file.stat().st_size > MAX_PREVIEW_BYTES:
        raise WebError("preview_too_large", "文件超过网页预览上限", 413)
    suffix = file.suffix.lower()
    if suffix in IMAGE_PREVIEW_TYPES:
        return file, IMAGE_PREVIEW_TYPES[suffix]
    if suffix in TEXT_PREVIEW_SUFFIXES or file.stat().st_size == 0:
        return file, "text/plain; charset=utf-8"
    raise WebError("preview_unsupported", "该文件类型暂不支持网页预览", 415)
