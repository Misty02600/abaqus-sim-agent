from __future__ import annotations

import re
from collections.abc import Sequence

import lxml.etree as etree

from .types import Document

_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLOCK_TAGS = frozenset(
    {
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)


def split_html_sections(html: str) -> list[Document]:
    root = etree.HTML(html)
    if root is None:
        return []
    sections: list[Document] = []
    headings: dict[str, str] = {}
    fragments: list[str] = []

    def flush() -> None:
        text = _normalize_fragments(fragments)
        fragments.clear()
        if text:
            sections.append(Document(page_content=text, metadata=dict(headings)))

    def walk(element: etree._Element) -> None:
        tag = etree.QName(element).localname.casefold()
        if tag in {"h1", "h2", "h3"}:
            flush()
            level = int(tag[1])
            headings[tag] = _normalize_inline("".join(element.itertext()))
            for deeper in range(level + 1, 4):
                headings.pop(f"h{deeper}", None)
            return
        if element.text:
            fragments.append(element.text)
        for child in element:
            walk(child)
            if child.tail:
                fragments.append(child.tail)
        if tag in _BLOCK_TAGS:
            fragments.append("\n")

    body = root.find("body")
    walk(body if body is not None else root)
    flush()
    return sections


def split_documents_by_length(
    documents: Sequence[Document],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[Document]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be between zero and chunk_size")
    return [
        Document(page_content=text, metadata=dict(document.metadata))
        for document in documents
        for text in _chunk_text(document.page_content, chunk_size, chunk_overlap)
    ]


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        limit = min(start + size, len(text))
        end = limit
        if limit < len(text):
            floor = start + size // 2
            boundary = max(
                text.rfind(separator, floor, limit) for separator in ("\n\n", "\n", " ")
            )
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
        while start < len(text) and text[start].isspace():
            start += 1
    return chunks


def _normalize_fragments(fragments: Sequence[str]) -> str:
    return "\n".join(
        normalized
        for line in "".join(fragments).splitlines()
        if (normalized := _normalize_inline(line))
    )


def _normalize_inline(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()
