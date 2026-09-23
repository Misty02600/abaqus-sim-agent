from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TEXT_ENCODING_POLICY_VERSION = "utf8-cp936-preserve-v1"

_COOKIE = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)")
_UTF8_NAMES = frozenset({"utf8", "utf-8", "utf_8"})
_CP936_NAMES = frozenset({"mbcs", "cp936", "gbk", "gb2312"})


class UnsupportedTextEncodingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str


def decode_workspace_text(path: Path, raw: bytes) -> DecodedText:
    """无损解码当前 Windows/Abaqus 工作区支持的 UTF-8 或 CP936 文本。

    Args:
        path: 用于错误说明和 Python coding cookie 解释的文件路径。
        raw: 文件原始字节。

    Returns:
        解码后的文本及用于无损写回的规范编码名。

    Raises:
        UnsupportedTextEncodingError: 声明了不支持的编码，或两种支持编码均无法解码。

    Note:
        空文件和纯 ASCII 文件规范为 UTF-8。Abaqus replay/recovery 文件常声明
        ``mbcs``，在本项目固定的中文 Windows runtime 中按 CP936 处理。
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return DecodedText(raw.decode("utf-8-sig"), "utf-8-sig")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnsupportedTextEncodingError(
            f"{path.name} uses unsupported UTF-16 text; supported encodings are UTF-8 and CP936"
        )

    declared = _declared_python_encoding(path, raw)
    if declared is not None:
        encoding = _normalize_declared_encoding(declared, path)
        return DecodedText(_strict_decode(raw, encoding, path), encoding)

    try:
        return DecodedText(raw.decode("utf-8"), "utf-8")
    except UnicodeDecodeError:
        try:
            return DecodedText(raw.decode("cp936"), "cp936")
        except UnicodeDecodeError as exc:
            raise UnsupportedTextEncodingError(
                f"{path.name} is neither valid UTF-8 nor valid CP936 text"
            ) from exc


def encode_workspace_text(path: Path, text: str, encoding: str) -> bytes:
    """按已选编码无损编码文本；不以替换字符掩盖无法表示的内容。"""
    try:
        return text.encode(encoding)
    except (LookupError, UnicodeEncodeError) as exc:
        raise UnsupportedTextEncodingError(
            f"Edited text for {path.name} cannot be represented as {encoding}"
        ) from exc


def _declared_python_encoding(path: Path, raw: bytes) -> str | None:
    if path.suffix.casefold() not in {".py", ".rpy", ".rec"}:
        return None
    for line in raw.splitlines()[:2]:
        match = _COOKIE.match(line)
        if match is not None:
            return match.group(1).decode("ascii").casefold()
    return None


def _normalize_declared_encoding(name: str, path: Path) -> str:
    if name in _UTF8_NAMES:
        return "utf-8"
    if name in _CP936_NAMES:
        return "cp936"
    raise UnsupportedTextEncodingError(
        f"{path.name} declares unsupported text encoding {name!r}; supported encodings are UTF-8 and CP936"
    )


def _strict_decode(raw: bytes, encoding: str, path: Path) -> str:
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise UnsupportedTextEncodingError(
            f"{path.name} is not valid {encoding} despite its coding declaration"
        ) from exc
