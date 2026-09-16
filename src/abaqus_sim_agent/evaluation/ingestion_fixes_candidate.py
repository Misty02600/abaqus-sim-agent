"""显式、独立的来源补页/公式说明段候选；不改旧索引或产品默认策略。"""

import argparse
import hashlib
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import lxml.etree as etree
import numpy as np

from abaqus_sim_agent.retrieval.equation_prose import (
    PARTITION_VERSION,
    EquationContextRelation,
    build_equation_prose_units,
)
from abaqus_sim_agent.retrieval.flat_store import FlatDocumentStore

from . import evidence_candidate as io
from .equation_context_delivery import context_references

ADDED_SOURCE = "SIMACAEEXCRefMap/simaexc-c-analysisproc.htm"
NEW_IMPLEMENTATIONS = (
    "src/abaqus_sim_agent/retrieval/equation_prose.py",
    "src/abaqus_sim_agent/evaluation/equation_context_delivery.py",
    "src/abaqus_sim_agent/evaluation/ingestion_fixes_candidate.py",
)


def check_relations(output: Path, units):
    ready = io.read(output / "prepared.json")
    path = output / "equation_context_relations.json"
    if io.sha(path) != ready["equation_context_relations_sha256"]:
        raise ValueError("equation context sidecar changed")
    raw = io.read(path)
    if raw["partition_version"] != PARTITION_VERSION:
        raise ValueError("unknown equation partition")
    relations = [
        EquationContextRelation(
            r["explanation_id"], tuple(r["context_ids"]), r["source_group_id"]
        )
        for r in raw["relations"]
    ]
    return context_references(relations, units)


def source_registry(source_root: Path, relative: str, digest: str):
    path = (source_root / relative).resolve()
    if not path.is_relative_to(source_root.resolve()):
        raise ValueError("source outside root")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("source hash changed")
    tree = etree.ElementTree(
        etree.fromstring(
            payload, etree.XMLParser(resolve_entities=False, no_network=True)
        )
    )
    title = str(tree.xpath("string(//title)")).strip()
    document_id = str(tree.xpath("string(//body/@id)"))
    if not title or not document_id:
        raise ValueError("source page identity missing")
    namespaces = {
        k: v
        for n in tree.iter()
        if isinstance(n.tag, str)
        for k, v in n.nsmap.items()
        if k
    }
    blocks, nodes = [], {}
    for ordinal, block in enumerate(
        io.extract_source_blocks(tree, title=title, document_id=document_id)
    ):
        found = tree.xpath(block.xpath, namespaces=namespaces)
        if len(found) != 1:
            raise ValueError("source XPath not unique")
        node = found[0]
        parent = io.make_evidence_block(
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
        ).model_copy(update={"source_role": io.source_role(node)})
        blocks.append(parent)
        nodes[parent.parent_id] = node
    return blocks, nodes


