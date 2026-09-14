"""两种文档入口共用的正文交付边界，不参与召回或排序。"""

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.documents import Document

DOCUMENT_DELIVERY_VERSION = "indexed-passage-body-budget-v1"
DOCUMENT_BODY_BUDGET = 12_000
DOCUMENT_DELIVERY_NOTICE = (
    "Evidence contains indexed passages, not necessarily complete sections. "
    "truncated=false only means the indexed body was returned in full; it does not "
    "establish complete applicability conditions. truncated=true means the shared "
    "body budget was exhausted; do not treat the missing tail as absent conditions. "
    f"The shared body budget is {DOCUMENT_BODY_BUDGET} characters, not tokens, excluding metadata."
)


@dataclass(frozen=True)
class DocumentExcerpt:
    text: str
    original_characters: int
    truncated: bool


def document_body(document: Document) -> str:
    """仅移除能与metadata核对的生成前缀，保留正文换行及代码缩进。"""
    text = document.page_content
    lines = text.split("\n", 2)
    metadata = document.metadata
    if metadata.get("entity_kind") == "concept_chunk" and len(lines) == 3:
        sections = {str(metadata.get("section_title") or metadata.get("title") or "")}
        path = metadata.get("heading_path")
        if isinstance(path, list) and all(isinstance(item, str) for item in path):
            sections.add(" > ".join(path))
        if lines[0] == f"Document: {metadata.get('title', '')}" and any(
            lines[1] == f"Section: {section}" for section in sections
        ):
            text = lines[2]
    return text.strip("\r\n")


def render_document_bodies(
    documents: Sequence[Document], *, budget: int = DOCUMENT_BODY_BUDGET
) -> tuple[DocumentExcerpt, ...]:
    """按原排名分配一次调用的正文字符预算，不隐藏未获得正文额度的候选。

    Args:
        documents: 已排序的命中，不在此函数重排或扩展相邻内容。
        budget: 全部正文共用的字符上限，非模型token预算，不包含metadata。

    Returns:
        与输入等长、等序的正文及截断标记；预算耗尽后仍保留候选位置。

    Raises:
        ValueError: 预算为负数。

    Note:
        普通短片段完整返回。极长片段可能耗尽剩余预算，后续候选正文为空且
        明确标为截断。截断文本不添加伪原文省略号，由结构化字段表达。
    """
    if budget < 0:
        raise ValueError("body budget must not be negative")
    remaining = budget
    rendered: list[DocumentExcerpt] = []
    for document in documents:
        body = document_body(document)
        excerpt = body[:remaining]
        remaining -= len(excerpt)
        rendered.append(DocumentExcerpt(excerpt, len(body), len(excerpt) < len(body)))
    return tuple(rendered)
