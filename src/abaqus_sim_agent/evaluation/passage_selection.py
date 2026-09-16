"""离线片段选择实验，不注册产品工具或修改默认检索策略。"""

from collections.abc import Sequence

from abaqus_sim_agent.retrieval import RetrievalHit
from abaqus_sim_agent.retrieval.types import Document


def fuse_passage_candidates(
    dense: Sequence[Document],
    sparse: Sequence[Document],
    *,
    top_k: int = 4,
    rrf_k: int = 60,
) -> list[RetrievalHit]:
    """按片段ID融合两个排名，允许同页不同片段进入最终结果。

    Args:
        dense: 已按Dense排名排序的有界候选。
        sparse: 已按BM25排名排序的有界候选。
        top_k: 返回条数，不扩大输入候选集合。
        rrf_k: 固定RRF平滑常数。

    Returns:
        带融合分数的片段；同分按最佳单路排名和片段ID排序。

    Raises:
        ValueError: 参数非正、缺少片段ID或同ID对应不同内容。
    """
    if top_k < 1 or rrf_k < 1:
        raise ValueError("top_k and rrf_k must be positive")
    documents: dict[str, Document] = {}
    scores: dict[str, float] = {}
    best: dict[str, int] = {}
    for source in (dense, sparse):
        seen: set[str] = set()
        for document in source:
            if not document.id:
                raise ValueError("candidate must have a record ID")
            key = document.id
            if key in documents and (
                documents[key].page_content != document.page_content
                or documents[key].metadata != document.metadata
            ):
                raise ValueError("conflicting records with the same ID")
            if key in seen:
                continue
            seen.add(key)
            rank = len(seen)
            documents[key] = document
            scores[key] = scores.get(key, 0.0) + 1 / (rrf_k + rank)
            best[key] = min(best.get(key, rank), rank)
    ranked = sorted(scores, key=lambda key: (-scores[key], best[key], key))
    return [
        RetrievalHit(document=documents[key], match_type="hybrid", score=scores[key])
        for key in ranked[:top_k]
    ]


def body_first_excerpt(document: Document, *, limit: int = 600) -> str:
    """仅剥离可核对的索引生成前缀，再按原字符预算返回正文。

    Note:
        只处理concept_chunk的Document/Section前缀；标题和章节路径仍由调用方
        单独返回。不改原记录、真实正文、summary或原子符号卡，也不保证完整句子。
    """
    if limit < 1:
        raise ValueError("limit must be positive")
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
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"