def prepare(old: Path, output: Path):
    previous = io._check_binding(old)
    old_children, old_units = io._load(old)
    source_root = Path(previous["source_root"])
    if ADDED_SOURCE in previous["sources"]:
        raise ValueError("additional source is already present")
    output.mkdir(parents=True, exist_ok=False)
    sources = {**previous["sources"], ADDED_SOURCE: io.sha(source_root / ADDED_SOURCE)}
    io.write_new(
        output / "sources.json",
        {
            "sources": sources,
            "baseline_manifest_sha256": io.sha(old / "manifest.json"),
            "added_sources": [ADDED_SOURCE],
            "provenance": "Same installed 2026 FD02 source tree; explicit execution-guide inventory repair, not a 2024 compatibility assertion or full manual coverage claim.",
        },
    )
    manifest = {
        **previous,
        "source_manifest": str((output / "sources.json").resolve()),
        "source_manifest_sha256": io.sha(output / "sources.json"),
        "sources": sources,
        "partition_version": PARTITION_VERSION,
        "implementation": {
            **previous["implementation"],
            **{
                p: io.sha(Path(previous["repository"]) / p) for p in NEW_IMPLEMENTATIONS
            },
        },
        "baseline": str(old.resolve()),
        "baseline_prepared_sha256": io.sha(old / "prepared.json"),
        "partition_eligibility": "Only legacy formula groups still unsearchable after the unchanged headerless-enabled projector.",
    }
    io.write_new(output / "manifest.json", manifest)
    split = io.splitter()
    units, children, relations, pages = [], [], [], []
    started = time.perf_counter()
    for number, (relative, digest) in enumerate(sources.items(), 1):
        blocks, nodes = source_registry(source_root, relative, digest)
        original = io.build_evidence_units(blocks, nodes)
        legacy = [
            io.project_evidence_unit(u, split, allow_headerless=True) for u in original
        ]
        eligible = frozenset(
            a.evidence.parent_id
            for a, b in zip(original, legacy, strict=True)
            if a.unit_type == "formula" and b.evidence.projection_status != "text"
        )
        partition = build_equation_prose_units(
            blocks, nodes, eligible_group_ids=eligible
        )
        current = [
            io.project_evidence_unit(u, split, allow_headerless=True)
            for u in partition.units
        ]
        mapped = {
            a.evidence.parent_id: b.evidence.parent_id
            for a, b in zip(partition.units, current, strict=True)
        }
        page_relations = [
            EquationContextRelation(
                mapped[r.explanation_id],
                tuple(mapped[k] for k in r.context_ids),
                r.source_group_id,
            )
            for r in partition.relations
        ]
        context_references(page_relations, {u.evidence.parent_id: u for u in current})
        if [k for u in current for k in u.member_ids] != [b.parent_id for b in blocks]:
            raise ValueError("source membership lost")
        if "\n\n".join(u.evidence.body for u in current) != "\n\n".join(
            u.evidence.body for u in legacy
        ):
            raise ValueError("source body changed")
        current_by_id = {u.evidence.parent_id: u for u in current}
        for unit in legacy:
            if (
                unit.evidence.projection_status == "text"
                and current_by_id.get(unit.evidence.parent_id) != unit
            ):
                raise ValueError("previous searchable unit changed")
        page_children = []
        for unit in current:
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
                raise ValueError("search spans lost")
            page_children.extend(pieces)
        units.extend(current)
        children.extend(page_children)
        relations.extend(page_relations)
        pages.append(
            {
                "source": relative,
                "blocks": len(blocks),
                "units": len(current),
                "children": len(page_children),
                "eligible_formula_groups": len(eligible),
                "split_groups": len({r.source_group_id for r in page_relations}),
                "explanations": len(page_relations),
                "searchable_explanations": sum(
                    current_by_id[r.explanation_id].evidence.projection_status == "text"
                    for r in page_relations
                ),
                "projection_status": dict(
                    Counter(u.evidence.projection_status for u in current)
                ),
            }
        )
        if number % 100 == 0 or number == len(sources):
            print(
                "PREPARE",
                number,
                "/",
                len(sources),
                "units",
                len(units),
                "children",
                len(children),
                flush=True,
            )
    new_by_id = {u.evidence.parent_id: u for u in units}
    preserved = [
        u for u in old_units.values() if u.evidence.projection_status == "text"
    ]
    if any(new_by_id.get(u.evidence.parent_id) != u for u in preserved):
        raise ValueError("frozen searchable baseline changed")
    child_by_id = {c.id: c for c in children}
    if any(child_by_id.get(c.id) != c for c in old_children):
        raise ValueError("frozen baseline child changed")
    unit_hash = io.write_unit_store(output / "units.jsonl", units)
    parents = {k: u.evidence for k, u in new_by_id.items()}
    with (output / "children.jsonl").open("x", encoding="utf-8") as stream:
        for child in children:
            io.scoring_document(child, parents)
            if split.count(child.page_content) > 256:
                raise ValueError("child token overflow")
            stream.write(child.model_dump_json() + "\n")
    io.write_new(
        output / "equation_context_relations.json",
        {
            "partition_version": PARTITION_VERSION,
            "relations": [asdict(r) for r in relations],
        },
    )
    coverage = {
        "source_pages": len(sources),
        "source_blocks": sum(p["blocks"] for p in pages),
        "units": len(units),
        "children": len(children),
        "preserved_searchable_units": len(preserved),
        "preserved_children": len(old_children),
        "split_groups": sum(p["split_groups"] for p in pages),
        "explanations": len(relations),
        "searchable_explanations": sum(p["searchable_explanations"] for p in pages),
        "projection_status": dict(Counter(u.evidence.projection_status for u in units)),
        "unit_types": dict(Counter(u.unit_type for u in units)),
        "unsupported_reasons": dict(
            Counter(u.projection_reason for u in units if u.projection_reason)
        ),
        "pages": pages,
        "prepare_seconds": time.perf_counter() - started,
    }
    io.write_new(output / "coverage.json", coverage)
    io._check_binding(output)
    io.write_new(
        output / "prepared.json",
        {
            "units_sha256": unit_hash,
            **{
                name + "_sha256": io.sha(output / filename)
                for name, filename in (
                    ("children", "children.jsonl"),
                    ("coverage", "coverage.json"),
                    ("manifest", "manifest.json"),
                    ("equation_context_relations", "equation_context_relations.json"),
                    ("sources", "sources.json"),
                )
            },
        },
    )
    check_relations(output, new_by_id)
    io._check_binding(old)
    if io.sha(old / "prepared.json") != manifest["baseline_prepared_sha256"]:
        raise ValueError("baseline changed")
    print("PREPARED", {k: v for k, v in coverage.items() if k != "pages"}, flush=True)


