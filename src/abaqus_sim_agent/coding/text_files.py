from __future__ import annotations

import errno
import fnmatch
import functools
import hashlib
import os
import re
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Concatenate

from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import FilteredToolset
from pydantic_ai_harness.filesystem import FileSystem, FileSystemToolset
from pydantic_ai_harness.filesystem._events import FileReadEvent, FileWrittenEvent
from pydantic_ai_harness.filesystem._toolset import (
    READ_ONLY_TOOL_NAMES,
    _format_lines,
    _is_binary,
    _matching_lines,
    _model_safe_filename,
    _sanitize_recoverable_error,
)

from .text_encoding import (
    DecodedText,
    UnsupportedTextEncodingError,
    decode_workspace_text,
    encode_workspace_text,
)

_CREATE_FLAGS = os.O_BINARY if os.name == "nt" else os.O_NONBLOCK | os.O_NOFOLLOW
_RECOVERABLE_ERRORS = (
    PermissionError,
    FileNotFoundError,
    NotADirectoryError,
    IsADirectoryError,
    ValueError,
)
_RECOVERABLE_ERRNOS: dict[int | None, str] = {
    errno.ENAMETOOLONG: "The path name is too long.",
    errno.ELOOP: "The path resolves through a symlink loop.",
    errno.EILSEQ: "The path name contains a byte sequence the filesystem cannot represent.",
}


def _recoverable_text[ToolsetT: FileSystemToolset[object], **P](
    function: Callable[Concatenate[ToolsetT, P], Awaitable[str]],
) -> Callable[Concatenate[ToolsetT, P], Awaitable[str]]:
    @functools.wraps(function)
    async def wrapper(self: ToolsetT, *args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return await function(self, *args, **kwargs)
        except _RECOVERABLE_ERRORS as exc:
            raise ModelRetry(_sanitize_recoverable_error(exc, self._real_root)) from exc
        except OSError as exc:
            reason = _RECOVERABLE_ERRNOS.get(exc.errno)
            if reason is None and getattr(exc, "winerror", None) == 123:
                reason = "The path name is invalid."
            if reason is None:
                raise
            raise ModelRetry(reason) from exc

    return wrapper


def _raw_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:12]


@dataclass
class AbaqusTextFileSystem(FileSystem[AgentDepsT]):
    """使用 UTF-8/CP936 无损文本策略的工作区 FileSystem capability。"""

    def get_toolset(
        self,
    ) -> AbaqusTextFileSystemToolset[AgentDepsT] | FilteredToolset[AgentDepsT]:
        toolset = AbaqusTextFileSystemToolset[AgentDepsT](
            root_dir=Path(self.root_dir),
            allowed_patterns=self.allowed_patterns,
            denied_patterns=self.denied_patterns,
            protected_patterns=self.protected_patterns,
            max_read_lines=self.max_read_lines,
            max_list_results=self.max_list_results,
            max_search_results=self.max_search_results,
            max_find_results=self.max_find_results,
            id=self.id or "file_system",
        )
        if self.read_only:
            return FilteredToolset(
                toolset, lambda _ctx, tool: tool.name in READ_ONLY_TOOL_NAMES
            )
        return toolset


