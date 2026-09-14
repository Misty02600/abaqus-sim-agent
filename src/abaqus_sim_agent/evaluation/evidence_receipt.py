"""宿主与worker共同的收据校验；只读取文件，不加载推理会话或重建索引。"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urldefrag, urlparse

from pydantic import Field

from abaqus_sim_agent.retrieval.evidence_contracts import (
    Applicability,
    Binding,
    Contract,
    Digest,
    Evidence,
    EvidenceResult,
    SearchRequest,
)


class RuntimePaths(Contract):
    candidate: Path
    symbol_index: Path
    scope_rules: Path
    ranker_directory: Path


class ModelFilesUnavailable(OSError):
    pass


class WorkerRequest(Contract):
    protocol: Literal["doc-evidence-worker-v1"] = "doc-evidence-worker-v1"
    search_id: str
    request: SearchRequest
    paths: RuntimePaths
    snapshot: dict[str, Digest]
    binding: Binding
    deadline: float = Field(allow_inf_nan=False)


class WorkerReceipt(Contract):
    protocol: Literal["doc-evidence-worker-v1"] = "doc-evidence-worker-v1"
    request_sha256: Digest
    result: EvidenceResult
    raw_result_sha256: Digest | None = None
    search_calls: int = Field(ge=0, le=1)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("document evidence deadline expired")


def _within(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError("bound file escapes root")
    return path


def capture_snapshot(
    paths: RuntimePaths, deadline: float
) -> tuple[dict[str, str], Binding]:
    """独立核对固定manifest链并绑定本次实际读取的文件。

    Note:
        保留旧profile固定hash；额外绑定源、模型、实现、关系、SQLite与向量bytes。
        文件必须在本机已存在；不自动获取依赖。deadline在每个文件边界检查。
    """
    from .rag_evidence_baseline import FIXED_HASHES, SYMBOL_VECTOR_SHA256

    snapshot: dict[str, str] = {}

    def add(path: Path, expected: str | None = None):
        check_deadline(deadline)
        actual = digest(path)
        if expected is not None and actual != expected:
            raise ValueError("bound file digest mismatch: " + str(path))
        snapshot[str(path.resolve())] = actual

    candidate = paths.candidate
    for name, path in {
        "prepared": candidate / "prepared.json",
        "index": candidate / "index-binding.json",
        "scopes": paths.scope_rules,
        "ranker": paths.ranker_directory / "acquisition.json",
        "symbols": paths.symbol_index / "manifest.json",
    }.items():
        add(path, FIXED_HASHES[name])
    prepared = read(candidate / "prepared.json")
    for stem, filename in (
        ("units", "units.jsonl"),
        ("children", "children.jsonl"),
        ("coverage", "coverage.json"),
        ("manifest", "manifest.json"),
        ("equation_context_relations", "equation_context_relations.json"),
    ):
        add(candidate / filename, prepared[stem + "_sha256"])
    manifest = read(candidate / "manifest.json")
    for relative, expected in manifest["sources"].items():
        add(_within(Path(manifest["source_root"]), relative), expected)
    for relative, expected in manifest["implementation"].items():
        add(_within(Path(manifest["repository"]), relative), expected)
    try:
        for filename, expected in manifest["encoder_files"].items():
            add(Path(filename), expected)
        for filename, record in read(paths.ranker_directory / "acquisition.json")[
            "files"
        ].items():
            add(_within(paths.ranker_directory, filename), record["sha256"])
    except FileNotFoundError as exc:
        raise ModelFilesUnavailable("local model files are missing") from exc
    index = read(candidate / "index-binding.json")
    add(candidate / "index/manifest.json", index["index_manifest_sha256"])
    add(candidate / "index/lexical/manifest.json", index["bm25_manifest_sha256"])
    sparse = read(candidate / "index/lexical/manifest.json")
    add(
        _within(candidate / "index/lexical", sparse["database_filename"]),
        index["bm25_database_sha256"],
    )
    for record in index["vector"]["files"]:
        add(_within(candidate / "index", record["path"]), record["sha256"])
    # Chroma's fixed verifier checks logical embeddings and segment identity in-worker.
    # This independent before/after byte snapshot also covers exact-card SQLite content.
    for root in (candidate / "index", paths.symbol_index):
        wal = root / "chroma.sqlite3-wal"
        if wal.exists() and wal.stat().st_size:
            raise ValueError("nonempty SQLite WAL requires a quiescent source snapshot")
        add(root / "chroma.sqlite3")
        for path in sorted(root.glob("*/*")):
            if path.is_file() and path.suffix in {".bin", ".pickle"}:
                add(path)
    package = Path(__file__).resolve().parent.parent
    for relative in (
        "retrieval/evidence_contracts.py",
        "coding/document_search_port.py",
        "evaluation/evidence_process.py",
        "evaluation/evidence_receipt.py",
        "evaluation/evidence_worker.py",
        "evaluation/runtime_evidence_service.py",
        "evaluation/rag_evidence_baseline.py",
        "evaluation/unified_candidate_backend.py",
        "evaluation/unified_retrieval.py",
        "evaluation/cross_encoder.py",
    ):
        add(package / relative)
    binding = Binding(
        snapshot_sha256=hashlib.sha256(
            canonical(sorted(snapshot.items())).encode()
        ).hexdigest(),
        prepared_sha256=FIXED_HASHES["prepared"],
        candidate_index_sha256=FIXED_HASHES["index"],
        symbol_vector_sha256=SYMBOL_VECTOR_SHA256,
        ranker_acquisition_sha256=FIXED_HASHES["ranker"],
    )
    return snapshot, binding


def verify_snapshot(snapshot: dict[str, str], deadline: float) -> None:
    for filename, expected in snapshot.items():
        check_deadline(deadline)
        if digest(Path(filename)) != expected:
            raise ValueError("evidence dependency changed: " + filename)


def load_units(paths: RuntimePaths, ids: set[str]) -> dict[str, dict]:
    units = {}
    with (paths.candidate / "units.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key = row["evidence"]["parent_id"]
            if key in ids:
                units[key] = row
    return units


def count_tokens(paths: RuntimePaths):
    from tokenizers import Tokenizer

    manifest = read(paths.candidate / "manifest.json")
    names = [p for p in manifest["encoder_files"] if Path(p).name == "tokenizer.json"]
    if len(names) != 1:
        raise ValueError("ambiguous delivery tokenizer")
    tokenizer = Tokenizer.from_file(names[0])
    tokenizer.no_padding()
    tokenizer.no_truncation()
    return lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)


def source_permitted(source: str, request: SearchRequest) -> bool:
    return (
        not request.allowed_sources or urldefrag(source).url in request.allowed_sources
    )


def symbol_locator(source: Path, metadata: dict) -> tuple[str, ...]:
    """把已存在的符号锚点解析为源XPath，不假定旧卡片存有段落XPath。

    Note:
        indexed_symbol_card正文仍由官方页解析器重建核验；这里是来源锚点定位，
        不声称卡片的拼接正文等于该DOM节点的原文。
    """
    import lxml.etree as etree

    anchor = metadata.get("section_id") or metadata["document_id"]
    tree = etree.parse(
        str(source), etree.XMLParser(resolve_entities=False, no_network=True)
    )
    nodes = tree.xpath("//*[@id=$anchor]", anchor=anchor)
    if len(nodes) != 1:
        raise ValueError("symbol source anchor is missing or ambiguous")
    return (tree.getpath(nodes[0]),)


def verify_applicability(
    item: Evidence, source: Path, relative: str, paths: RuntimePaths
) -> None:
    import lxml.etree as etree

    from abaqus_sim_agent.retrieval.documents import _product_texts

    tree = etree.parse(
        str(source), etree.XMLParser(resolve_entities=False, no_network=True)
    )
    products = tuple(
        p for p in _product_texts(tree) if p in {"Abaqus/Standard", "Abaqus/Explicit"}
    )
    expected = {
        "products": products,
        "origin": "official_page" if products else "unknown",
        "evidence": relative + ": official Products"
        if products
        else "no official solver product declaration",
    }
    overrides = []
    if item.body_scope != "indexed_symbol_card":
        for rule in read(paths.scope_rules)["sections"]:
            if rule["source"] != relative:
                continue
            nodes = tree.xpath("//*[@id=$anchor]", anchor=rule["anchor"])
            if len(nodes) != 1 or rule["source_sha256"] != item.source_sha256:
                raise ValueError("scope source identity mismatch")
            xpath = tree.getpath(nodes[0])
            if all(x == xpath or x.startswith(xpath + "/") for x in item.member_xpaths):
                overrides.append((len(xpath), rule))
        if overrides:
            rule = max(overrides, key=lambda pair: pair[0])[1]
            expected = {
                "products": tuple(rule["products"]),
                "origin": "verified_section",
                "evidence": rule["anchor"],
            }
    if item.applicability != Applicability.model_validate(expected):
        raise ValueError("applicability provenance differs from source")


def validate_evidence(
    request: SearchRequest, result: EvidenceResult, paths: RuntimePaths
) -> None:
    """父进程重新读取原始单元/官方页并核对正文、引用、范围及实际交付预算。"""
    if result.status not in {"found", "not_found"}:
        return
    count = count_tokens(paths)
    payload = [e.model_dump(mode="json") for e in result.evidence]
    if result.cost.evidence_tokens != count(canonical(payload)):
        raise ValueError("serialized evidence count mismatch")
    if tuple(count(e.body) for e in result.evidence) != result.cost.body_tokens:
        raise ValueError("body count mismatch")
    ids = {e.id for e in result.evidence}
    relations = read(paths.candidate / "equation_context_relations.json")["relations"]
    related = {r["explanation_id"]: r["context_ids"] for r in relations}
    units = load_units(paths, ids | {k for id_ in ids for k in related.get(id_, [])})
    source_root = Path(read(paths.candidate / "manifest.json")["source_root"])
    for item in result.evidence:
        if not source_permitted(item.source, request):
            raise ValueError("source constraint violated")
        if (
            request.query.source_release is not None
            and item.release != request.query.source_release
        ):
            raise ValueError("release constraint violated")
        scope = item.applicability
        if (
            request.query.product
            and scope.origin != "unknown"
            and request.query.product not in scope.products
        ):
            raise ValueError("product constraint violated")
        relative = urlparse(item.source).path.removeprefix("/English/")
        source = _within(source_root, relative)
        if digest(source) != item.source_sha256:
            raise ValueError("source bytes changed")
        verify_applicability(item, source, relative, paths)
        if request.query.kind == "question":
            unit = units[item.id]
            parent = unit["evidence"]
            for key in (
                "body",
                "source",
                "source_sha256",
                "release",
                "document_id",
                "title",
            ):
                if getattr(item, key) != parent[key]:
                    raise ValueError("evidence differs from stored source: " + key)
            if item.member_xpaths != tuple(unit["member_xpaths"]):
                raise ValueError("source XPath mismatch")
            if item.section != (
                parent["heading_path"][-1] if parent["heading_path"] else ""
            ):
                raise ValueError("section title mismatch")
            expected = related.get(item.id, [])
            if [r.id for r in item.equation_context] != expected:
                raise ValueError("equation relation mismatch")
            for ref in item.equation_context:
                target = units[ref.id]
                if (
                    ref.source != target["evidence"]["source"]
                    or ref.member_xpaths != tuple(target["member_xpaths"])
                    or ref.projection_status != target["evidence"]["projection_status"]
                ):
                    raise ValueError("equation reference changed")
        else:
            from abaqus_sim_agent.retrieval.documents import AbaqusDocumentLoader

            rebuilt = [
                d
                for d in AbaqusDocumentLoader(source_root)._load_page(
                    source, relative.split("/")[0]
                )
                if d.id == item.id
            ]
            if len(rebuilt) != 1 or rebuilt[0].page_content != item.body:
                raise ValueError("symbol card differs from source")
            doc = rebuilt[0]
            for key in ("release", "title", "document_id"):
                if getattr(item, key) != doc.metadata[key]:
                    raise ValueError("symbol metadata mismatch")
            if (
                doc.metadata["primary_symbol"] != request.query.symbol
                or doc.metadata["entity_kind"] != request.query.symbol_kind
            ):
                raise ValueError("nonexact symbol match")
            if item.member_xpaths != symbol_locator(source, doc.metadata):
                raise ValueError("symbol XPath mismatch")


def validate_raw_receipt(raw: dict, result: EvidenceResult) -> None:
    """绑定模型可见证据到本次原始搜索结果；新字段不能改变正文、元数据或顺序。"""
    previous = -1
    ids = [row["id"] for row in raw["evidence"]]
    for item in result.evidence:
        position = ids.index(item.id)
        if position <= previous:
            raise ValueError("delivered ranking changed")
        previous = position
        old = raw["evidence"][position]
        candidate = item.model_dump(
            mode="json", exclude={"body_sha256", "member_xpaths"}
        )
        if not candidate["equation_context"]:
            candidate.pop("equation_context")
        if candidate != old:
            raise ValueError("raw search and delivered evidence disagree")
    if result.cost.baseline_evidence_tokens != raw.get("context_tokens"):
        raise ValueError("baseline budget receipt mismatch")
    trace = raw.get("trace", {})
    if result.cost.candidate_count != len(
        trace.get("pool_ids", [])
    ) or result.cost.scored_count != len(trace.get("ranking", [])):
        raise ValueError("stage counts mismatch")


def attach_provenance(raw: dict, baseline) -> Evidence:
    from pydantic import TypeAdapter

    if raw["body_scope"] == "indexed_symbol_card":
        cards = [d for d in baseline.backend.cards if d.id == raw["id"]]
        metadata = cards[0].metadata
        source = _within(baseline.backend.source_root, metadata["source_path"])
        xpaths = list(symbol_locator(source, metadata))
    else:
        xpaths = list(baseline.units[raw["id"]].member_xpaths)
    return TypeAdapter(Evidence).validate_json(
        canonical(
            {
                **raw,
                "body_sha256": hashlib.sha256(raw["body"].encode()).hexdigest(),
                "member_xpaths": xpaths,
            }
        )
    )