def build_index(old: Path, output: Path):
    previous = io._check_binding(old)
    manifest = io._check_binding(output)
    documents, units = io._load(output)
    check_relations(output, units)
    binding = io.read(old / "index-binding.json")
    if (
        binding["prepared_sha256"] != io.sha(old / "prepared.json")
        or manifest["baseline_prepared_sha256"] != binding["prepared_sha256"]
    ):
        raise ValueError("old prepared binding changed")
    if any(
        manifest["sources"].get(p) != h for p, h in previous["sources"].items()
    ) or set(manifest["sources"]) - set(previous["sources"]) != {ADDED_SOURCE}:
        raise ValueError("source change outside declared inventory repair")
    if (
        manifest["encoder_files"] != previous["encoder_files"]
        or manifest["packages"] != previous["packages"]
    ):
        raise ValueError("encoder/runtime mismatch")

    def check_old():
        if (
            io.dense_vector_storage_snapshot(
                old / "index", collection_name=io.COLLECTION
            ).model_dump(mode="json")
            != binding["vector"]
        ):
            raise ValueError("old dense vector storage changed")
        if (
            io.sha(old / "index/manifest.json") != binding["index_manifest_sha256"]
            or io.sha(old / "index/lexical/manifest.json")
            != binding["bm25_manifest_sha256"]
        ):
            raise ValueError("old index manifest changed")
        sparse = io.BM25DocumentIndex.open(old / "index")
        if sparse.manifest.database_sha256 != binding["bm25_database_sha256"]:
            raise ValueError("old BM25 changed")

    check_old()
    prior = FlatDocumentStore(
        old / "index",
        embeddings=io.encoder(),
    )
    raw = io.paged_get(prior, include=["documents", "metadatas", "embeddings"])
    old_docs = io._raw_documents(raw)
    if (
        io.semantic_corpus_sha256(old_docs) != binding["semantic_corpus_sha256"]
        or io.embedding_digest(prior) != binding["embeddings_sha256"]
    ):
        raise ValueError("old corpus/embedding changed")
    by_id = {d.id: (d, e) for d, e in zip(old_docs, raw["embeddings"], strict=True)}
    target = output / "index"
    target.mkdir(exist_ok=False)
    started = time.perf_counter()
    vector = FlatDocumentStore(
        target,
        embeddings=io.encoder(),
        create_if_missing=True,
    )
    reused = embedded = 0
    fresh = [document for document in documents if document.id not in by_id]
    fresh_vectors = iter(io.encoder().embed_documents([d.page_content for d in fresh]))
    vectors = []
    for document in documents:
        existing = by_id.get(document.id)
        if existing is None:
            vectors.append(next(fresh_vectors))
            embedded += 1
        else:
            if document != existing[0]:
                raise ValueError("same ID with different text or metadata")
            vectors.append(existing[1])
            reused += 1
    vector.replace_all_with_vectors(documents, np.asarray(vectors, dtype=np.float32))
    if reused != len(old_docs):
        raise ValueError("not every old embedding retained")
    io.write_new(
        target / "manifest.json",
        {
            "schema_version": "structured-evidence-candidate-v1",
            "collection_name": io.COLLECTION,
            "document_count": len(documents),
            "product_tool_compatible": False,
            "release": "2026 FD02",
            "dense_algorithm": "numpy-flat-cosine-v1",
            "vector_dimensions": int(vector.vectors().shape[1]),
        },
    )
    sparse, _ = io.BM25DocumentIndex.build(documents, target)
    restored = io._raw_documents(
        io.paged_get(vector, include=["documents", "metadatas"])
    )
    corpus = io.semantic_corpus_sha256(restored)
    if (
        corpus != io.semantic_corpus_sha256(documents)
        or corpus != sparse.manifest.semantic_corpus_sha256
    ):
        raise ValueError("new corpus mismatch")
    check_old()
    if io.embedding_digest(prior) != binding["embeddings_sha256"]:
        raise ValueError("old embeddings changed")
    io._check_binding(output)
    check_relations(output, units)
    io.write_new(
        output / "index-binding.json",
        {
            "prepared_sha256": io.sha(output / "prepared.json"),
            "semantic_corpus_sha256": corpus,
            "embeddings_sha256": io.embedding_digest(vector),
            "index_manifest_sha256": io.sha(target / "manifest.json"),
            "bm25_manifest_sha256": io.sha(target / "lexical/manifest.json"),
            "bm25_database_sha256": sparse.manifest.database_sha256,
            "vector": io.dense_vector_storage_snapshot(
                target, collection_name=io.COLLECTION
            ).model_dump(mode="json"),
            "documents": len(restored),
            "build_seconds": time.perf_counter() - started,
            "reuse": {
                "old_index_binding_sha256": io.sha(old / "index-binding.json"),
                "identical_record_embeddings_reused": reused,
                "new_record_embeddings": embedded,
            },
        },
    )
    print("SEALED", len(restored), "reused", reused, "new", embedded, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "index"])
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.stage == "prepare" else build_index)(args.old, args.output)


if __name__ == "__main__":
    main()
