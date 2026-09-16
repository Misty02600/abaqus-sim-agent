from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from abaqus_sim_agent.retrieval.types import Document

from .bm25 import BM25IndexError
from .comparison import (
    benchmark_sha256,
    evaluate_comparison,
    load_comparison_cases,
)
from .documents import AbaqusDocumentError, AbaqusDocumentLoader
from .evaluation import evaluate_retrieval, load_cases
from .heldout import audit_heldout, evaluate_heldout
from .index import DEFAULT_COLLECTION_NAME, AbaqusDocumentIndex, RetrievalHit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m abaqus_sim_agent.retrieval",
        description="Build and inspect the local Abaqus documentation retrieval index.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build the local SQLite/NumPy index")
    build.add_argument("--docs-root", required=True, type=Path)
    build.add_argument("--index-dir", type=Path, default=Path("var/rag"))
    build.add_argument("--docs-base-url", default="http://localhost:4040/English")
    build.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    build.add_argument("--max-chunk-size", type=int, default=1000)
    build.add_argument("--batch-size", type=int, default=128)
    build.add_argument(
        "--encoder-dir",
        type=Path,
        help="Bound local all-MiniLM-L6-v2 ONNX directory; defaults to the legacy cache",
    )
    build.add_argument("--no-reset", action="store_true")
    build.set_defaults(handler=_build_index)

    build_bm25 = subparsers.add_parser(
        "build-bm25",
        help="Build a persistent BM25 inverted index from semantic dense records",
    )
    _add_index_arguments(build_bm25)
    build_bm25.set_defaults(handler=_build_bm25_index)

    search = subparsers.add_parser("search", help="Run a document search")
    _add_index_arguments(search)
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument(
        "--mode",
        choices=("dense", "bm25", "hybrid", "augmented"),
        default="augmented",
        help=(
            "Retrieval algorithm; augmented preserves the historical "
            "caller-supplied exact-symbol prefix"
        ),
    )
    search.add_argument(
        "--exact-term",
        action="append",
        default=[],
        help="Abaqus keyword, element, parameter, or output symbol to rank first",
    )
    search.add_argument(
        "--product",
        action="append",
        default=[],
        help="Optional product filter for exact symbols, for example Abaqus/Standard",
    )
    search.set_defaults(handler=_search_concepts)

    lookup = subparsers.add_parser("lookup", help="Look up an exact Abaqus symbol")
    _add_index_arguments(lookup)
    lookup.add_argument("symbol")
    lookup.add_argument("--limit", type=int, default=20)
    lookup.add_argument("--product", action="append", default=[])
    lookup.set_defaults(handler=_lookup_symbol)

    inspect = subparsers.add_parser("inspect", help="Print the index manifest")
    _add_index_arguments(inspect)
    inspect.set_defaults(handler=_inspect_index)

    evaluate = subparsers.add_parser(
        "evaluate", help="Evaluate a frozen JSONL query set"
    )
    _add_index_arguments(evaluate)
    evaluate.add_argument("--benchmark", required=True, type=Path)
    evaluate.add_argument("--top-k", type=int, default=5)
    evaluate.add_argument("--output", type=Path)
    evaluate.set_defaults(handler=_evaluate_index)

    compare = subparsers.add_parser(
        "compare",
        help="Compare BM25, Dense, and RRF with the same raw queries",
    )
    _add_index_arguments(compare)
    compare.add_argument("--benchmark", required=True, type=Path)
    compare.add_argument("--top-k", type=int, default=5)
    compare.add_argument("--fetch-k", type=int)
    compare.add_argument("--rrf-k", type=int, default=60)
    compare.add_argument("--no-warmup", action="store_true")
    compare.add_argument("--output", type=Path)
    compare.set_defaults(handler=_compare_index)

    audit = subparsers.add_parser(
        "audit-heldout",
        help="Validate a sealed held-out dataset without running retrieval",
    )
    _add_index_arguments(audit)
    audit.add_argument("--seal", required=True, type=Path)
    audit.add_argument("--repository-root", type=Path, default=Path.cwd())
    audit.set_defaults(handler=_audit_heldout)

    heldout = subparsers.add_parser(
        "evaluate-heldout",
        help="Audit and evaluate a sealed held-out dataset",
    )
    _add_index_arguments(heldout)
    heldout.add_argument("--seal", required=True, type=Path)
    heldout.add_argument("--repository-root", type=Path, default=Path.cwd())
    heldout.add_argument("--no-warmup", action="store_true")
    heldout.add_argument("--output", required=True, type=Path)
    heldout.set_defaults(handler=_evaluate_heldout)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (AbaqusDocumentError, BM25IndexError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 2


def _add_index_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index-dir", type=Path, default=Path("var/rag"))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)


def _build_index(args: argparse.Namespace) -> int:
    loader = AbaqusDocumentLoader(
        args.docs_root,
        docs_base_url=args.docs_base_url,
        max_chunk_size=args.max_chunk_size,
    )
    _index, manifest = AbaqusDocumentIndex.build(
        loader,
        args.index_dir,
        encoder_directory=args.encoder_dir,
        collection_name=args.collection,
        batch_size=args.batch_size,
        reset=not args.no_reset,
    )
    print(manifest.model_dump_json(indent=2))
    return 0


def _build_bm25_index(args: argparse.Namespace) -> int:
    manifest = _open_index(args).build_bm25()
    print(manifest.model_dump_json(indent=2))
    return 0


def _search_concepts(args: argparse.Namespace) -> int:
    index = _open_index(args)
    if args.mode != "augmented" and (args.exact_term or args.product):
        raise ValueError(
            "--exact-term and --product are only valid with --mode augmented"
        )
    if args.mode == "dense":
        results = [
            RetrievalHit(
                document=document,
                match_type="semantic",
                distance=distance,
            )
            for document, distance in index.search_concepts(
                args.query,
                top_k=args.top_k,
            )
        ]
    elif args.mode == "bm25":
        results = index.search_bm25(args.query, top_k=args.top_k)
    elif args.mode == "hybrid":
        results = index.search_hybrid(args.query, top_k=args.top_k)
    else:
        results = index.search(
            args.query,
            exact_terms=args.exact_term,
            products=args.product,
            top_k=args.top_k,
        )
    payload = [
        _document_payload(
            hit.document,
            distance=hit.distance,
            match_type=hit.match_type,
            score=hit.score,
        )
        for hit in results
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _lookup_symbol(args: argparse.Namespace) -> int:
    index = _open_index(args)
    documents = index.lookup_symbol(
        args.symbol,
        products=args.product,
        limit=args.limit,
    )
    payload = [_document_payload(document) for document in documents]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _inspect_index(args: argparse.Namespace) -> int:
    manifest = _open_index(args).read_manifest()
    print(manifest.model_dump_json(indent=2))
    return 0


def _evaluate_index(args: argparse.Namespace) -> int:
    index = _open_index(args)
    evaluation = evaluate_retrieval(
        index,
        load_cases(args.benchmark),
        top_k=args.top_k,
    )
    output = evaluation.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)
    return 0


def _compare_index(args: argparse.Namespace) -> int:
    benchmark = args.benchmark.resolve(strict=True)
    evaluation = evaluate_comparison(
        _open_index(args),
        load_comparison_cases(benchmark),
        benchmark_sha256=benchmark_sha256(benchmark),
        top_k=args.top_k,
        fetch_k=args.fetch_k,
        rrf_k=args.rrf_k,
        warmup=not args.no_warmup,
    )
    output = evaluation.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)
    return 0