class AbaqusTextFileSystemToolset(FileSystemToolset[AgentDepsT]):
    """保留 Harness 路径授权，只替换文本编码、hash、搜索和写回语义。"""

    @_recoverable_text
    async def _read_file(
        self,
        ctx: RunContext[object] | None,
        path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> str:
        if limit is None:
            limit = self._max_read_lines
        resolved = self._safe_resolve(path)
        if not resolved.is_file():
            if resolved.is_dir():
                raise FileNotFoundError(f"{path!r} is a directory, not a file.")
            raise FileNotFoundError(f"File not found: {path}")

        raw = resolved.read_bytes()
        content_hash = _raw_hash(raw)
        if _is_binary(raw):
            if ctx is not None:
                await ctx.emit(
                    FileReadEvent(
                        **self._event_location(resolved), content_hash=content_hash
                    )
                )
            return (
                f"[Binary file: {len(raw)} bytes. Use a binary-aware tool to inspect.]"
            )

        decoded = decode_workspace_text(resolved, raw)
        lines = decoded.text.splitlines(keepends=True)
        body = _format_lines(lines, offset, limit)
        if ctx is not None:
            await ctx.emit(
                FileReadEvent(
                    **self._event_location(resolved), content_hash=content_hash
                )
            )
        return (
            f"[{path} | {len(lines)} lines | encoding:{decoded.encoding} | hash:{content_hash}]\n"
            + body
        )

    @_recoverable_text
    async def _write_file(
        self,
        ctx: RunContext[object] | None,
        path: str,
        content: str,
        *,
        expected_hash: str | None = None,
    ) -> str:
        resolved = self._safe_resolve(path, write=True)
        if resolved.exists() and not resolved.is_file():
            raise ModelRetry(f"Path {path!r} exists and is not a regular file.")
        if not resolved.parent.exists():
            parent_rel = str(resolved.parent.relative_to(self._root))
            raise FileNotFoundError(
                f"Parent directory {parent_rel!r} does not exist. Use create_directory first."
            )

        stream, created = self._open_for_update(resolved, path, create=True)
        with stream:
            before = stream.read()
            current_hash = _raw_hash(before)
            if expected_hash is not None and not created:
                self._check_hash(path, current_hash, expected_hash)
            encoding = (
                "utf-8" if created else self._decode_existing(resolved, before).encoding
            )
            encoded = encode_workspace_text(resolved, content, encoding)
            stream.seek(0)
            stream.truncate(0)
            stream.write(encoded)

        new_hash = _raw_hash(encoded)
        if ctx is not None:
            await ctx.emit(
                FileWrittenEvent(
                    **self._event_location(resolved), content_hash=new_hash
                )
            )
        return (
            f"Wrote {len(content)} chars ({len(content.splitlines())} lines) to {path}. "
            f"[encoding:{encoding} | hash:{new_hash}]"
        )

    @_recoverable_text
    async def _edit_file(
        self,
        ctx: RunContext[object] | None,
        path: str,
        old_text: str,
        new_text: str,
        *,
        expected_hash: str | None = None,
    ) -> str:
        resolved = self._safe_resolve(path, write=True)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        stream, _created = self._open_for_update(resolved, path, create=False)
        with stream:
            before = stream.read()
            current_hash = _raw_hash(before)
            if expected_hash is not None:
                self._check_hash(path, current_hash, expected_hash)
            decoded = self._decode_existing(resolved, before)
            count = decoded.text.count(old_text)
            if count == 0:
                raise ValueError(f"old_text not found in {path}.")
            if count > 1:
                raise ValueError(
                    f"old_text found {count} times in {path}. Include more surrounding context to make the match unique."
                )
            changed = decoded.text.replace(old_text, new_text, 1)
            encoded = encode_workspace_text(resolved, changed, decoded.encoding)
            stream.seek(0)
            stream.truncate(0)
            stream.write(encoded)

        new_hash = _raw_hash(encoded)
        if ctx is not None:
            await ctx.emit(
                FileWrittenEvent(
                    **self._event_location(resolved), content_hash=new_hash
                )
            )
        return f"Edited {path}. [encoding:{decoded.encoding} | hash:{new_hash}]"

    @_recoverable_text
    async def search_files(
        self,
        pattern: str,
        *,
        path: str = ".",
        include_glob: str | None = None,
    ) -> str:
        resolved = self._safe_resolve(path, check_allowed=False)
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {exc}") from exc

        files = [resolved] if resolved.is_file() else sorted(resolved.rglob("*"))
        results: list[str] = []
        encodings: dict[str, str] = {}
        skipped = 0
        for file_path in files:
            try:
                rel_path = file_path.relative_to(self._real_root)
            except ValueError:  # pragma: no cover
                continue
            if any(part.startswith(".") for part in rel_path.parts):
                continue
            rel_str = str(rel_path)
            if include_glob and not fnmatch.fnmatch(rel_str, include_glob):
                continue
            target = self._resolve_walk_entry(file_path)
            if target is None or not target.is_file():
                continue
            try:
                raw = target.read_bytes()
            except OSError:  # pragma: no cover
                continue
            if _is_binary(raw):
                continue
            try:
                decoded = decode_workspace_text(target, raw)
            except UnsupportedTextEncodingError:
                if resolved.is_file():
                    raise
                skipped += 1
                continue
            matches, truncated = _matching_lines(
                decoded.text,
                compiled,
                rel_str,
                self._max_search_results - len(results),
            )
            if matches:
                encodings[rel_str] = decoded.encoding
                results.extend(matches)
            if truncated:
                results.append(f"[... truncated at {self._max_search_results} matches]")
                break
        if not results:
            return (
                f"No matches found. [{skipped} unsupported text file(s) skipped]"
                if skipped
                else "No matches found."
            )
        metadata = "; ".join(
            f"{name}={encoding}" for name, encoding in sorted(encodings.items())
        )
        results.append(f"[encodings: {metadata}]")
        if skipped:
            results.append(f"[{skipped} unsupported text file(s) skipped]")
        return "\n".join(results)

    @_recoverable_text
    async def file_info(self, path: str) -> str:
        resolved = self._safe_resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(f"Path not found: {path}")
        original = self._root / path
        is_link = original.is_symlink()
        kind = "directory" if resolved.is_dir() else "file"
        parts = [
            f"path: {path}",
            f"type: {kind}",
            f"size: {resolved.stat().st_size} bytes",
        ]
        if resolved.is_file():
            raw = resolved.read_bytes()
            binary = _is_binary(raw)
            parts.append(f"binary: {binary}")
            parts.append(f"hash: {_raw_hash(raw)}")
            if not binary:
                try:
                    decoded = decode_workspace_text(resolved, raw)
                except UnsupportedTextEncodingError:
                    parts.append("encoding: unsupported")
                else:
                    parts.append(f"encoding: {decoded.encoding}")
                    parts.append(f"lines: {len(decoded.text.splitlines())}")
        if is_link:
            target = _model_safe_filename(os.readlink(original), self._real_root)
            parts.append(f"symlink_target: {target}")
        return "\n".join(parts)

    @staticmethod
    def _check_hash(path: str, current_hash: str, expected_hash: str) -> None:
        if current_hash != expected_hash:
            raise ValueError(
                f"Conflict: file {path!r} has changed (expected hash:{expected_hash}, got hash:{current_hash}). Re-read the file and retry."
            )

    @staticmethod
    def _decode_existing(path: Path, raw: bytes) -> DecodedText:
        if _is_binary(raw):
            raise ValueError(
                f"{path.name} is a binary file; text operations are unavailable"
            )
        return decode_workspace_text(path, raw)

    @staticmethod
    def _open_for_update(
        resolved: Path, path: str, *, create: bool
    ) -> tuple[BinaryIO, bool]:
        descriptor = -1
        created = False
        try:
            if create:
                for _ in range(3):
                    try:
                        descriptor = os.open(
                            resolved,
                            os.O_RDWR | _CREATE_FLAGS | os.O_CREAT | os.O_EXCL,
                            0o666,
                        )
                    except FileExistsError:
                        try:
                            descriptor = os.open(resolved, os.O_RDWR | _CREATE_FLAGS)
                        except FileNotFoundError:
                            continue
                    else:
                        created = True
                    break
                else:
                    raise ModelRetry(
                        f"Path {path!r} changed repeatedly while opening. Retry the write."
                    )
            else:
                descriptor = os.open(resolved, os.O_RDWR | _CREATE_FLAGS)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ModelRetry(f"Path {path!r} exists and is not a regular file.")
            stream = os.fdopen(descriptor, "rb+")
            descriptor = -1
            return stream, created
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ModelRetry(
                    f"Path {path!r} encountered a symlink loop or changed to a symlink before opening."
                ) from exc
            if exc.errno in (errno.EISDIR, errno.ENODEV, errno.ENXIO):
                raise ModelRetry(
                    f"Path {path!r} exists and is not a regular file."
                ) from exc
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
