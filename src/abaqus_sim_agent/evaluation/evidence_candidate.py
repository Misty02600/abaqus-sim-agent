"""独立候选索引的分阶段构建与development回放；绝不替换产品默认索引。"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import lxml.etree as etree
import numpy as np
from tokenizers import Tokenizer

from abaqus_sim_agent.coding.concept_lookup import _evidence_payloads
from abaqus_sim_agent.retrieval import RetrievalHit
from abaqus_sim_agent.retrieval.bm25 import BM25DocumentIndex, semantic_corpus_sha256
from abaqus_sim_agent.retrieval.evidence_blocks import (
    TokenBoundChildren,
    expand_ranked_children,
    make_evidence_block,
    scoring_document,
)
from abaqus_sim_agent.retrieval.evidence_units import (
    build_evidence_units,
    read_unit_store,
    write_unit_store,
)
from abaqus_sim_agent.retrieval.evidence_windows import source_role
from abaqus_sim_agent.retrieval.flat_store import FlatDocumentStore
from abaqus_sim_agent.retrieval.minilm import MiniLMEncoder
from abaqus_sim_agent.retrieval.reproduction import dense_vector_storage_snapshot
from abaqus_sim_agent.retrieval.structured_projection import (
    HEADERLESS_PAIR_VERSION,
    PROJECTION_VERSION,
    project_evidence_unit,
)
from abaqus_sim_agent.retrieval.types import Document

from .cross_encoder import OnnxPassageReranker, candidate_union
from .structural_chunking import STRUCTURAL_PARSER_VERSION, extract_source_blocks

COLLECTION = "abaqus_evidence_candidate_v1"
IMPLEMENTATIONS = (
    "src/abaqus_sim_agent/evaluation/evidence_candidate.py",
    "src/abaqus_sim_agent/evaluation/structural_chunking.py",
    "src/abaqus_sim_agent/evaluation/cross_encoder.py",
    "src/abaqus_sim_agent/retrieval/structured_projection.py",
    "src/abaqus_sim_agent/retrieval/evidence_blocks.py",
    "src/abaqus_sim_agent/retrieval/evidence_units.py",
    "src/abaqus_sim_agent/retrieval/evidence_windows.py",
    "src/abaqus_sim_agent/retrieval/bm25.py",
    "src/abaqus_sim_agent/coding/document_evidence.py",
    "src/abaqus_sim_agent/coding/concept_lookup.py",
    "uv.lock",
)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def splitter() -> TokenBoundChildren:
    token_path = _encoder_directory() / "tokenizer.json"
    return TokenBoundChildren(Tokenizer.from_file(str(token_path)))


def _encoder_binding() -> dict[str, str]:
    root = _encoder_directory()
    return {str(p.resolve()): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def encoder() -> MiniLMEncoder:
    return MiniLMEncoder(_encoder_directory())


def _encoder_directory() -> Path:
    configured = os.environ.get("ABAQUS_SIM_AGENT_MINILM_DIR")
    if configured:
        return Path(configured).expanduser().resolve(strict=True)
    return (
        Path(__file__).parents[3] / "var/models/all-MiniLM-L6-v2-chroma-913d730/onnx"
    ).resolve(strict=True)


def _check_binding(output: Path) -> dict[str, Any]:
    manifest = read(output / "manifest.json")
    for path, digest in manifest["implementation"].items():
        if sha(Path(manifest["repository"]) / path) != digest:
            raise ValueError("implementation changed: " + path)
    for path, digest in manifest["encoder_files"].items():
        if sha(Path(path)) != digest:
            raise ValueError("encoder changed: " + path)
    for relative, digest in manifest["sources"].items():
        if sha(Path(manifest["source_root"]) / relative) != digest:
            raise ValueError("source changed: " + relative)
    return manifest


def prepare(
    source_root: Path,
    source_manifest: Path,
    source_key: str,
    output: Path,
    repository: Path,
    *,
    allow_headerless: bool = False,
) -> None:
    """冻结来源与实现，再保存完整父单元、检索子片段和逐页支持率。

    Note:
        source_manifest必须提供已绑定的相对文件路径→SHA256映射。失败保留目录，
        不生成ready标记；再次准备需要显式使用新目录，不覆盖任何历史证据。
    """
    output.mkdir(parents=True, exist_ok=False)
    sources = read(source_manifest)[source_key]
    manifest = {
        "schema_version": "structured-evidence-candidate-v1",
        "projection_version": PROJECTION_VERSION,
        "headerless_projection_version": HEADERLESS_PAIR_VERSION
        if allow_headerless
        else None,
        "parser_version": STRUCTURAL_PARSER_VERSION,
        "repository": str(repository.resolve()),
        "source_root": str(source_root.resolve()),
        "source_manifest": str(source_manifest.resolve()),
        "source_manifest_sha256": sha(source_manifest),
        "sources": sources,
        "implementation": {p: sha(repository / p) for p in IMPLEMENTATIONS},
        "encoder_files": _encoder_binding(),
        "packages": {
            p: importlib.metadata.version(p)
            for p in (
                "numpy",
                "onnxruntime",
                "tokenizers",
                "lxml",
                "pydantic",
            )
        },
        "release": "2026 FD02",
        "max_child_tokens": 256,
        "product_tool_compatible": False,
        "limits": [
            "Selected content roots are not a proof of complete source semantics.",
            "Simple rectangular tables and a bounded Presentation MathML subset only; unresolved structures remain stored but unsearchable.",
            "No default index switch, generative model call or Abaqus invocation.",
        ],
    }
    write_new(output / "manifest.json", manifest)
    split = splitter()
    units = []
    children = []
    pages = []
    started = time.perf_counter()
    for number, (relative, digest) in enumerate(sources.items(), 1):
        path = (source_root / relative).resolve()
        if not path.is_relative_to(source_root.resolve()):
            raise ValueError("source path outside root")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("source hash changed: " + relative)
        tree = etree.ElementTree(
            etree.fromstring(
                payload, etree.XMLParser(resolve_entities=False, no_network=True)
            )
        )
        title = str(tree.xpath("string(//title)")).strip()
        document_id = str(tree.xpath("string(//body/@id)"))
        namespaces = {
            k: v
            for n in tree.iter()
            if isinstance(n.tag, str)
            for k, v in n.nsmap.items()
            if k
        }
        blocks = []
        nodes = {}
        for ordinal, block in enumerate(
            extract_source_blocks(tree, title=title, document_id=document_id)
        ):
            found = tree.xpath(block.xpath, namespaces=namespaces)
            if len(found) != 1:
                raise ValueError("source XPath is not unique")
            node = found[0]
            parent = make_evidence_block(
                document_id=document_id,
                title=title,
                release="2026 FD02",
                heading_path=block.heading_path,
                source=f"http://localhost:4040/English/{relative}#{block.anchor}",
                source_sha256=digest,
                xpath=block.xpath,
                anchor=block.anchor,
                kind=block.kind,
                body=block.text,
                ordinal=ordinal,
            )
            parent = parent.model_copy(update={"source_role": source_role(node)})
            blocks.append(parent)
            nodes[parent.parent_id] = node
        original = build_evidence_units(blocks, nodes)
        current = [
            project_evidence_unit(u, split, allow_headerless=allow_headerless)
            for u in original
        ]
        if [k for u in current for k in u.member_ids] != [b.parent_id for b in blocks]:
            raise ValueError("source membership lost")
        page_children = []
        for old, unit in zip(original, current, strict=True):
            if old.evidence.body != unit.evidence.body:
                raise ValueError("source body changed")
            pieces = split.split(unit.evidence)
            if (
                pieces
                and "".join(
                    unit.evidence.search_text[
                        c.metadata["search_start"] : c.metadata["search_end"]
                    ]
                    for c in pieces
                )
                != unit.evidence.search_text
            ):
                raise ValueError("search projection spans lost")
            page_children.extend(pieces)
        units.extend(current)
        children.extend(page_children)
        pages.append(
            {
                "source": relative,
                "blocks": len(blocks),
                "units": len(current),
                "children": len(page_children),
                "new_searchable_units": sum(
                    a.evidence.projection_status != "text"
                    and b.evidence.projection_status == "text"
                    for a, b in zip(original, current, strict=True)
                ),
                "reasons": dict(
                    Counter(u.projection_reason for u in current if u.projection_reason)
                ),
                "projection_status": dict(
                    Counter(u.evidence.projection_status for u in current)
                ),
            }
        )
        if number % 100 == 0 or number == len(sources):
            print(
                f"PREPARE {number}/{len(sources)} units={len(units)} children={len(children)}",
                flush=True,
            )
    unit_hash = write_unit_store(output / "units.jsonl", units)
    reopened = read_unit_store(output / "units.jsonl", expected_sha256=unit_hash)
    parents = {k: u.evidence for k, u in reopened.items()}
    with (output / "children.jsonl").open("x", encoding="utf-8") as stream:
        for child in children:
            scoring_document(child, parents)
            if split.count(child.page_content) > 256:
                raise ValueError("child token overflow")
            stream.write(child.model_dump_json() + "\n")
    _check_binding(output)
    coverage = {
        "source_pages": len(pages),
        "source_blocks": sum(p["blocks"] for p in pages),
        "units": len(units),
        "children": len(children),
        "new_searchable_units": sum(p["new_searchable_units"] for p in pages),
        "unit_types": dict(Counter(u.unit_type for u in units)),
        "projection_status": dict(Counter(u.evidence.projection_status for u in units)),
        "new_supported_types": dict(
            Counter(
                u.unit_type
                for u in units
                if u.projection_version in {PROJECTION_VERSION, HEADERLESS_PAIR_VERSION}
                and u.evidence.projection_status == "text"
            )
        ),
        "unsupported_reasons": dict(
            Counter(u.projection_reason for u in units if u.projection_reason)
        ),
        "pages": pages,
        "max_child_tokens": max(split.count(c.page_content) for c in children),
        "prepare_seconds": time.perf_counter() - started,
    }
    write_new(output / "coverage.json", coverage)
    write_new(
        output / "prepared.json",
        {
            "units_sha256": unit_hash,
            "children_sha256": sha(output / "children.jsonl"),
            "coverage_sha256": sha(output / "coverage.json"),
            "manifest_sha256": sha(output / "manifest.json"),
        },
    )


def _load(output: Path) -> tuple[list[Document], dict[str, Any]]:
    ready = read(output / "prepared.json")
    for stem, filename in (
        ("units", "units.jsonl"),
        ("children", "children.jsonl"),
        ("coverage", "coverage.json"),
        ("manifest", "manifest.json"),
    ):
        if sha(output / filename) != ready[stem + "_sha256"]:
            raise ValueError("prepared artifact changed: " + filename)
    units = read_unit_store(
        output / "units.jsonl", expected_sha256=ready["units_sha256"]
    )
    children = [
        Document.model_validate_json(line)
        for line in (output / "children.jsonl").read_bytes().splitlines()
    ]
    return children, units


def _raw_documents(raw: Any) -> list[Document]:
    return [
        Document(id=i, page_content=d, metadata=m)
        for i, d, m in zip(raw["ids"], raw["documents"], raw["metadatas"], strict=True)
    ]


def paged_get(
    vector: FlatDocumentStore, *, include: list[str], page_size: int = 256
) -> dict[str, Any]:
    """分批读取，避免一次物化全部文档和向量。"""
    if page_size < 1:
        raise ValueError("page_size must be positive")
    result: dict[str, Any] = {"ids": [], **{key: [] for key in include}}
    while True:
        page = vector.get(include=include, limit=page_size, offset=len(result["ids"]))
        if not page["ids"]:
            break
        result["ids"].extend(page["ids"])
        for key in include:
            if page[key] is None or len(page[key]) != len(page["ids"]):
                raise ValueError("incomplete paged field: " + key)
            result[key].extend(page[key])
    if len(set(result["ids"])) != len(result["ids"]):
        raise ValueError("duplicate IDs in paged index read")
    return result


def embedding_digest(vector: FlatDocumentStore) -> str:
    """按稳定ID顺序绑定全部已存储向量。"""
    raw = paged_get(vector, include=["embeddings"])
    if raw["embeddings"] is None or len(set(raw["ids"])) != len(raw["ids"]):
        raise ValueError("missing embeddings or duplicate IDs")
    digest = hashlib.sha256()
    rows = sorted(
        zip(raw["ids"], raw["embeddings"], strict=True), key=lambda item: item[0]
    )
    for record_id, values in rows:
        encoded_id = record_id.encode()
        array = np.asarray(values, dtype="<f4")
        if array.ndim != 1 or not np.isfinite(array).all():
            raise ValueError("invalid stored embedding")
        digest.update(len(encoded_id).to_bytes(8, "little"))
        digest.update(encoded_id)
        digest.update(len(array).to_bytes(8, "little"))
        digest.update(array.tobytes())
    return digest.hexdigest()


def build_index(output: Path) -> None:
    _check_binding(output)
    documents, units = _load(output)
    target = output / "index"
    target.mkdir(exist_ok=False)
    started = time.perf_counter()
    vector = FlatDocumentStore(
        target,
        embeddings=encoder(),
        create_if_missing=True,
    )
    vector.replace_all(documents, batch_size=64)
    write_new(
        target / "manifest.json",
        {
            "schema_version": "structured-evidence-candidate-v1",
            "collection_name": COLLECTION,
            "document_count": len(documents),
            "product_tool_compatible": False,
            "release": "2026 FD02",
            "dense_algorithm": "numpy-flat-cosine-v1",
            "vector_dimensions": int(vector.vectors().shape[1]),
        },
    )
    sparse, _ = BM25DocumentIndex.build(documents, target)
    reopened = FlatDocumentStore(
        target,
        embeddings=encoder(),
    )
    records = _raw_documents(paged_get(reopened, include=["documents", "metadatas"]))
    parents = {k: u.evidence for k, u in units.items()}
    split = splitter()
    for child in records:
        scoring_document(child, parents)
        if split.count(child.page_content) > 256:
            raise ValueError("persisted token overflow")
    corpus = semantic_corpus_sha256(records)
    if (
        corpus != sparse.manifest.semantic_corpus_sha256
        or corpus != semantic_corpus_sha256(documents)
    ):
        raise ValueError("Dense/BM25/prepared corpus mismatch")
    _check_binding(output)
    write_new(
        output / "index-binding.json",
        {
            "prepared_sha256": sha(output / "prepared.json"),
            "semantic_corpus_sha256": corpus,
            "embeddings_sha256": embedding_digest(reopened),
            "index_manifest_sha256": sha(target / "manifest.json"),
            "bm25_manifest_sha256": sha(target / "lexical/manifest.json"),
            "bm25_database_sha256": sparse.manifest.database_sha256,
            "vector": dense_vector_storage_snapshot(
                target, collection_name=COLLECTION
            ).model_dump(mode="json"),
            "documents": len(records),
            "build_seconds": time.perf_counter() - started,
        },
    )


def replay(output: Path, query_manifest: Path, requirements: Path, model: Path) -> None:
    _check_binding(output)
    _, units = _load(output)
    target = output / "replay"
    target.mkdir(exist_ok=False)
    parents = {k: u.evidence for k, u in units.items()}
    membership = {k: u.evidence.parent_id for u in units.values() for k in u.member_ids}
    queries = read(query_manifest)["queries"]
    probes = read(requirements)["requirements"]
    acquired = read(model / "acquisition.json")
    if any(sha(model / p) != v["sha256"] for p, v in acquired["files"].items()):
        raise ValueError("reranker model binding changed")
    binding = read(output / "index-binding.json")
    if binding["prepared_sha256"] != sha(output / "prepared.json"):
        raise ValueError("index belongs to a different prepared corpus")
    if binding["index_manifest_sha256"] != sha(
        output / "index/manifest.json"
    ) or binding["bm25_manifest_sha256"] != sha(output / "index/lexical/manifest.json"):
        raise ValueError("index manifest changed")
    vector_snapshot = dense_vector_storage_snapshot(
        output / "index", collection_name=COLLECTION
    ).model_dump(mode="json")
    if vector_snapshot != binding["vector"]:
        raise ValueError("vector binding changed")
    write_new(
        target / "manifest.json",
        {
            "query_manifest_sha256": sha(query_manifest),
            "requirements_sha256": sha(requirements),
            "queries": queries,
            "requirements": probes,
            "model_binding": acquired,
            "index_binding_sha256": sha(output / "index-binding.json"),
            "policy": "Previously exposed development queries; Dense20+BM2520, fixed cross-encoder, dedupe Top4 complete parents; no new query tuning or held-out claim.",
        },
    )
    vector = FlatDocumentStore(
        output / "index",
        embeddings=encoder(),
    )
    sparse = BM25DocumentIndex.open(output / "index")
    records = _raw_documents(paged_get(vector, include=["documents", "metadatas"]))
    if embedding_digest(vector) != binding["embeddings_sha256"]:
        raise ValueError("stored embeddings changed")
    if (
        semantic_corpus_sha256(records) != binding["semantic_corpus_sha256"]
        or sparse.manifest.semantic_corpus_sha256 != binding["semantic_corpus_sha256"]
        or sparse.manifest.database_sha256 != binding["bm25_database_sha256"]
    ):
        raise ValueError("replay corpus or sparse database changed")
    reranker = OnnxPassageReranker(model)
    summaries = []
    for query in queries:
        dense = [
            d
            for d, _ in vector.similarity_search_with_score(
                query["query"], k=20, filter={"retrieval_role": "semantic"}
            )
        ]
        lexical = [h.document for h in sparse.search_passages(query["query"], top_k=20)]
        pool = candidate_union(dense, lexical)
        raw = {d.id: d for d in pool}
        ranked, seconds = reranker.rank(
            query["query"], [scoring_document(d, parents) for d in pool]
        )
        selected = expand_ranked_children([raw[r.document.id] for r in ranked], parents)
        evidence = _evidence_payloads(
            [RetrievalHit(document=d, match_type="semantic") for d in selected]
        )
        results = []
        for probe in probes:
            if probe["query"] != query["id"]:
                continue
            parent_id = membership[probe["parent_id"]]
            results.append(
                {
                    "need": probe["need"],
                    "unit_id": parent_id,
                    "in_pool": any(d.metadata["parent_id"] == parent_id for d in pool),
                    "selected": parent_id in [d.id for d in selected],
                    "delivered_complete_unit": any(
                        d.id == parent_id
                        and e.excerpt == parents[parent_id].body
                        and not e.truncated
                        for d, e in zip(selected, evidence, strict=True)
                    ),
                }
            )
        row = {
            "id": query["id"],
            "query": query["query"],
            "dense_ids": [d.id for d in dense],
            "bm25_ids": [d.id for d in lexical],
            "ranking": [
                {
                    "id": r.document.id,
                    "score": r.score,
                    "pair_tokens": r.pair_tokens,
                    "truncated": r.truncated,
                }
                for r in ranked
            ],
            "selected_units": [d.id for d in selected],
            "unit_types": [units[str(d.id)].unit_type for d in selected],
            "probes": results,
            "evidence": [e.model_dump(mode="json") for e in evidence],
            "rerank_seconds": seconds,
            "truncated_bodies": sum(e.truncated for e in evidence),
            "truncated_pairs": sum(r.truncated for r in ranked),
        }
        write_new(target / (query["id"] + ".json"), row)
        summaries.append(
            {
                k: v
                for k, v in row.items()
                if k not in {"evidence", "ranking", "dense_ids", "bm25_ids"}
            }
        )
        print(
            "QUERY",
            query["id"],
            [(p["need"], p["delivered_complete_unit"]) for p in results],
            flush=True,
        )
    if (
        dense_vector_storage_snapshot(
            output / "index", collection_name=COLLECTION
        ).model_dump(mode="json")
        != vector_snapshot
    ):
        raise ValueError("vector changed during replay")
    if embedding_digest(vector) != binding["embeddings_sha256"]:
        raise ValueError("stored embeddings changed during replay")
    _check_binding(output)
    replay_manifest = read(target / "manifest.json")
    if (
        sha(query_manifest) != replay_manifest["query_manifest_sha256"]
        or sha(requirements) != replay_manifest["requirements_sha256"]
        or any(sha(model / p) != v["sha256"] for p, v in acquired["files"].items())
    ):
        raise ValueError("replay inputs changed")
    write_new(
        target / "summary.json",
        {
            "results": summaries,
            "raw_sha256": {p.name: sha(p) for p in target.glob("*.json")},
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "index", "replay"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--source-key", default="source_sha256")
    parser.add_argument("--headerless-pairs", action="store_true")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--query-manifest", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    if args.stage == "prepare":
        if args.source_root is None or args.source_manifest is None:
            parser.error("prepare requires --source-root and --source-manifest")
        prepare(
            args.source_root,
            args.source_manifest,
            args.source_key,
            args.output,
            args.repository,
            allow_headerless=args.headerless_pairs,
        )
    elif args.stage == "index":
        build_index(args.output)
    else:
        if (
            args.query_manifest is None
            or args.requirements is None
            or args.model is None
        ):
            parser.error("replay requires --query-manifest, --requirements and --model")
        replay(args.output, args.query_manifest, args.requirements, args.model)


if __name__ == "__main__":
    main()
