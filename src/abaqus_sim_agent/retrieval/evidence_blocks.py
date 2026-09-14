"""完整证据块与token受限检索片段；尚未接入默认Agent工具。"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import lxml.etree as etree
from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, model_validator
from tokenizers import Tokenizer


class EvidenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    parent_id: str
    document_id: str
    title: str
    release: str
    heading_path: tuple[str, ...]
    source: str
    source_sha256: str
    xpath: str
    anchor: str
    kind: str
    body: str
    search_text: str
    search_context: str = ""
    atomic_search_ends: tuple[int, ...] = ()
    projection_status: Literal[
        "text", "deferred_math", "deferred_table", "deferred_list"
    ]
    source_role: Literal[
        "unclassified", "content", "metadata", "navigation", "equation_context"
    ] = "unclassified"

    @model_validator(mode="after")
    def check_search_boundaries(self) -> "EvidenceBlock":
        if self.atomic_search_ends and (
            self.atomic_search_ends[-1] != len(self.search_text)
            or any(
                a >= b
                for a, b in zip(
                    (0, *self.atomic_search_ends[:-1]),
                    self.atomic_search_ends,
                    strict=True,
                )
            )
        ):
            raise ValueError("atomic search boundaries must partition the projection")
        return self


def _plain_xml(node: etree._Element) -> str:
    parts = [node.text or ""]
    for child in node:
        if not isinstance(child.tag, str):
            parts.append(child.tail or "")
            continue
        tag = etree.QName(child).localname
        if tag not in {"script", "style"}:
            parts.append(_plain_xml(child))
        if tag in {"p", "pre", "br", "li", "div", "dd", "dt"}:
            parts.append("\n")
        parts.append(child.tail or "")
    return "".join(parts)


def _flat_list_projection(body: str) -> str | None:
    xml = body.split("[ListXML]\n", 1)[1].removesuffix("\n[/ListXML]")
    root = etree.fromstring(
        xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True)
    )
    tag = etree.QName(root).localname
    if tag not in {"ul", "ol"} or any(
        etree.QName(node).localname in {"ul", "ol", "table", "pre", "dl", "math"}
        for node in root.iterdescendants()
        if isinstance(node.tag, str)
    ):
        return None
    items = [node for node in root if isinstance(node.tag, str)]
    if (root.text or "").strip() or any(
        etree.QName(node).localname != "li" or (node.tail or "").strip()
        for node in items
    ):
        return None
    if tag == "ol" and root.get("type", "1") != "1":
        return None
    reverse = "reversed" in root.attrib
    try:
        number = int(root.get("start", str(len(items) if reverse else 1)))
        lines = []
        for item in items:
            if tag == "ol" and item.get("value") is not None:
                number = int(str(item.get("value")))
            marker = f"{number}." if tag == "ol" else "-"
            lines.append(f"{marker} {_plain_xml(item).strip()}")
            number += -1 if reverse else 1
    except ValueError:
        return None
    return "\n".join(lines)


def retrieval_projection(
    body: str, kind: str
) -> tuple[str, Literal["text", "deferred_math", "deferred_table", "deferred_list"]]:
    """仅为已验证的文本结构生成检索视图；不把复杂XML冒充自然语言。"""
    if kind == "table" or "[TableXML]" in body:
        return "", "deferred_table"
    if (
        kind == "math"
        or "[MathML]" in body
        or re.search(r"<(?:\w+:)?math(?:\s|>)", body)
    ):
        return "", "deferred_math"
    if "[ListXML]" in body:
        text = _flat_list_projection(body)
        return (text, "text") if text is not None else ("", "deferred_list")
    if "[DefinitionXML]" in body:
        label, xml = body.split("[DefinitionXML]\n", 1)
        xml = xml.removesuffix("\n[/DefinitionXML]")
        root = etree.fromstring(
            xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True)
        )
        if any(
            etree.QName(node).localname == "table"
            for node in root.iter()
            if isinstance(node.tag, str)
        ):
            return "", "deferred_table"
        return label + _plain_xml(root), "text"
    return body, "text"


def make_evidence_block(
    *,
    document_id: str,
    title: str,
    release: str,
    heading_path: tuple[str, ...],
    source: str,
    source_sha256: str,
    xpath: str,
    anchor: str,
    kind: str,
    body: str,
    ordinal: int,
) -> EvidenceBlock:
    projection, status = retrieval_projection(body, kind)
    identity = json.dumps(
        [document_id, release, source_sha256, xpath, ordinal, kind, body],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return EvidenceBlock(
        parent_id=hashlib.sha256(identity.encode()).hexdigest(),
        document_id=document_id,
        title=title,
        release=release,
        heading_path=heading_path,
        source=source,
        source_sha256=source_sha256,
        xpath=xpath,
        anchor=anchor,
        kind=kind,
        body=body,
        search_text=projection,
        projection_status=status,
    )


def _child_prefix(parent: EvidenceBlock) -> str:
    prefix = f"Document: {parent.title}\nSection: {' > '.join(parent.heading_path)}\n"
    if parent.kind == "definition":
        prefix += f"Entry: {parent.search_text.splitlines()[0]}\n"
    prefix += parent.search_context
    return prefix


class TokenBoundChildren:
    def __init__(self, tokenizer: Tokenizer, *, max_tokens: int = 256) -> None:
        if max_tokens < 4:
            raise ValueError("max_tokens must be at least four")
        self.tokenizer = Tokenizer.from_str(tokenizer.to_str())
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.max_tokens = max_tokens

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text).ids)

    def split(self, parent: EvidenceBlock) -> list[Document]:
        """使用实际编码长度生成无缺口、无重叠的检索切片。

        Note:
            标题/章节和特殊token均计入上限；优先句末或换行，其次词间边界，
            超长单词才使用字符边界。偏移属于search_text，不是HTML字节偏移。
            复杂结构暂不生成子片段；标题本身过长时显式失败，不静默删标题。
        """
        if parent.source_role in {"metadata", "navigation", "equation_context"}:
            return []
        if parent.projection_status != "text" or not parent.search_text:
            return []
        prefix = _child_prefix(parent)
        if self.count(prefix) >= self.max_tokens:
            raise ValueError("heading context exhausts token budget")
        text = parent.search_text
        start = 0
        result = []
        while start < len(text):
            if parent.atomic_search_ends:
                fits = [
                    end
                    for end in parent.atomic_search_ends
                    if end > start
                    and self.count(prefix + text[start:end]) <= self.max_tokens
                ]
                if not fits:
                    raise ValueError("atomic row and context exhaust token budget")
                end = max(fits)
                result.append(self._document(parent, prefix, start, end))
                start = end
                continue
            lo, hi = start + 1, len(text)
            end = start
            while lo <= hi:
                mid = (lo + hi) // 2
                if self.count(prefix + text[start:mid]) <= self.max_tokens:
                    end, lo = mid, mid + 1
                else:
                    hi = mid - 1
            if end == start:
                raise ValueError("no body character fits token budget")
            if end < len(text):
                minimum_boundary = (end - start) // 2
                boundaries = [
                    m
                    for m in re.finditer(r"(?<=[.!?])\s+|\n+", text[start:end])
                    if m.end() >= minimum_boundary
                ]
                if not boundaries:
                    boundaries = list(re.finditer(r"\s+", text[start:end]))
                if boundaries:
                    end = start + boundaries[-1].end()
            content = prefix + text[start:end]
            # BPE token counts need not be strictly monotonic with character length.
            while self.count(content) > self.max_tokens and end > start:
                end -= 1
                content = prefix + text[start:end]
            if end <= start:
                raise ValueError("no progress while splitting body")
            result.append(self._document(parent, prefix, start, end))
            start = end
        return result

    def _document(
        self, parent: EvidenceBlock, prefix: str, start: int, end: int
    ) -> Document:
        content = prefix + parent.search_text[start:end]
        return Document(
            id=f"{parent.parent_id}:{start}:{end}",
            page_content=content,
            metadata={
                "parent_id": parent.parent_id,
                "document_id": parent.document_id,
                "title": parent.title,
                "release": parent.release,
                "heading_path": list(parent.heading_path),
                "section_title": parent.heading_path[-1]
                if parent.heading_path
                else parent.title,
                "section_id": parent.anchor,
                "source_url": parent.source,
                "source_sha256": parent.source_sha256,
                "source_xpath": parent.xpath,
                "search_start": start,
                "search_end": end,
                "token_count": self.count(content),
                "entity_kind": "retrieval_child",
                "retrieval_role": "semantic",
                "offset_scope": "retrieval_projection_characters",
            },
        )


def scoring_document(child: Document, parents: Mapping[str, EvidenceBlock]) -> Document:
    parent = parents[str(child.metadata["parent_id"])]
    if parent.source_role in {"metadata", "navigation", "equation_context"}:
        raise ValueError("parent is not searchable under its source role")
    start, end = int(child.metadata["search_start"]), int(child.metadata["search_end"])
    expected = _child_prefix(parent) + parent.search_text[start:end]
    if (
        not (0 <= start < end <= len(parent.search_text))
        or (
            parent.atomic_search_ends
            and (
                start not in (0, *parent.atomic_search_ends)
                or end not in parent.atomic_search_ends
            )
        )
        or child.page_content != expected
        or child.id != f"{parent.parent_id}:{start}:{end}"
        or child.metadata.get("source_sha256") != parent.source_sha256
        or child.metadata.get("document_id") != parent.document_id
        or child.metadata.get("heading_path") != list(parent.heading_path)
        or child.metadata.get("title") != parent.title
        or child.metadata.get("release") != parent.release
    ):
        raise ValueError("child projection does not match parent")
    scoring_text = parent.search_context + parent.search_text[start:end]
    if parent.kind == "definition":
        scoring_text = f"Entry: {parent.search_text.splitlines()[0]}\n" + scoring_text
    return Document(
        id=child.id,
        page_content=scoring_text,
        metadata=dict(child.metadata),
    )


def expand_ranked_children(
    children: Sequence[Document],
    parents: Mapping[str, EvidenceBlock],
    *,
    top_k: int = 4,
) -> list[Document]:
    """按排序结果返回完整父块，同一父块只返回一次，不扩展到整页。"""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    selected = []
    seen = set()
    for child in children:
        scoring_document(child, parents)
        parent = parents[str(child.metadata["parent_id"])]
        if parent.parent_id in seen:
            continue
        seen.add(parent.parent_id)
        selected.append(
            Document(
                id=parent.parent_id,
                page_content=parent.body,
                metadata={
                    "document_id": parent.document_id,
                    "title": parent.title,
                    "release": parent.release,
                    "heading_path": list(parent.heading_path),
                    "section_id": parent.anchor,
                    "section_title": parent.heading_path[-1]
                    if parent.heading_path
                    else parent.title,
                    "source_url": parent.source,
                    "source_xpath": parent.xpath,
                    "source_sha256": parent.source_sha256,
                    "entity_kind": "source_block",
                    "parent_id": parent.parent_id,
                    "matched_child_id": child.id,
                },
            )
        )
        if len(selected) == top_k:
            break
    return selected


def write_parent_store(path: Path, parents: Sequence[EvidenceBlock]) -> str:
    if len({p.parent_id for p in parents}) != len(parents):
        raise ValueError("duplicate parent IDs")
    payload = "".join(parent.model_dump_json() + "\n" for parent in parents).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
    return hashlib.sha256(payload).hexdigest()


def read_parent_store(path: Path, *, expected_sha256: str) -> dict[str, EvidenceBlock]:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("parent store hash mismatch")
    parents = [
        EvidenceBlock.model_validate_json(line) for line in payload.splitlines() if line
    ]
    if len({p.parent_id for p in parents}) != len(parents):
        raise ValueError("duplicate parent IDs")
    return {parent.parent_id: parent for parent in parents}
