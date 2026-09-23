"""新会话的本机目录选择；不读取文件内容或创建目录。"""

import os
import string
from pathlib import Path

from .contracts import DirectoryBrowse, DirectoryChoice, WebError
from .storage import local_directory, selected_workspace

MAX_DIRECTORY_ENTRIES = 500
MAX_DIRECTORY_SCAN = 20_000


def browse_directories(managed_root: Path, value: str | None) -> DirectoryBrowse:
    if value is None and os.name == "nt":
        drives = []
        for letter in string.ascii_uppercase:
            path = Path(f"{letter}:/")
            try:
                if path.is_dir():
                    drives.append(DirectoryChoice(name=f"{letter}:", path=str(path)))
            except OSError:
                continue
        return DirectoryBrowse(drives=tuple(drives))
    path = local_directory(value if value is not None else Path.home())
    root = managed_root.resolve()
    if path == root or path.is_relative_to(root):
        raise WebError("managed_directory", "宿主管理目录不作为工程目录浏览。", 400)
    choices: list[DirectoryChoice] = []
    truncated = False
    try:
        # Stop as soon as the response bound is reached; do not recurse or hash files.
        with os.scandir(path) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_SCAN:
                    truncated = True
                    break
                child = Path(entry.path)
                try:
                    if entry.is_symlink() or child.is_junction() or not entry.is_dir():
                        continue
                    if child == root or child.is_relative_to(root):
                        continue
                    if len(choices) == MAX_DIRECTORY_ENTRIES:
                        truncated = True
                        break
                    choices.append(DirectoryChoice(name=entry.name, path=str(child)))
                except OSError:
                    continue
    except OSError as exc:
        raise WebError(
            "directory_unavailable", "无法列出这个目录，请检查访问权限。", 400
        ) from exc
    error = None
    try:
        selected_workspace(path, root)
    except WebError as exc:
        error = exc.message
    return DirectoryBrowse(
        path=str(path),
        parent_path=str(path.parent) if path.parent != path else None,
        directories=tuple(sorted(choices, key=lambda item: item.name.casefold())),
        selectable=error is None,
        selection_error=error,
        truncated=truncated,
    )
