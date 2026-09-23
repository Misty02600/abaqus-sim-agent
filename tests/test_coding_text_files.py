from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai_harness.filesystem import FileSystemToolset

from abaqus_sim_agent.coding.text_encoding import (
    UnsupportedTextEncodingError,
    decode_workspace_text,
    encode_workspace_text,
)
from abaqus_sim_agent.coding.text_files import AbaqusTextFileSystem


def _toolset(tmp_path: Path) -> FileSystemToolset:
    toolset = AbaqusTextFileSystem(
        root_dir=tmp_path, protected_patterns=()
    ).get_toolset()
    assert isinstance(toolset, FileSystemToolset)
    return toolset


def _hash(result: str) -> str:
    match = re.search(r"hash:\s*([0-9a-f]{12})", result)
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(
    ("name", "raw", "encoding", "text"),
    [
        ("plain.txt", "结果完成".encode(), "utf-8", "结果完成"),
        (
            "abaqus.rpy",
            "# -*- coding: mbcs -*-\r\n#: 模型已创建。\r\n".encode("cp936"),
            "cp936",
            "#: 模型已创建。",
        ),
        ("model.inp", "** 类型: 位移/转角\r\n".encode("cp936"), "cp936", "位移/转角"),
    ],
)
def test_text_policy_decodes_supported_workspace_text(
    tmp_path: Path, name: str, raw: bytes, encoding: str, text: str
) -> None:
    decoded = decode_workspace_text(tmp_path / name, raw)
    assert decoded.encoding == encoding
    assert text in decoded.text
    assert encode_workspace_text(tmp_path / name, decoded.text, encoding) == raw


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("unsupported.py", b"# coding: latin-1\nvalue = '\xe9'\n"),
        ("broken.txt", b"\x81"),
        ("utf16.txt", b"\xff\xfea\x00"),
    ],
)
def test_text_policy_rejects_unsupported_or_invalid_text(
    tmp_path: Path, name: str, raw: bytes
) -> None:
    with pytest.raises(UnsupportedTextEncodingError):
        decode_workspace_text(tmp_path / name, raw)


@pytest.mark.asyncio
async def test_cp936_read_search_info_and_edit_preserve_original_bytes(
    tmp_path: Path,
) -> None:
    toolset = _toolset(tmp_path)
    path = tmp_path / "model.inp"
    original = "** Name: BC Type: 位移/转角\r\nSet, 1, 1\r\n".encode("cp936")
    path.write_bytes(original)

    read = await toolset.read_file(path.name)
    assert "encoding:cp936" in read
    assert "位移/转角" in read
    assert "�" not in read
    assert _hash(read) == hashlib.sha256(original).hexdigest()[:12]

    searched = await toolset.search_files("位移", path=path.name)
    assert "位移/转角" in searched
    assert "model.inp=cp936" in searched
    info = await toolset.file_info(path.name)
    assert "encoding: cp936" in info
    assert _hash(info) == _hash(read)

    edited = await toolset.edit_file(
        path.name,
        "位移/转角",
        "位移约束",
        expected_hash=_hash(read),
    )
    assert "encoding:cp936" in edited
    assert path.read_bytes().decode("cp936").startswith("** Name: BC Type: 位移约束")
    with pytest.raises(UnicodeDecodeError):
        path.read_text(encoding="utf-8")
    assert _hash(edited) == _hash(await toolset.read_file(path.name))


@pytest.mark.asyncio
async def test_write_preserves_existing_encoding_and_new_files_use_utf8(
    tmp_path: Path,
) -> None:
    toolset = _toolset(tmp_path)
    existing = tmp_path / "probe.txt"
    existing.write_bytes("已找到 Set".encode("cp936"))

    written = await toolset.write_file("probe.txt", "应当提供 Surface")
    assert "encoding:cp936" in written
    assert existing.read_bytes().decode("cp936") == "应当提供 Surface"

    created = await toolset.write_file("new.txt", "UTF-8 新文件")
    assert "encoding:utf-8" in created
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "UTF-8 新文件"


@pytest.mark.asyncio
async def test_unrepresentable_edit_and_stale_hash_leave_cp936_file_unchanged(
    tmp_path: Path,
) -> None:
    toolset = _toolset(tmp_path)
    path = tmp_path / "probe.txt"
    original = "中文内容".encode("cp936")
    path.write_bytes(original)
    content_hash = _hash(await toolset.read_file(path.name))

    with pytest.raises(ModelRetry, match="cannot be represented"):
        await toolset.edit_file(path.name, "内容", "内容🙂", expected_hash=content_hash)
    assert path.read_bytes() == original

    path.write_bytes("外部变化".encode("cp936"))
    with pytest.raises(ModelRetry, match="Conflict"):
        await toolset.edit_file(path.name, "变化", "修改", expected_hash=content_hash)
    assert path.read_bytes() == "外部变化".encode("cp936")


@pytest.mark.asyncio
async def test_unsupported_text_is_explicit_and_directory_search_reports_skips(
    tmp_path: Path,
) -> None:
    toolset = _toolset(tmp_path)
    (tmp_path / "broken.txt").write_bytes(b"\x81")
    (tmp_path / "valid.txt").write_text("needle", encoding="utf-8")

    with pytest.raises(ModelRetry, match="neither valid UTF-8 nor valid CP936"):
        await toolset.read_file("broken.txt")
    result = await toolset.search_files("needle")
    assert "valid.txt:1:needle" in result
    assert "1 unsupported text file" in result