def _audit_heldout(args: argparse.Namespace) -> int:
    audited = audit_heldout(
        _open_index(args),
        args.seal,
        repository_root=args.repository_root,
    )
    print(audited.audit.model_dump_json(indent=2))
    return 0


def _evaluate_heldout(args: argparse.Namespace) -> int:
    index = _open_index(args)
    audited = audit_heldout(
        index,
        args.seal,
        repository_root=args.repository_root,
    )
    evaluation = evaluate_heldout(
        index,
        audited,
        warmup=not args.no_warmup,
    )
    output = evaluation.model_dump_json(indent=2)
    if args.output.exists():
        raise ValueError(f"held-out output already exists: {args.output}")
    _write_text_atomic(args.output, output)
    print(output)
    return 0


def _open_index(args: argparse.Namespace) -> AbaqusDocumentIndex:
    return AbaqusDocumentIndex(
        args.index_dir,
        collection_name=args.collection,
    )


def _document_payload(
    document: Document,
    *,
    distance: float | None = None,
    match_type: str | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    metadata = dict(document.metadata)
    payload: dict[str, Any] = {
        "id": document.id,
        "document_id": metadata.get("document_id"),
        "title": metadata.get("title"),
        "section_title": metadata.get("section_title"),
        "source_url": metadata.get("source_url"),
        "entity_kind": metadata.get("entity_kind"),
        "excerpt": document.page_content[:500],
    }
    if distance is not None:
        payload["distance"] = distance
    if match_type is not None:
        payload["match_type"] = match_type
    if score is not None:
        payload["score"] = score
    return payload


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
