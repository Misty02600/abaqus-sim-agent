"""固定 RAG 候选的薄组合与宿主评分；不修改检索算法或产品工具。"""

import hashlib
import importlib.metadata
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import evidence_candidate as io
from .equation_context_delivery import deliver_with_context_references
from .ingestion_fixes_candidate import check_relations
from .unified_candidate_backend import BoundCandidateBackend, FixedRanker
from .unified_retrieval import CandidateBudget, Query, UnifiedRetrievalCandidate

PROFILE = "rag-evidence-baseline-v1"
SYMBOL_VECTOR_SHA256 = (
    "6a4d1d016d4371ddf0016ef6812853369a0225c3ce656fd8ab6c9c2af8c42896"
)
BUDGET = CandidateBudget()
OPTIONS = {
    "retrieval_mode": "hybrid",
    "query_text_mode": "rerank_question_only",
    "pool_policy": "page_lexical",
}
FIXED_HASHES = {
    "prepared": "fed06ace27237275abec5b1c29e973761093af0650fa21b0070da49dc3c28a0b",
    "index": "a266e22ca3805f8fea7efbb3f25d87163b679d6006260102ce82c2c8ae7811eb",
    "scopes": "09ccf2cd932a9e61d9dca0dfd0559f59c3a20189e66c1c00f550c44c5b699ca5",
    "ranker": "b4787f727622d776e65cccc1ddf58b1753748dc662328104131874d7f64dbc17",
    "symbols": "9671bd588940438ef1edfc067ac9bb8b26b96d6be57c0c4740b70bd2aafb5cc1",
}
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=120)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EvidenceRequirement(_Contract):
    id: Identifier
    acceptable_unit_sets: tuple[tuple[Digest, ...], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_sets(self):
        if any(
            not group or len(set(group)) != len(group)
            for group in self.acceptable_unit_sets
        ):
            raise ValueError("each alternative needs distinct, nonempty required units")
        if len({frozenset(g) for g in self.acceptable_unit_sets}) != len(
            self.acceptable_unit_sets
        ):
            raise ValueError("duplicate requirement alternatives")
        return self


class SourceTarget(_Contract):
    id: Identifier
    unit_id: Digest
    member_id: Digest
    source_sha256: Digest
    source_xpath: str = Field(min_length=1)
    body_sha256: Digest
    require_context_reference: bool = False


class BaselineCase(_Contract):
    id: Identifier
    request: Query
    quality_scope: Literal["annotated", "unannotated", "unanswerable"] = "unannotated"
    requirements: tuple[EvidenceRequirement, ...] = ()
    source_targets: tuple[SourceTarget, ...] = ()
    expected_status: (
        Literal["found", "not_found", "query_too_long", "reranker_input_too_long"]
        | None
    ) = None

    @model_validator(mode="after")
    def check_labels(self):
        if (self.quality_scope == "annotated") != bool(self.requirements):
            raise ValueError(
                "annotated scope requires labels; other scopes must not contain them"
            )
        if self.request.kind == "symbol" and (self.requirements or self.source_targets):
            raise ValueError(
                "symbol cases use status checks, not semantic evidence labels"
            )
        for rows in (self.requirements, self.source_targets):
            if len({r.id for r in rows}) != len(rows):
                raise ValueError("duplicate label id")
        return self


class BaselineBatch(_Contract):
    profile: Literal["rag-evidence-baseline-v1"] = PROFILE
    cases: tuple[BaselineCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate case id")
        return self


class RagEvidenceBaseline:
    def __init__(
        self,
        candidate: Path,
        symbol_index: Path,
        scope_rules: Path,
        ranker_directory: Path,
    ):
        """加载固定内容快照后组合既有搜索与关联交付，不接受运行时算法覆盖。

        Args:
            candidate: 已构建且与本 profile 内容绑定一致的候选目录。
            symbol_index: 既有 exact 卡片索引；不合并它的语义记录。
            scope_rules: 已冻结的官方/审阅范围规则文件。
            ranker_directory: 已获取的固定重排模型，不能在这里自动下载。

        Note:
            构造只验证和读取，不发起查询。调用方应在批次结束调用 assert_unchanged；
            漂移不能形成通过的评测结果。这里不是 OS 安全隔离或并发更新服务。
        """
        self.candidate = candidate
        self.symbol_index = symbol_index
        self.ranker_directory = ranker_directory
        self.files = {
            "prepared": candidate / "prepared.json",
            "index": candidate / "index-binding.json",
            "scopes": scope_rules,
            "ranker": ranker_directory / "acquisition.json",
            "symbols": symbol_index / "manifest.json",
        }
        self._check_preset()
        self.backend = BoundCandidateBackend(candidate, symbol_index, scope_rules)
        self.units = self.backend.units
        self.references = check_relations(candidate, self.units)
        children, _ = io._load(candidate)
        self.child_to_parent = {str(d.id): d.metadata["parent_id"] for d in children}
        self.ranker = FixedRanker(ranker_directory)
        self.service = UnifiedRetrievalCandidate(
            self.backend,
            self.ranker,
            self.backend.parents,
            self.backend.scopes,
            self.backend.verified_sources,
            embedding_count=self.backend.split.count,
            delivery_count=self.count,
            delivery_tokenizer="MiniLM WordPiece proxy, NOT provider billing tokens",
            budget=BUDGET,
        )
        self.binding = {
            "profile": PROFILE,
            "symbol_vector_sha256": SYMBOL_VECTOR_SHA256,
            "files": dict(FIXED_HASHES),
            "dataset": self.backend.dataset_binding,
            "budget": BUDGET.model_dump(mode="json"),
            "options": dict(OPTIONS),
        }

    def _check_preset(self):
        for key, path in self.files.items():
            if io.sha(path) != FIXED_HASHES[key]:
                raise ValueError("baseline profile content mismatch: " + key)
        symbol_manifest = io.read(self.files["symbols"])
        if (
            io.dense_vector_storage_snapshot(
                self.symbol_index, collection_name=symbol_manifest["collection_name"]
            ).sha256
            != SYMBOL_VECTOR_SHA256
        ):
            raise ValueError("baseline symbol vector changed")
        manifest = io.read(self.candidate / "manifest.json")
        for package, version in manifest["packages"].items():
            if importlib.metadata.version(package) != version:
                raise ValueError("baseline runtime package changed: " + package)

    def count(self, text: str) -> int:
        return len(
            self.backend.split.tokenizer.encode(text, add_special_tokens=False).ids
        )

    def search(self, request: Query) -> dict[str, Any]:
        initial = self.service.search(
            request,
            retrieval_mode="hybrid",
            query_text_mode="rerank_question_only",
            pool_policy="page_lexical",
        )
        started = time.perf_counter()
        result = deliver_with_context_references(
            initial,
            self.references,
            count=self.count,
            context_tokens=BUDGET.context_tokens,
        )
        self.validate_result(request, result)
        result["baseline"] = {
            "profile": PROFILE,
            "context_reference_delivery_seconds": time.perf_counter() - started,
        }
        return result

    def validate_result(self, request: Query, result: Mapping[str, Any]) -> None:
        evidence = result["evidence"]
        if len(evidence) > BUDGET.output_units or len(
            {e["id"] for e in evidence}
        ) != len(evidence):
            raise ValueError("invalid delivered unit count")
        tokens = self.count(
            json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        )
        if tokens > BUDGET.context_tokens or (
            "context_tokens" in result and result["context_tokens"] != tokens
        ):
            raise ValueError("invalid serialized evidence budget")
        for item in evidence:
            key = item["id"]
            if not self.service._scope(key).permits(request.product):
                raise ValueError("delivered applicability mismatch")
            if (
                request.source_release is not None
                and item["release"] != request.source_release
            ):
                raise ValueError("delivered release mismatch")
            if self.count(item["body"]) > BUDGET.unit_body_tokens:
                raise ValueError("delivered body overflow")
            if request.kind == "question":
                parent = self.units[key].evidence
                if (
                    item["body"] != parent.body
                    or item["source_sha256"] != parent.source_sha256
                ):
                    raise ValueError("delivered source body mismatch")
                refs = self.references.get(key)
                expected_scope = (
                    "complete_source_paragraph_with_equation_context_reference"
                    if refs
                    else "complete_stored_evidence_unit"
                )
                if (
                    item["body_scope"] != expected_scope
                    or item.get("equation_context") != refs
                ):
                    raise ValueError("missing or changed equation context delivery")
            elif item["body_scope"] != "indexed_symbol_card":
                raise ValueError("invalid symbol body scope")

    def assert_unchanged(self) -> None:
        self._check_preset()
        self.backend.assert_unchanged()
        io._check_binding(self.candidate)
        io._load(self.candidate)
        if check_relations(self.candidate, self.units) != self.references:
            raise ValueError("equation references changed during batch")
        lexical = self.candidate / "index/lexical"
        if (
            io.sha(lexical / "manifest.json")
            != self.backend.binding["bm25_manifest_sha256"]
        ):
            raise ValueError("BM25 manifest changed")
        if (
            io.sha(lexical / self.backend.sparse.manifest.database_filename)
            != self.backend.binding["bm25_database_sha256"]
        ):
            raise ValueError("BM25 database changed")
        for name, record in self.ranker.acquisition["files"].items():
            if io.sha(self.ranker_directory / name) != record["sha256"]:
                raise ValueError("reranker file changed")


def _parents(ids: Sequence[str], mapping: Mapping[str, str]) -> list[str]:
    return list(dict.fromkeys(mapping[key] for key in ids))


def score_case(
    case: BaselineCase,
    result: Mapping[str, Any],
    units: Mapping[str, Any],
    child_to_parent: Mapping[str, str],
) -> dict[str, Any]:
    """依据已标注必要证据做阶段评分；标注绝不进入检索服务。

    Note:
        同一 requirement 的 alternatives 为 OR，每组内全部单元为 AND。
        RR@64 只取首个已标注必要父单元，不表示多证据完整覆盖。缺标签不评分，
        有标签的运行失败为零；源段目标独立计分，不改变原 requirements 分母。
    """
    if result["status"] not in {
        "found",
        "not_found",
        "query_too_long",
        "reranker_input_too_long",
        "execution_error",
    }:
        raise ValueError("unknown retrieval status")
    # A failed request cannot earn quality credit from leftover partial evidence.
    if result["status"] == "execution_error":
        result = {"status": "execution_error", "evidence": []}
    elif (result["status"] == "found") != bool(result["evidence"]):
        raise ValueError("retrieval status and evidence disagree")
    trace = result.get("trace", {})
    dense = _parents(trace.get("dense_ids", []), child_to_parent)
    lexical = _parents(trace.get("lexical_ids", []), child_to_parent)
    pool = _parents(trace.get("pool_ids", []), child_to_parent)
    ranked = _parents([r["id"] for r in trace.get("ranking", [])], child_to_parent)
    if len(trace.get("ranking", [])) > BUDGET.candidates:
        raise ValueError("ranking exceeds baseline budget")
    delivered = set()
    for item in result["evidence"]:
        if case.request.kind == "question":
            if (
                item["id"] not in units
                or units[item["id"]].evidence.body != item["body"]
            ):
                raise ValueError("scoring received an unverified delivered body")
        delivered.add(item["id"])
    stage_sets = {
        "candidate": set(pool),
        "rerank_top4": set(ranked[:4]),
        "delivered": delivered,
    }
    requirements = []
    for req in case.requirements:
        requirements.append(
            {
                "id": req.id,
                **{
                    stage: any(
                        set(group) <= found for group in req.acceptable_unit_sets
                    )
                    for stage, found in stage_sets.items()
                },
            }
        )
    expected_units = {
        k for req in case.requirements for g in req.acceptable_unit_sets for k in g
    }
    first_rank = next(
        (i + 1 for i, key in enumerate(ranked[:64]) if key in expected_units), None
    )
    scored = case.quality_scope == "annotated"
    targets = []
    searchable_ids = set(child_to_parent.values())
    for target in case.source_targets:
        unit = units.get(target.unit_id)
        available = bool(
            unit
            and target.member_id in unit.member_ids
            and target.source_xpath in unit.member_xpaths
            and unit.evidence.source_sha256 == target.source_sha256
            and hashlib.sha256(unit.evidence.body.encode()).hexdigest()
            == target.body_sha256
        )
        item = next((e for e in result["evidence"] if e["id"] == target.unit_id), None)
        exact = bool(
            available
            and item
            and hashlib.sha256(item["body"].encode()).hexdigest() == target.body_sha256
        )
        context = bool(item and item.get("equation_context"))
        child_ranks = [
            i + 1
            for i, r in enumerate(trace.get("ranking", []))
            if child_to_parent[r["id"]] == target.unit_id
        ]
        targets.append(
            {
                "id": target.id,
                "unit_id": target.unit_id,
                "source_available": available,
                "searchable": bool(
                    available
                    and unit is not None
                    and unit.evidence.projection_status == "text"
                    and target.unit_id in searchable_ids
                ),
                "in_dense": target.unit_id in dense,
                "in_bm25": target.unit_id in lexical,
                "in_pool": target.unit_id in pool,
                "best_child_rerank": min(child_ranks) if child_ranks else None,
                "delivered_exact_atomic_body": exact,
                "context_reference_delivered": context,
                "delivered_complete": exact
                and (context or not target.require_context_reference),
            }
        )
    return {
        "case_id": case.id,
        "quality_scope": case.quality_scope,
        "status": result["status"],
        "guard_pass": result["status"] == case.expected_status
        if case.expected_status
        else None,
        "requirements": requirements,
        "requirement_coverage": {
            stage: sum(r[stage] for r in requirements) / len(requirements)
            if scored
            else None
            for stage in stage_sets
        },
        "complete_evidence": {
            stage: all(r[stage] for r in requirements) if scored else None
            for stage in stage_sets
        },
        "first_necessary_parent_rank": first_rank,
        "necessary_evidence_rr_at_64": (1 / first_rank if first_rank else 0.0)
        if scored
        else None,
        "source_targets": targets,
        "stage_status": {
            "retrieval": "executed"
            if "dense" in trace.get("latency_seconds", {})
            else "not_executed",
            "rerank": "executed"
            if "rerank" in trace.get("latency_seconds", {})
            else "not_executed",
            "delivery": "executed" if "context_tokens" in result else "not_executed",
        },
        "cost": {
            "reranked_pairs": len(trace.get("ranking", [])),
            "context_tokens": result.get("context_tokens"),
            "latency_seconds": trace.get("latency_seconds", {}),
            "baseline": result.get("baseline", {}),
        },
        "rejected": trace.get("rejected", []),
        "evidence": [
            {
                k: e[k]
                for k in ("id", "source", "source_sha256", "release", "body_scope")
            }
            | {"body_sha256": hashlib.sha256(e["body"].encode()).hexdigest()}
            for e in result["evidence"]
        ],
    }


def aggregate_scores(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    annotated = [r for r in rows if r["quality_scope"] == "annotated"]
    total = sum(len(r["requirements"]) for r in annotated)
    stages = {}
    for stage in ("candidate", "rerank_top4", "delivered"):
        matched = sum(req[stage] for row in annotated for req in row["requirements"])
        complete = sum(row["complete_evidence"][stage] for row in annotated)
        stages[stage] = {
            "requirements_matched": matched,
            "requirements_total": total,
            "requirement_recall": matched / total if total else None,
            "complete_cases": complete,
            "scored_cases": len(annotated),
            "complete_evidence_rate": complete / len(annotated) if annotated else None,
        }
    return {
        "cases": len(rows),
        "quality_scored_cases": len(annotated),
        "stages": stages,
        "necessary_evidence_mrr_at_64": sum(
            r["necessary_evidence_rr_at_64"] for r in annotated
        )
        / len(annotated)
        if annotated
        else None,
        "guard_checks": sum(r["guard_pass"] is not None for r in rows),
        "guard_passes": sum(r["guard_pass"] is True for r in rows),
        "execution_errors": sum(r["status"] == "execution_error" for r in rows),
        "source_targets": {
            "checks": sum(len(r["source_targets"]) for r in rows),
            "delivered_complete": sum(
                t["delivered_complete"] for r in rows for t in r["source_targets"]
            ),
        },
        "reranked_pairs": sum(r["cost"]["reranked_pairs"] for r in rows),
        "quality_gate": "not_defined_not_a_product_acceptance_score",
    }
