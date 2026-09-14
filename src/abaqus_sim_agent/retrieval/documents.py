from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lxml.etree as etree
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_text_splitters import (
    HTMLHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

DEFAULT_GUIDES: dict[str, str] = {
    "SIMACAEMODRefMap": "Modeling",
    "SIMACAEANLRefMap": "Analysis",
    "SIMACAECSTRefMap": "Constraints",
    "SIMACAEELMRefMap": "Elements",
    "SIMACAEITNRefMap": "Interactions",
    "SIMACAEKEYRefMap": "Keywords",
    "SIMACAEMATRefMap": "Materials",
    "SIMACAEOUTRefMap": "Output",
    "SIMACAEPRCRefMap": "Prescribed Conditions",
}

_CLASS_TOKEN_XPATH = 'contains(concat(" ", normalize-space(@class), " "), " {token} ")'
_RELEASE_PATTERN = re.compile(r"\b20\d{2}\s+(?:GA|FD\d{2})\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_REFERENCE_TEXT_LIMIT = 1000
DOCUMENT_CONTEXT_VERSION = "abaqus-document-context-v2"


class AbaqusDocumentError(RuntimeError):
    pass


@dataclass
class LoaderStats:
    files_seen: int = 0
    files_loaded: int = 0
    files_skipped: int = 0
    documents_emitted: int = 0


class AbaqusDocumentLoader(BaseLoader):
    """把本地 Abaqus XHTML 转为带稳定来源信息的 LangChain Document。"""

    def __init__(
        self,
        docs_root: str | Path,
        *,
        docs_base_url: str = "http://localhost:4040/English",
        guide_ids: Sequence[str] | None = None,
        max_chunk_size: int = 1000,
    ) -> None:
        self.docs_root = _resolve_language_root(Path(docs_root))
        self.docs_base_url = docs_base_url.rstrip("/")
        self.guide_ids = tuple(guide_ids or DEFAULT_GUIDES)
        if max_chunk_size < 1:
            raise ValueError("max_chunk_size must be positive")
        self.max_chunk_size = max_chunk_size
        self.release = _detect_release(self.docs_root)
        self.stats = LoaderStats()
        self._parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            recover=False,
        )
        self._section_splitter = HTMLHeaderTextSplitter(
            headers_to_split_on=[("h1", "h1"), ("h2", "h2"), ("h3", "h3")],
        )
        self._length_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=min(100, max_chunk_size // 10),
        )
        unknown_guides = set(self.guide_ids).difference(DEFAULT_GUIDES)
        if unknown_guides:
            names = ", ".join(sorted(unknown_guides))
            raise ValueError(f"unsupported Abaqus guide ids: {names}")

    def lazy_load(self) -> Iterator[Document]:
        self.stats = LoaderStats()
        for guide_id in self.guide_ids:
            guide_dir = self.docs_root / guide_id
            if not guide_dir.is_dir():
                raise AbaqusDocumentError(
                    f"Abaqus guide directory is missing: {guide_dir}"
                )
            for path in sorted(guide_dir.glob("*.htm")):
                self.stats.files_seen += 1
                if path.name.casefold() == "sitemap.htm":
                    self.stats.files_skipped += 1
                    continue
                documents = self._load_page(path, guide_id)
                if not documents:
                    self.stats.files_skipped += 1
                    continue
                self.stats.files_loaded += 1
                self.stats.documents_emitted += len(documents)
                yield from documents

    def _load_page(self, path: Path, guide_id: str) -> list[Document]:
        try:
            tree = etree.parse(str(path), self._parser)
        except (OSError, etree.XMLSyntaxError) as exc:
            raise AbaqusDocumentError(f"failed to parse Abaqus XHTML: {path}") from exc

        document_id = _xpath_string(tree, './/meta[@name="DC.identifier"]/@content')
        title = _normalized_text(_xpath_string(tree, ".//title"))
        body_id = _xpath_string(tree, ".//body/@id")
        if not document_id or not title or body_id != document_id:
            raise AbaqusDocumentError(
                f"Abaqus page lacks a stable identifier/title contract: {path}"
            )

        relative_path = path.relative_to(self.docs_root).as_posix()
        base_metadata: dict[str, Any] = {
            "document_context_version": DOCUMENT_CONTEXT_VERSION,
            "document_id": document_id,
            "release": self.release,
            "dc_type": (
                _xpath_string(tree, './/meta[@name="DC.type"]/@content') or "unknown"
            ).casefold(),
            "guide_id": guide_id,
            "guide": DEFAULT_GUIDES[guide_id],
            "title": title,
            "source_path": relative_path,
            "source_url": f"{self.docs_base_url}/{relative_path}",
        }
        products = _product_texts(tree)
        product_source = "official_page_header" if products else "unknown"
        if not products:
            products = _inferred_products(document_id)
            if products:
                product_source = "document_id_inference"
        base_metadata["products_source"] = product_source
        base_metadata["products_scope"] = "page"
        if products:
            base_metadata["products"] = products

        documents: list[Document] = []
        summary = _summary_document(tree, base_metadata)
        if summary is not None:
            documents.append(summary)
        documents.extend(self._split_page(tree, base_metadata))
        if guide_id == "SIMACAEKEYRefMap":
            documents.extend(self._keyword_documents(tree, base_metadata))
        elif guide_id == "SIMACAEELMRefMap":
            documents.extend(
                self._definition_documents(
                    tree,
                    base_metadata,
                    semantic_class="abqelement",
                    entity_kind="element",
                )
            )
        elif guide_id == "SIMACAEOUTRefMap":
            documents.extend(
                self._definition_documents(
                    tree,
                    base_metadata,
                    semantic_class="abqoutputvar",
                    entity_kind="output_variable",
                )
            )
        return _deduplicate_documents(documents)

    def _split_page(
        self,
        tree: etree._ElementTree,
        base_metadata: dict[str, Any],
    ) -> list[Document]:
        content_roots = _content_roots(tree)
        if not content_roots:
            return []

        documents: list[Document] = []
        chunk_number = 0
        for content_root in content_roots:
            html = _html_with_complete_headings(content_root)
            sections = self._section_splitter.split_text(html)
            for chunk in self._length_splitter.split_documents(sections):
                content = chunk.page_content.strip()
                if not content:
                    continue
                chunk_number += 1
                section_title = _last_heading(chunk.metadata)
                section_id = _nearest_id(
                    content_root,
                    str(base_metadata["document_id"]),
                )
                display_section = section_title or str(base_metadata["title"])
                heading_path = _heading_path(
                    str(base_metadata["title"]),
                    [
                        str(chunk.metadata[key])
                        for key in ("h1", "h2", "h3")
                        if chunk.metadata.get(key)
                    ],
                )
                embedding_text = (
                    f"Document: {base_metadata['title']}\n"
                    f"Section: {' > '.join(heading_path)}\n{content}"
                )
                metadata = dict(base_metadata)
                metadata.update(chunk.metadata)
                metadata.update(_heading_metadata(heading_path))
                metadata.update(
                    {
                        "entity_kind": "concept_chunk",
                        "retrieval_role": "semantic",
                        "section_id": section_id,
                        "section_title": display_section,
                        "source_id": (
                            f"{base_metadata['release']}:"
                            f"{base_metadata['document_id']}#{section_id}"
                        ),
                        "source_url": f"{base_metadata['source_url']}#{section_id}",
                    }
                )
                document_id = (
                    f"{base_metadata['document_id']}#{section_id}:"
                    f"chunk-{chunk_number:03d}"
                )
                documents.append(
                    Document(
                        id=document_id,
                        page_content=embedding_text,
                        metadata=metadata,
                    )
                )
        return documents

    def _keyword_documents(
        self,
        tree: etree._ElementTree,
        base_metadata: dict[str, Any],
    ) -> list[Document]:
        documents: list[Document] = []
        title = str(base_metadata["title"])
        if title.startswith("*"):
            abstract = _xpath_string(tree, './/meta[@name="abstract"]/@content')
            keygroup_nodes = tree.xpath(
                f".//*[{_CLASS_TOKEN_XPATH.format(token='keygroup')}]"
            )
            keygroup = (
                _normalized_text(" ".join(_iter_text(keygroup_nodes[0])))
                if keygroup_nodes
                else ""
            )
            content = "\n".join(part for part in (title, abstract, keygroup) if part)
            metadata = _reference_metadata(
                base_metadata,
                entity_kind="keyword",
                symbol=title,
                section_id=str(base_metadata["document_id"]),
                section_title=title,
            )
            documents.append(
                Document(
                    id=f"{base_metadata['document_id']}#keyword",
                    page_content=content,
                    metadata=metadata,
                )
            )
        documents.extend(
            self._definition_documents(
                tree,
                base_metadata,
                semantic_class="abqparameter",
                entity_kind="keyword_parameter",
            )
        )
        return documents

    def _definition_documents(
        self,
        tree: etree._ElementTree,
        base_metadata: dict[str, Any],
        *,
        semantic_class: str,
        entity_kind: str,
    ) -> list[Document]:
        token = _CLASS_TOKEN_XPATH.format(token=semantic_class)
        terms = tree.xpath(f".//dt[.//*[{token}]]")
        documents: list[Document] = []
        seen: set[tuple[str, str]] = set()
        for index, term in enumerate(terms, start=1):
            symbol_nodes = term.xpath(f".//*[{token}]")
            if not symbol_nodes:
                continue
            symbol_node = symbol_nodes[0]
            symbol = _normalized_symbol(" ".join(_iter_text(symbol_node)))
            if not symbol:
                continue
            description = term.getnext()
            description_text = (
                _normalized_text(" ".join(_iter_text(description)))
                if description is not None and description.tag == "dd"
                else ""
            )
            section_title, section_id = _preceding_heading(
                term, base_metadata["document_id"]
            )
            semantic_id = symbol_node.get("id") or section_id
            identity = (symbol, semantic_id)
            if identity in seen:
                continue
            seen.add(identity)
            content = "\n".join(
                part
                for part in (
                    f"Document: {base_metadata['title']}",
                    f"Section: {section_title}",
                    f"Symbol: {symbol}",
                    _truncate_reference_text(description_text),
                )
                if part
            )
            metadata = _reference_metadata(
                base_metadata,
                entity_kind=entity_kind,
                symbol=symbol,
                section_id=semantic_id,
                section_title=section_title,
            )
            metadata.update(
                _heading_metadata(
                    _reference_heading_path(
                        term, str(base_metadata["title"]), section_title
                    )
                )
            )
            documents.append(
                Document(
                    id=(
                        f"{base_metadata['document_id']}#{semantic_id}:"
                        f"{entity_kind}-{index:03d}"
                    ),
                    page_content=content,
                    metadata=metadata,
                )
            )
        return documents


def _resolve_language_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    english = path / "English"
    root = english if english.is_dir() else path
    if not root.is_dir():
        raise ValueError(f"Abaqus documentation root does not exist: {root}")
    return root


def _html_with_complete_headings(root: etree._Element) -> str:
    """在副本中展平标题的内嵌标签，避免 splitter 丢掉 span 等标签内的文字。

    Note:
        仅改传给切分器的副本；原始树、锚点和后续原子reference解析不受影响。
        正文和代码块保留原结构，不把标题修复扩展为通用HTML改写。
    """
    copied = deepcopy(root)
    for heading in copied.xpath("self::h1|self::h2|self::h3|.//h1|.//h2|.//h3"):
        text = _normalized_text("".join(heading.itertext()))
        for child in list(heading):
            heading.remove(child)
        heading.text = text
    return etree.tostring(copied, encoding="unicode", method="html")


def _heading_path(page_title: str, headings: Sequence[str]) -> list[str]:
    path = [page_title]
    for text in headings:
        text = _normalized_text(text)
        if text and text != path[-1]:
            path.append(text)
    return path


def _heading_metadata(path: Sequence[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"heading_path": list(path)}
    # This is a heading-text hint. It must not overwrite the page's Products field.
    for heading in reversed(path[1:]):
        mentions = [
            product
            for product in ("Abaqus/Standard", "Abaqus/Explicit")
            if product.casefold() in heading.casefold()
        ]
        if mentions:
            metadata["section_product_mentions"] = mentions
            metadata["section_product_heading"] = heading
            break
    return metadata


def _reference_heading_path(
    element: etree._Element, page_title: str, section_title: str
) -> list[str]:
    headings: list[str] = []
    for ancestor in reversed(list(element.iterancestors())):
        if etree.QName(ancestor).localname not in {"article", "section"}:
            continue
        direct = ancestor.xpath("./h1|./h2|./h3")
        if direct:
            headings.append(_normalized_text("".join(direct[0].itertext())))
    headings.append(section_title)
    return _heading_path(page_title, headings)


def _content_roots(tree: etree._ElementTree) -> list[etree._Element]:
    body_token = _CLASS_TOKEN_XPATH.format(token="body")
    section_token = _CLASS_TOKEN_XPATH.format(token="section")
    keygroup_token = _CLASS_TOKEN_XPATH.format(token="keygroup")
    roots = tree.xpath(
        f".//body//div[{body_token} and not(ancestor::article)]"
        " | .//body//article[not(ancestor::article)]"
        f" | .//body//div[{section_token} and not({keygroup_token})"
        f" and not(ancestor::article) and not(ancestor::div[{body_token}])]"
    )
    return [
        root
        for root in roots
        if isinstance(root, etree._Element) and not _is_metadata_only_body(root)
    ]


def _is_metadata_only_body(element: etree._Element) -> bool:
    if not _element_has_class(element, "body"):
        return False
    children = [
        child
        for child in element
        if isinstance(child.tag, str)
        and etree.QName(child).localname not in {"script", "style"}
    ]
    return bool(children) and all(
        _element_has_class(child, "keygroup") for child in children
    )


def _element_has_class(element: etree._Element, token: str) -> bool:
    return token in str(element.get("class", "")).split()


def _detect_release(root: Path) -> str:
    legal = (
        root
        / "SIMULIA_Established_FrontmatterMap"
        / "sim-r-SIMULIA_EstablishedLegal.htm"
    )
    if not legal.is_file():
        return "unknown"
    text = legal.read_text(encoding="utf-8", errors="replace")
    match = _RELEASE_PATTERN.search(text)
    return match.group(0) if match else "unknown"


def _summary_document(
    tree: etree._ElementTree,
    base_metadata: dict[str, Any],
) -> Document | None:
    abstract = _xpath_string(tree, './/meta[@name="abstract"]/@content')
    if not abstract:
        abstract_nodes = tree.xpath(
            f".//p[{_CLASS_TOKEN_XPATH.format(token='abstract')}][1]"
        )
        if abstract_nodes:
            abstract = _normalized_text(" ".join(_iter_text(abstract_nodes[0])))
    abstract = _truncate_reference_text(
        abstract.split("This page discusses:", maxsplit=1)[0].strip()
    )
    if not abstract:
        return None

    document_id = str(base_metadata["document_id"])
    metadata = dict(base_metadata)
    metadata.update(
        {
            "entity_kind": "page_summary",
            "retrieval_role": "semantic",
            "section_id": document_id,
            "section_title": base_metadata["title"],
            "heading_path": [str(base_metadata["title"])],
            "source_id": f"{base_metadata['release']}:{document_id}#{document_id}",
            "source_url": f"{base_metadata['source_url']}#{document_id}",
        }
    )
    return Document(
        id=f"{document_id}#summary",
        page_content=(f"Document: {base_metadata['title']}\nSummary: {abstract}"),
        metadata=metadata,
    )


def _reference_metadata(
    base_metadata: dict[str, Any],
    *,
    entity_kind: str,
    symbol: str,
    section_id: str,
    section_title: str,
) -> dict[str, Any]:
    metadata = dict(base_metadata)
    metadata.update(
        {
            "entity_kind": entity_kind,
            "retrieval_role": "exact",
            "primary_symbol": symbol,
            "symbols": [symbol],
            "section_id": section_id,
            "section_title": section_title,
            "source_id": (
                f"{base_metadata['release']}:"
                f"{base_metadata['document_id']}#{section_id}"
            ),
            "source_url": f"{base_metadata['source_url']}#{section_id}",
        }
    )
    metadata.update(
        _heading_metadata(_heading_path(str(base_metadata["title"]), [section_title]))
    )
    return metadata


def _nearest_id(element: etree._Element, fallback: str) -> str:
    owner: etree._Element | None = element
    while owner is not None:
        tag = etree.QName(owner).localname
        if owner.get("id") and (owner is element or tag in {"article", "body"}):
            return str(owner.get("id"))
        owner = owner.getparent()
    return fallback


def _preceding_heading(element: etree._Element, fallback_id: Any) -> tuple[str, str]:
    headings = element.xpath("preceding::*[self::h1 or self::h2 or self::h3][1]")
    if not headings:
        return str(fallback_id), str(fallback_id)
    heading = headings[0]
    title = _normalized_text(" ".join(_iter_text(heading)))
    owner = heading
    while owner is not None and not owner.get("id"):
        owner = owner.getparent()
    section_id = (
        str(owner.get("id"))
        if owner is not None and owner.get("id")
        else str(fallback_id)
    )
    return title or str(fallback_id), section_id


def _last_heading(metadata: dict[str, Any]) -> str:
    for key in ("h3", "h2", "h1"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _normalized_text(value)
    return ""


def _deduplicate_documents(documents: Sequence[Document]) -> list[Document]:
    unique: dict[str, Document] = {}
    for document in documents:
        if document.id is None:
            raise AbaqusDocumentError("retrieval document is missing an id")
        unique.setdefault(document.id, document)
    return list(unique.values())


def _xpath_string(tree: etree._ElementTree, expression: str) -> str:
    value = tree.xpath(f"string({expression})")
    return str(value).strip()


def _product_texts(tree: etree._ElementTree) -> list[str]:
    parents = tree.xpath(f".//*[{_CLASS_TOKEN_XPATH.format(token='abqproductname')}]")
    values: list[str] = []
    for parent in parents:
        nodes = parent.xpath(f".//*[{_CLASS_TOKEN_XPATH.format(token='key')}]")
        for node in nodes:
            value = _normalized_text(" ".join(_iter_text(node)))
            if value and value not in values:
                values.append(value)
    return values


def _inferred_products(document_id: str) -> list[str]:
    marker = document_id.casefold()
    products: list[str] = []
    if "-std-" in marker or marker.endswith("-std"):
        products.append("Abaqus/Standard")
    if "-exp-" in marker or marker.endswith("-exp"):
        products.append("Abaqus/Explicit")
    return products


def _iter_text(element: etree._Element) -> Iterator[str]:
    yield from (str(value) for value in element.itertext())


def _normalized_text(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _normalized_symbol(value: str) -> str:
    symbol = _normalized_text(value).upper()
    return re.sub(r"\s*\((?:S|E)\)$", "", symbol).strip()


def _truncate_reference_text(value: str) -> str:
    if len(value) <= _REFERENCE_TEXT_LIMIT:
        return value
    return f"{value[: _REFERENCE_TEXT_LIMIT - 1].rstrip()}…"
