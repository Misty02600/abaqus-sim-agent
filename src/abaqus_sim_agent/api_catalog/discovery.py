"""同一Catalog的离线发现视图；不进入Coder工具列表。"""

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from itertools import zip_longest
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..retrieval.bm25 import BM25_B, BM25_K1, _bm25_term_score, tokenize_abaqus_lexical
from .catalog import ApiCatalog
from .intent import DiscoveryIntent
from .models import ApiMethodCard

PROJECTION = "api-discovery-view-v1"
PROJECTION_V2 = "api-discovery-view-v2"
Projection = Literal["api-discovery-view-v1", "api-discovery-view-v2"]
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SYMBOL = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class View(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    card_id: str
    name: str
    kind: str
    text: str
    content_sha256: str
    shortened: bool = False


class DiscoveryManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    projection: Projection = PROJECTION
    runtime_release: str
    methods_sha256: str
    members_sha256: str | None
    views_sha256: str
    count: int = Field(gt=0)
    vectors_sha256: str | None = None
    encoder: dict[str, str] | None = None

    @model_validator(mode="after")
    def check_dense_pair(self):
        if (self.vectors_sha256 is None) != (self.encoder is None):
            raise ValueError("vectors and encoder binding must be present together")
        return self


def make_views(
    catalog: ApiCatalog, projection: Projection = PROJECTION
) -> tuple[View, ...]:
    if projection not in (PROJECTION, PROJECTION_V2):
        raise ValueError("unsupported discovery projection")
    views = []
    for card in (*catalog.cards, *catalog.members):
        if isinstance(card, ApiMethodCard):
            summaries = [r.summary for r in card.official_records]
            summaries += [
                d.summary for r in card.abqpy_records for d in (r.documentation or ())
            ]
            parameters = list(
                dict.fromkeys(p.name for s in card.signatures for p in s.parameters)
            )
            text = "\n".join(
                [
                    card.canonical_name,
                    *dict.fromkeys(s for s in summaries if s),
                    "Parameters: " + ", ".join(parameters),
                ]
            )
        else:
            text = f"{card.canonical_name}\n{card.description}\nType: {card.annotation}"
        shortened = len(text) > 1000
        if projection == PROJECTION_V2 and isinstance(card, ApiMethodCard):
            text, shortened = _v2_text(card)
        # A bounded index view is not the full answer. Full records stay in Catalog.
        views.append(
            View(
                card_id=card.card_id,
                name=card.canonical_name,
                kind=card.entity_kind,
                text=text[:1000],
                shortened=shortened,
                content_sha256=sha(card.model_dump_json().encode()),
            )
        )
    return tuple(sorted(views, key=lambda v: v.card_id))


def _excerpt(text: str, budget: int) -> str:
    # Only complete sentences/lines; this is a locator excerpt, not a full contract.
    units = re.split(r"(?<=[.!?])\s+|\n", text.strip())
    selected = []
    for unit in units:
        if len(" ".join([*selected, unit])) > budget:
            break
        selected.append(unit)
    return " ".join(selected)


def _v2_text(card: ApiMethodCard) -> tuple[str, bool]:
    """按统一优先顺序形成定位视图，不替代完整Card。

    Returns:
        最多1000字符的视图与省略标记。对象用途、变体及路径、表格、参数依次选取，
        官方参数跨变体交错；只保留完整句/行，不依据查询或gold选择内容。
    """
    descriptions, variants, tables, parameters = [], [], [], []
    parameter_groups = []
    shortened = False
    for i, record in enumerate(card.official_records, 1):
        label = f"Official variant {i}"
        for context in record.context or ():
            if context.kind == "object_description":
                excerpt = _excerpt(context.text, 350)
                descriptions.append(f"Object context: {excerpt}")
            else:
                excerpt = _excerpt(context.text, 450)
                tables.append(f"{label} table excerpt: {excerpt}")
            shortened |= excerpt != " ".join(context.text.split())
        summary = _excerpt(record.summary, 280)
        shortened |= summary != " ".join(record.summary.split())
        variants.append(f"{label}: {summary}")
        for path in record.paths:
            variants.append(f"{label} public path: {path}")
        group = []
        for p in (*record.required_parameters, *record.optional_parameters):
            description = _excerpt(p.description, 220)
            shortened |= description != " ".join(p.description.split())
            group.append(f"{label} {p.name}: {description}")
        parameter_groups.append(group)
    parameters.extend(
        item
        for row in zip_longest(*parameter_groups)
        for item in row
        if item is not None
    )
    # Stub variants remain individually labelled; no inferred factory/constructor merge.
    for i, record in enumerate(card.abqpy_records, 1):
        for j, doc in enumerate(record.documentation or (), 1):
            if not doc.raw:
                continue
            label = f"Stub {i} variant {j}"
            summary = _excerpt(doc.summary, 280)
            # Exact duplicate prose adds no information; source identity stays in the Card.
            if not any(r.summary == doc.summary for r in card.official_records):
                variants.append(f"{label}: {summary}")
            shortened |= summary != " ".join(doc.summary.split())
            for name, value in doc.parameters.items():
                if any(
                    p.name == name and p.description == value
                    for r in card.official_records
                    for p in (*r.required_parameters, *r.optional_parameters)
                ):
                    continue
                excerpt = _excerpt(value, 220)
                shortened |= excerpt != " ".join(value.split())
                parameters.append(f"{label} {name}: {excerpt}")
    text = card.canonical_name
    for fragment in dict.fromkeys([*descriptions, *variants, *tables, *parameters]):
        if len(text) + 1 + len(fragment) <= 1000:
            text += "\n" + fragment
        else:
            shortened = True
    return text, shortened


def tokens(text: str) -> list[str]:
    # Preserve the original text in query/results; this is only an index analyzer.
    expanded = _CAMEL.sub(" ", text)
    expanded = re.sub(
        r"(?<=[\u3400-\u9fff])(?=[A-Za-z])|(?<=[A-Za-z])(?=[\u3400-\u9fff])",
        " ",
        expanded,
    )
    return tokenize_abaqus_lexical(text) + tokenize_abaqus_lexical(expanded)


class CatalogDiscovery:
    def __init__(
        self,
        catalog: ApiCatalog,
        views: Sequence[View],
        manifest: DiscoveryManifest,
        vectors: np.ndarray | None = None,
        *,
        lexical_profile: Literal["legacy", "english-snowball-v1"] = "legacy",
    ):
        self.catalog, self.views, self.manifest, self.vectors = (
            catalog,
            tuple(views),
            manifest,
            vectors,
        )
        self.by_id = {v.card_id: v for v in self.views}
        self.cards_by_id = {c.card_id: c for c in (*catalog.cards, *catalog.members)}
        self.lexical_binding = None
        self._analyze = tokens
        if lexical_profile == "english-snowball-v1":
            from .lexical import EnglishLexicalAnalyzer

            analyzer = EnglishLexicalAnalyzer()
            self._analyze = analyzer
            self.lexical_binding = analyzer.binding
        elif lexical_profile != "legacy":
            raise ValueError("unknown lexical profile")
        self.counts = [Counter(self._analyze(v.text)) for v in self.views]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = sum(self.lengths) / len(self.lengths)
        self.frequencies = Counter(term for counts in self.counts for term in counts)

    @classmethod
    def build(
        cls,
        catalog: ApiCatalog,
        directory: Path,
        *,
        encoder=None,
        projection: Projection = PROJECTION,
    ):
        if catalog.manifest is None:
            raise ValueError("discovery requires a persisted catalog")
        directory.mkdir(parents=True, exist_ok=False)
        views = make_views(catalog, projection)
        payload = "".join(v.model_dump_json() + "\n" for v in views).encode()
        (directory / "views.jsonl").write_bytes(payload)
        vector_hash, binding = None, None
        if encoder is not None:
            vectors = encoder.encode([v.text for v in views], query=False)
            np.save(directory / "vectors.npy", vectors, allow_pickle=False)
            vector_hash, binding = (
                sha((directory / "vectors.npy").read_bytes()),
                encoder.binding,
            )
        manifest = DiscoveryManifest(
            projection=projection,
            runtime_release=catalog.runtime_release,
            methods_sha256=catalog.manifest.cards_sha256,
            members_sha256=catalog.manifest.members_sha256,
            views_sha256=sha(payload),
            count=len(views),
            vectors_sha256=vector_hash,
            encoder=binding,
        )
        (directory / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        return cls.open(catalog, directory)

    @classmethod
    def open(
        cls,
        catalog: ApiCatalog,
        directory: Path,
        *,
        lexical_profile: Literal["legacy", "english-snowball-v1"] = "legacy",
    ):
        manifest = DiscoveryManifest.model_validate_json(
            (directory / "manifest.json").read_bytes()
        )
        if catalog.manifest is None or (
            manifest.methods_sha256,
            manifest.members_sha256,
        ) != (catalog.manifest.cards_sha256, catalog.manifest.members_sha256):
            raise ValueError("discovery catalog binding mismatch")
        if manifest.runtime_release != catalog.runtime_release:
            raise ValueError("discovery runtime release mismatch")
        payload = (directory / "views.jsonl").read_bytes()
        if sha(payload) != manifest.views_sha256:
            raise ValueError("discovery view checksum mismatch")
        views = tuple(View.model_validate_json(line) for line in payload.splitlines())
        if len(views) != manifest.count or views != make_views(
            catalog, manifest.projection
        ):
            raise ValueError("discovery view content mismatch")
        vectors = None
        if manifest.vectors_sha256 is not None:
            if sha((directory / "vectors.npy").read_bytes()) != manifest.vectors_sha256:
                raise ValueError("discovery vector checksum mismatch")
            vectors = np.load(directory / "vectors.npy", allow_pickle=False)
            if (
                vectors.ndim != 2
                or vectors.shape[0] != len(views)
                or not np.isfinite(vectors).all()
            ):
                raise ValueError("invalid discovery vectors")
            if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3):
                raise ValueError("discovery vectors must be normalized")
        return cls(catalog, views, manifest, vectors, lexical_profile=lexical_profile)

    def search(
        self,
        question: str,
        *,
        strategy: Literal["bm25", "dense"] = "bm25",
        limit: int = 5,
        encoder=None,
    ) -> dict:
        if not question.strip() or len(question) > 8000 or not 2 <= limit <= 10:
            raise ValueError(
                "query must be nonblank, <=8000 characters; limit must be 2..10"
            )
        result = self.catalog.lookup(question.strip(), limit=limit)
        common = {
            "runtime_release": self.catalog.runtime_release,
            "original_query": question,
            "protected_symbols": list(dict.fromkeys(_SYMBOL.findall(question))),
            "projection": self.manifest.projection,
        }
        if self.lexical_binding is not None and strategy == "bm25":
            common["lexical_configuration"] = self.lexical_binding
        if question.strip() in self.by_id:
            return {
                **common,
                "route": "exact",
                "status": "found",
                "candidates": [self._candidate(question.strip())],
            }
        if result.status != "not_found" or _SYMBOL.fullmatch(question.strip()):
            return {
                **common,
                "route": "exact",
                "status": result.status,
                "candidates": [
                    self._candidate(c.card_id) for c in (*result.cards, *result.members)
                ],
            }
        if strategy == "bm25":
            query = set(self._analyze(question))
            scores = [
                sum(
                    _bm25_term_score(
                        term_frequency=c[t],
                        document_frequency=self.frequencies[t],
                        document_length=self.lengths[i],
                        document_count=len(self.views),
                        average_document_length=self.average,
                        k1=BM25_K1,
                        b=BM25_B,
                    )
                    for t in query
                    if c[t]
                )
                for i, c in enumerate(self.counts)
            ]
        elif strategy == "dense":
            if (
                encoder is None
                or self.vectors is None
                or encoder.binding != self.manifest.encoder
            ):
                raise ValueError("matching encoder and vector index required")
            q = encoder.encode([question], query=True)[0]
            scores = (self.vectors @ q).tolist()
        else:
            raise ValueError("unsupported discovery strategy")
        ranked = sorted(
            range(len(scores)), key=lambda i: (-scores[i], self.views[i].card_id)
        )
        selected = [i for i in ranked if strategy == "dense" or scores[i] > 0][:limit]
        return {
            **common,
            "route": strategy,
            "status": "candidates" if selected else "no_candidates",
            "notice": "Candidates are not verified applicability. Read the exact card before use.",
            "candidates": [
                {**self._candidate(self.views[i].card_id), "score": scores[i]}
                for i in selected
            ],
        }

    def read(self, card_id: str):
        if card_id not in self.by_id:
            raise ValueError("unknown discovery reference")
        return self.cards_by_id[card_id]

    def search_intent(
        self,
        intent: DiscoveryIntent,
        *,
        strategy: Literal["bm25", "dense"] = "bm25",
        limit: int = 5,
        encoder=None,
    ) -> dict:
        """只对操作表达排名，原需求原样返回供读取和采用时核对。

        Note:
            宿主应从自身任务记录构造original_request，不能让模型覆盖它。本入口不自动
            提取或认证物理约束，也不证明operation保真；候选适用性仍未验证。
        """
        from .lexical import source_symbols

        result = self.search(
            intent.operation, strategy=strategy, limit=limit, encoder=encoder
        )
        return {
            **result,
            "original_query": intent.original_request,
            "retrieval_query": intent.operation,
            "intent": {
                **intent.model_dump(),
                "original_sha256": intent.original_sha256,
                "source_symbols": list(source_symbols(intent.original_request)),
            },
            "adoption_notice": "Original request remains authoritative. Read candidate sources and check ownership, current/target state and all original constraints before adoption. Ranking is not applicability verification.",
        }

    def _candidate(self, card_id: str) -> dict:
        card = self.read(card_id)
        sources = (
            [r.source for r in (*card.official_records, *card.abqpy_records)]
            if isinstance(card, ApiMethodCard)
            else [card.source]
        )
        unique = {s.model_dump_json(): s for s in sources}
        return {
            **self.by_id[card_id].model_dump(),
            "sources": [s.model_dump(mode="json") for s in unique.values()],
            "release_alignment": card.release_alignment,
            "lookup_name": card.canonical_name,
        }
