"""复用冻结 NumPy Flat/BM25 及源单元的只读候选适配器。"""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import lxml.etree as etree

from abaqus_sim_agent.retrieval.documents import AbaqusDocumentLoader, _product_texts
from abaqus_sim_agent.retrieval.flat_store import FlatDocumentStore
from abaqus_sim_agent.retrieval.types import Document

from . import evidence_candidate as driver
from .cross_encoder import OnnxPassageReranker, passage_text
from .unified_retrieval import Applicability


class FixedRanker:
    def __init__(self, directory: Path):
        self.acquisition = driver.read(directory / "acquisition.json")
        if any(
            driver.sha(directory / name) != v["sha256"]
            for name, v in self.acquisition["files"].items()
        ):
            raise ValueError("reranker weights changed")
        self.model = OnnxPassageReranker(directory)

    def pair_count(self, query, document):
        return len(self.model.full_tokenizer.encode(query, passage_text(document)).ids)

    def rank(self, query, documents):
        return self.model.rank(query, documents)


class BoundCandidateBackend:
    def __init__(self, candidate: Path, symbol_index: Path, scope_rules: Path):
        """核对固定语料并读取官方范围；未知产品范围不猜测。

        Args:
            candidate: 既有结构化父子候选库及其 Flat/BM25 索引。
            symbol_index: 只读取exact卡片的旧索引；不合并其语义语料。
            scope_rules: 已审阅的章节范围声明，每项绑定源hash、唯一DOM ID和完整标题。

        Note:
            Dense通过parent_id排除集在SQLite候选阶段过滤；BM25先计算所有匹配片段
            再在Top-K截取前应用相同排除集，保留原全语料IDF。没有浅Top-K后过滤。
        """
        self.candidate = candidate
        manifest = driver._check_binding(candidate)
        docs, units = driver._load(candidate)
        self.parents = {key: u.evidence for key, u in units.items()}
        self.units = units
        self.scopes: dict[str, Applicability] = {}
        self.verified_sources: dict[str, str] = {}
        self.source_manifest = manifest
        source_root = Path(manifest["source_root"]).resolve()
        self.source_root = source_root
        self.page_relatives = {}
        self.verified_cards: set[str] = set()
        rules = driver.read(scope_rules)["sections"]
        trees = {}
        page_scopes = {}
        for number, (relative, digest) in enumerate(manifest["sources"].items(), 1):
            source = (source_root / relative).resolve()
            if not source.is_relative_to(source_root) or driver.sha(source) != digest:
                raise ValueError("source binding changed")
            tree = etree.parse(
                str(source), etree.XMLParser(resolve_entities=False, no_network=True)
            )
            document_id = str(tree.xpath("string(//body/@id)"))
            self.verified_sources[document_id] = digest
            self.page_relatives[document_id] = relative
            products = tuple(
                p
                for p in _product_texts(tree)
                if p in {"Abaqus/Standard", "Abaqus/Explicit"}
            )
            page_scopes[document_id] = Applicability(
                products,
                "official_page" if products else "unknown",
                relative + ": official Products"
                if products
                else "no official solver product declaration",
            )
            if any(r["source"] == relative for r in rules):
                trees[relative] = tree
            if number % 500 == 0:
                print("SOURCE VERIFIED", number, flush=True)
        overrides = []
        declared_locations = set()
        for rule in rules:
            if manifest["sources"].get(rule["source"]) != rule["source_sha256"]:
                raise ValueError("scope declaration source changed")
            tree = trees[rule["source"]]
            nodes = tree.xpath("//*[@id=$anchor]", anchor=rule["anchor"])
            if len(nodes) != 1:
                raise ValueError("scope declaration anchor not unique")
            headings = nodes[0].xpath("./h1|./h2|./h3")
            if (
                len(headings) != 1
                or " ".join("".join(headings[0].itertext()).split()) != rule["heading"]
            ):
                raise ValueError("scope declaration heading changed")
            if not rule["products"] or not set(rule["products"]) <= {
                "Abaqus/Standard",
                "Abaqus/Explicit",
            }:
                raise ValueError("invalid scope product declaration")
            if any(product not in rule["heading"] for product in rule["products"]):
                raise ValueError("reviewed scope conflicts with its declared heading")
            location = (rule["source"], tree.getpath(nodes[0]))
            if location in declared_locations:
                raise ValueError("duplicate scope declaration")
            declared_locations.add(location)
            overrides.append(
                (
                    rule["source"],
                    tree.getpath(nodes[0]),
                    Applicability(
                        tuple(rule["products"]), "verified_section", rule["anchor"]
                    ),
                )
            )
        for key, unit in units.items():
            parent = unit.evidence
            scope = page_scopes[parent.document_id]
            relative = urlparse(parent.source).path.removeprefix("/English/")
            matches = [
                (len(path), specific)
                for src, path, specific in overrides
                if src == relative
                and all(
                    x == path or x.startswith(path + "/") for x in unit.member_xpaths
                )
            ]
            if matches:
                scope = max(matches, key=lambda item: item[0])[1]
            self.scopes[key] = scope
        self.page_scopes = page_scopes
        self.binding = driver.read(candidate / "index-binding.json")
        target = candidate / "index"
        if (
            driver.sha(candidate / "prepared.json") != self.binding["prepared_sha256"]
            or driver.sha(target / "manifest.json")
            != self.binding["index_manifest_sha256"]
        ):
            raise ValueError("candidate manifest mismatch")
        self.vector = FlatDocumentStore(
            target,
            embeddings=driver.encoder(),
        )
        self.sparse = driver.BM25DocumentIndex.open(target)
        if (
            self.sparse.manifest.semantic_corpus_sha256
            != self.binding["semantic_corpus_sha256"]
            or driver.semantic_corpus_sha256(docs)
            != self.binding["semantic_corpus_sha256"]
        ):
            raise ValueError("candidate corpus mismatch")
        if self.sparse.manifest.database_sha256 != self.binding["bm25_database_sha256"]:
            raise ValueError("BM25 database changed")
        if driver.embedding_digest(self.vector) != self.binding["embeddings_sha256"]:
            raise ValueError("candidate embeddings changed")
        if (
            driver.dense_vector_storage_snapshot(
                target, collection_name=driver.COLLECTION
            ).model_dump(mode="json")
            != self.binding["vector"]
        ):
            raise ValueError("candidate HNSW changed")
        self.symbol_index = symbol_index
        self.cards: list[Document] | None = None
        self.symbol_binding = None
        self.split = driver.splitter()
        self.dataset_binding = {
            "prepared": driver.sha(candidate / "prepared.json"),
            "index": driver.sha(candidate / "index-binding.json"),
            "scope_rules": driver.sha(scope_rules),
            "known_units": sum(s.origin != "unknown" for s in self.scopes.values()),
            "unknown_units": sum(s.origin == "unknown" for s in self.scopes.values()),
            "verified_section_units": sum(
                s.origin == "verified_section" for s in self.scopes.values()
            ),
        }

    def dense(
        self,
        query: str,
        *,
        limit: int,
        excluded_parents: frozenset[str],
    ) -> list[Document]:
        if len(excluded_parents) > 20000:
            raise ValueError("scope predicate exceeds explicit adapter capacity")
        where: dict[str, Any] = {"retrieval_role": "semantic"}
        if excluded_parents:
            where = {"$and": [where, {"parent_id": {"$nin": sorted(excluded_parents)}}]}
        return [
            d
            for d, _ in self.vector.similarity_search_with_score(
                query, k=limit, filter=where
            )
        ]

    def lexical(self, query, *, limit, excluded_parents):
        hits = self.sparse.search_passages(
            query, top_k=self.sparse.manifest.documents_indexed
        )
        return [
            h.document
            for h in hits
            if h.document.metadata["parent_id"] not in excluded_parents
        ][:limit]

    def page_lexical(
        self,
        query,
        *,
        document_ids,
        limit_per_page,
        excluded_parents,
        represented_parents,
    ):
        """在全语料BM25评分后按已选页面、范围和父单元筛选，保留全局IDF。"""
        if not document_ids or limit_per_page < 1:
            return []
        hits = self.sparse.search_passages(
            query, top_k=self.sparse.manifest.documents_indexed
        )
        pages = set(document_ids)
        counts = dict.fromkeys(pages, 0)
        seen = set(represented_parents)
        selected = []
        for hit in hits:
            doc = hit.document
            parent, page = doc.metadata["parent_id"], doc.metadata["document_id"]
            if (
                page not in pages
                or counts[page] >= limit_per_page
                or parent in excluded_parents
                or parent in seen
            ):
                continue
            selected.append(doc)
            seen.add(parent)
            counts[page] += 1
        return selected

    def symbols(self, symbol, symbol_kind):
        if self.cards is None:
            manifest = driver.read(self.symbol_index / "manifest.json")
            vector = FlatDocumentStore(
                self.symbol_index,
                embeddings=driver.encoder(),
            )
            self.symbol_binding = {
                "manifest": driver.sha(self.symbol_index / "manifest.json"),
                "vector": driver.dense_vector_storage_snapshot(
                    self.symbol_index, collection_name=manifest["collection_name"]
                ).model_dump(mode="json"),
            }
            raw = vector.get(
                where={"retrieval_role": "exact"}, include=["documents", "metadatas"]
            )
            self.cards = []
            for doc in driver._raw_documents(raw):
                key = doc.metadata["document_id"]
                if key not in self.verified_sources:
                    raise ValueError("symbol source outside verified corpus")
                doc.metadata["source_sha256"] = self.verified_sources[key]
                self.scopes[str(doc.id)] = self.page_scopes[key]
                self.cards.append(doc)
        matches = [
            d
            for d in self.cards
            if d.metadata.get("primary_symbol") == symbol
            and d.metadata.get("entity_kind") == symbol_kind
        ]
        for doc in matches:
            if str(doc.id) in self.verified_cards:
                continue
            relative = self.page_relatives[doc.metadata["document_id"]]
            source = self.source_root / relative
            if driver.sha(source) != doc.metadata["source_sha256"]:
                raise ValueError("symbol original source changed")
            # Verify the indexed card against the existing source parser, not just a filename.
            loader = AbaqusDocumentLoader(self.source_root)
            rebuilt = [
                d
                for d in loader._load_page(source, relative.split("/")[0])
                if d.id == doc.id
            ]
            if (
                len(rebuilt) != 1
                or rebuilt[0].page_content != doc.page_content
                or rebuilt[0].metadata["release"] != doc.metadata["release"]
            ):
                raise ValueError("symbol card does not match verified source")
            self.verified_cards.add(str(doc.id))
        return matches

    def assert_unchanged(self):
        if (
            driver.embedding_digest(self.vector) != self.binding["embeddings_sha256"]
            or driver.dense_vector_storage_snapshot(
                self.candidate / "index", collection_name=driver.COLLECTION
            ).model_dump(mode="json")
            != self.binding["vector"]
        ):
            raise ValueError("candidate vector changed during run")
        if self.symbol_binding:
            manifest = driver.read(self.symbol_index / "manifest.json")
            if (
                driver.sha(self.symbol_index / "manifest.json")
                != self.symbol_binding["manifest"]
                or driver.dense_vector_storage_snapshot(
                    self.symbol_index, collection_name=manifest["collection_name"]
                ).model_dump(mode="json")
                != self.symbol_binding["vector"]
            ):
                raise ValueError("symbol index changed during run")
