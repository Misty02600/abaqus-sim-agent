from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.retrieval import AbaqusDocumentIndex, RetrievalHit
from abaqus_sim_agent.retrieval.comparison import load_comparison_cases
from abaqus_sim_agent.retrieval.heldout import (
    AcceptablePage,
    AnnotationMetadata,
    AuditedHeldout,
    FamilyRegistry,
    HeldoutAuditReport,
    HeldoutEvaluation,
    HeldoutLabel,
    HeldoutQuery,
    HeldoutQueryOrigin,
    HeldoutRequirement,
    HeldoutSeal,
    _score_requirements,
    evaluate_heldout,
    load_heldout_labels,
    load_heldout_queries,
)
from abaqus_sim_agent.retrieval.types import Document

BENCHMARK_ROOT = Path(__file__).parent.parent / "benchmarks" / "rag"


def _page(document_id: str) -> AcceptablePage:
    return AcceptablePage(
        document_id=document_id,
        source_path=f"guide/{document_id}.htm",
        source_sha256="0" * 64,
        section_ids=(document_id,),
        evidence_paraphrase=f"Evidence for {document_id}.",
        family_ids=(f"family-{document_id}",),
    )


def _hit(document_id: str) -> RetrievalHit:
    return RetrievalHit(
        document=Document(
            id=f"{document_id}#chunk",
            page_content=document_id,
            metadata={"document_id": document_id},
        ),
        match_type="semantic",
    )


def test_heldout_query_schema_rejects_retrieval_side_channels() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HeldoutQuery.model_validate(
            {
                "schema_version": "1",
                "dataset_id": "heldout-v1",
                "case_id": "case",
                "stratum": "concept",
                "query": "How does the model behave?",
                "origin": {
                    "kind": "predefined_scenario",
                    "source_ref": "scenario-v1",
                },
                "exact_terms": ["ANSWER"],
            }
        )


def test_repository_heldout_query_versions_are_frozen_before_retrieval() -> None:
    versions = {
        "v1": "6afdc3ace17c1324903e0690cc344e217bd1defe7b765ab11e43a10a3c68bbb9",
        "v2": "ae100f44ebe68cb8f20734b11e6ddb3b1702884ecf9235ec75c86afda781ca36",
        "v3": "42e11d2e01e66b9325c5ab85d5625716d6ade31fbade69e9f5d2da07775c46eb",
    }
    for version, expected_hash in versions.items():
        path = (
            BENCHMARK_ROOT
            / f"abaqus_2026_bm25_dense_rrf_heldout_queries_{version}.jsonl"
        )
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
        queries = load_heldout_queries(path)
        assert len(queries) == 30
        assert len({query.case_id for query in queries}) == 30
        assert (
            len({" ".join(query.query.casefold().split()) for query in queries}) == 30
        )

    v3_queries = load_heldout_queries(
        BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf_heldout_queries_v3.jsonl"
    )
    assert Counter(query.stratum for query in v3_queries) == {
        "concept": 12,
        "identifier_context": 12,
        "multi_document": 6,
    }
    development = load_comparison_cases(
        BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf.jsonl"
    )
    development_queries = {
        " ".join(case.query.casefold().split()) for case in development
    }
    assert not development_queries.intersection(
        " ".join(query.query.casefold().split()) for query in v3_queries
    )

    for version in ("v1", "v2"):
        withdrawal = json.loads(
            (
                BENCHMARK_ROOT
                / f"abaqus_2026_bm25_dense_rrf_heldout_{version}_withdrawal.json"
            ).read_text(encoding="utf-8")
        )
        assert withdrawal["status"] == "withdrawn_before_label_seal_and_retrieval"
        assert withdrawal["retrieval_rankings_observed"] is False
        assert withdrawal["queries_sha256"] == versions[version]


def test_repository_v3_reviewed_labels_and_declared_families_are_disjoint() -> None:
    query_path = BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf_heldout_queries_v3.jsonl"
    label_path = (
        BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf_heldout_labels_v3_reviewed.jsonl"
    )
    family_path = (
        BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf_heldout_families_v3_reviewed.json"
    )
    assert hashlib.sha256(query_path.read_bytes()).hexdigest() == (
        "42e11d2e01e66b9325c5ab85d5625716d6ade31fbade69e9f5d2da07775c46eb"
    )
    assert hashlib.sha256(label_path.read_bytes()).hexdigest() == (
        "32c51082472265c1c3cbd9871927953e9f256496fb96a4e13be5f3a9be2d1a7f"
    )
    assert hashlib.sha256(family_path.read_bytes()).hexdigest() == (
        "a268ef641aa9034da82dd35f9cb1ce9750a205f3d9757ae948cd3bc14a1afaa2"
    )

    queries = load_heldout_queries(query_path)
    labels = load_heldout_labels(label_path)
    registry = FamilyRegistry.model_validate_json(
        family_path.read_text(encoding="utf-8")
    )
    assert [query.case_id for query in queries] == [label.case_id for label in labels]
    assert all(
        label.annotation.reviewer_id == "codex-heldout-preseal-direct-html-review"
        and label.annotation.reviewed_at is not None
        for label in labels
    )
    assert sum(len(label.requirements) for label in labels) == 40
    pages = [
        page
        for label in labels
        for requirement in label.requirements
        for page in requirement.acceptable_pages
    ]
    assert len({page.document_id for page in pages}) == 38

    families = {family.family_id: family for family in registry.families}
    development_family_documents = {
        document_id
        for family in registry.families
        if family.split == "development"
        for document_id in family.member_document_ids
    }
    heldout_family_documents = {
        document_id
        for family in registry.families
        if family.split == "heldout"
        for document_id in family.member_document_ids
    }
    assert development_family_documents.isdisjoint(heldout_family_documents)
    for page in pages:
        assert page.document_id not in development_family_documents
        assert all(
            family_id in families
            and families[family_id].split == "heldout"
            and page.document_id in families[family_id].member_document_ids
            for family_id in page.family_ids
        )


def test_repository_v3_seal_and_first_evaluation_are_immutable() -> None:
    seal_path = BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf_heldout_seal_v3.json"
    report_path = (
        BENCHMARK_ROOT
        / "abaqus_2026_bm25_dense_rrf_heldout_evaluation_v3_20260903.json"
    )
    attempts_path = (
        BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf_heldout_attempts_v3.jsonl"
    )
    consumption_path = (
        BENCHMARK_ROOT / "abaqus_2026_bm25_dense_rrf_heldout_consumption_v3.json"
    )
    seal_hash = hashlib.sha256(seal_path.read_bytes()).hexdigest()
    report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert seal_hash == (
        "00e43e6dc0274d3ee60cf94c74d457004cf2172ad31d7fd19a0b248b92ff7d39"
    )
    assert report_hash == (
        "9bb5d7a6e688e0d5a3ecd598c1a252ca3234fb509514886228be9444623fd0a8"
    )

    seal = HeldoutSeal.model_validate_json(seal_path.read_text(encoding="utf-8"))
    report = HeldoutEvaluation.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    attempts = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
    ]
    consumption = json.loads(consumption_path.read_text(encoding="utf-8"))
    assert seal.status == "sealed"
    assert report.seal_sha256 == seal_hash
    assert report.audit.document_disjoint is True
    assert report.audit.declared_family_disjoint is True
    assert report.bm25.all.complete_hit_rate_at_k == pytest.approx(23 / 30)
    assert report.dense.all.complete_hit_rate_at_k == pytest.approx(24 / 30)
    assert report.hybrid.all.complete_hit_rate_at_k == pytest.approx(25 / 30)
    assert [item["event"] for item in attempts] == ["started", "completed"]
    assert attempts[0]["rankings_emitted"] is False
    assert attempts[1]["report_sha256"] == report_hash
    assert consumption["status"] == "consumed"
    assert consumption["seal_sha256"] == seal_hash
    assert consumption["report_sha256"] == report_hash
    assert consumption["post_result_tuning_allowed"] is False


def test_requirement_scoring_treats_alternatives_as_or_and_requirements_as_and() -> (
    None
):
    requirements = (
        HeldoutRequirement(
            requirement_id="procedure",
            description="Find the applicable procedure.",
            acceptable_pages=(_page("procedure-a"), _page("procedure-b")),
            rationale="Either procedure page fully covers this requirement.",
        ),
        HeldoutRequirement(
            requirement_id="output",
            description="Find the required output evidence.",
            acceptable_pages=(_page("output"),),
            rationale="Output evidence is independently required.",
        ),
    )

    partial = _score_requirements(
        [_hit("procedure-b")],
        requirements,
        elapsed_ms=1.0,
    )
    complete = _score_requirements(
        [_hit("procedure-b"), _hit("output")],
        requirements,
        elapsed_ms=2.0,
    )

    assert partial.covered_requirement_ids == ["procedure"]
    assert partial.requirement_recall == 0.5
    assert partial.complete_hit is False
    assert complete.requirement_recall == 1.0
    assert complete.complete_hit is True
    assert complete.reciprocal_rank == 1.0


def test_label_rejects_retrieval_assisted_annotation() -> None:
    with pytest.raises(ValidationError, match="cannot use retrieval assistance"):
        HeldoutLabel(
            dataset_id="heldout-v1",
            case_id="case",
            annotation_status="adjudicated",
            requirements=(
                HeldoutRequirement(
                    requirement_id="one",
                    description="Find one directly inspected page.",
                    acceptable_pages=(_page("page"),),
                    rationale="One directly inspected page.",
                ),
            ),
            annotation=AnnotationMetadata(
                method="direct_official_html",
                retrieval_assistance=("bm25",),
                annotator_id="annotator",
                completed_at=datetime.now(UTC),
            ),
        )


def test_heldout_evaluator_reuses_raw_queries_and_scores_requirements() -> None:
    now = datetime.now(UTC)
    queries = (
        HeldoutQuery(
            dataset_id="heldout-v1",
            case_id="concept",
            stratum="concept",
            query="concept query",
            origin=HeldoutQueryOrigin(
                kind="predefined_scenario",
                source_ref="scenario",
            ),
        ),
        HeldoutQuery(
            dataset_id="heldout-v1",
            case_id="multi",
            stratum="multi_document",
            query="multi query",
            origin=HeldoutQueryOrigin(
                kind="predefined_scenario",
                source_ref="scenario",
            ),
        ),
    )
    labels = (
        HeldoutLabel(
            dataset_id="heldout-v1",
            case_id="concept",
            annotation_status="adjudicated",
            requirements=(
                HeldoutRequirement(
                    requirement_id="concept",
                    description="One concept requirement.",
                    acceptable_pages=(_page("concept-gold"),),
                    rationale="Direct official evidence.",
                ),
            ),
            annotation=AnnotationMetadata(
                method="direct_official_html",
                annotator_id="annotator",
                completed_at=now,
            ),
        ),
        HeldoutLabel(
            dataset_id="heldout-v1",
            case_id="multi",
            annotation_status="adjudicated",
            requirements=(
                HeldoutRequirement(
                    requirement_id="first",
                    description="First independent requirement.",
                    acceptable_pages=(_page("first-gold"),),
                    rationale="Direct official evidence.",
                ),
                HeldoutRequirement(
                    requirement_id="second",
                    description="Second independent requirement.",
                    acceptable_pages=(_page("second-gold"),),
                    rationale="Direct official evidence.",
                ),
            ),
            annotation=AnnotationMetadata(
                method="direct_official_html",
                annotator_id="annotator",
                completed_at=now,
            ),
        ),
    )
    seal = HeldoutSeal(
        dataset_id="heldout-v1",
        protocol_version="direct-html-family-disjoint-heldout-v1",
        status="sealed",
        queries_file="queries.jsonl",
        queries_sha256="0" * 64,
        labels_file="labels.jsonl",
        labels_sha256="0" * 64,
        family_registry_file="families.json",
        family_registry_sha256="0" * 64,
        development_benchmark_file="dev.jsonl",
        development_benchmark_sha256="0" * 64,
        development_gold_document_ids=("dev",),
        development_gold_set_sha256="0" * 64,
        release="2026 FD02",
        dense_manifest_sha256="0" * 64,
        dense_embedding="fixture-embedding",
        bm25_manifest_sha256="0" * 64,
        bm25_database_sha256="0" * 64,
        semantic_corpus_sha256="0" * 64,
        bm25_algorithm="bm25-okapi-v1",
        bm25_tokenizer="abaqus-lexical-v1",
        bm25_k1=1.2,
        bm25_b=0.75,
        implementation_sha256={
            "src/abaqus_sim_agent/retrieval/bm25.py": "0" * 64,
            "src/abaqus_sim_agent/retrieval/cli.py": "0" * 64,
            "src/abaqus_sim_agent/retrieval/comparison.py": "0" * 64,
            "src/abaqus_sim_agent/retrieval/heldout.py": "0" * 64,
            "src/abaqus_sim_agent/retrieval/index.py": "0" * 64,
        },
        top_k=5,
        fetch_k=20,
        rrf_k=60,
        stratum_counts={
            "concept": 12,
            "identifier_context": 12,
            "multi_document": 6,
        },
        sealed_at=now,
    )
    audited = AuditedHeldout(
        seal=seal,
        seal_sha256="0" * 64,
        queries=queries,
        labels=labels,
        audit=HeldoutAuditReport(
            dataset_id="heldout-v1",
            query_count=2,
            label_count=2,
            requirement_count=3,
            acceptable_page_count=3,
            unique_gold_document_count=3,
            stratum_counts={"concept": 1, "multi_document": 1},
            document_disjoint=True,
            declared_family_disjoint=True,
            html_sources_verified=3,
            semantic_gold_documents_verified=3,
        ),
    )

    class FakeIndex:
        def __init__(self) -> None:
            self.bm25_queries: list[str] = []
            self.dense_queries: list[str] = []
            self.verifications = 0

        def verify_bm25_corpus(self) -> str:
            self.verifications += 1
            return "0" * 64

        def search_bm25(self, query: str, *, top_k: int = 5) -> list[RetrievalHit]:
            self.bm25_queries.append(query)
            if query == "concept query":
                return [_hit("concept-gold")]
            return [_hit("first-gold"), _hit("second-gold")]

        def search_concepts(
            self,
            query: str,
            *,
            top_k: int = 5,
        ) -> list[tuple[Document, float]]:
            self.dense_queries.append(query)
            if query == "concept query":
                return [(_hit("irrelevant").document, 0.1)]
            return [(_hit("first-gold").document, 0.1)]

    fake = FakeIndex()
    report = evaluate_heldout(
        cast(AbaqusDocumentIndex, fake),
        audited,
        warmup=False,
    )

    assert fake.bm25_queries == [query.query for query in queries]
    assert fake.dense_queries == [query.query for query in queries]
    assert fake.verifications == 2
    assert report.bm25.all.complete_hit_rate_at_k == 1.0
    assert report.dense.all.complete_hit_rate_at_k == 0.0
    assert report.dense.multi_document.requirement_recall_at_k == 0.5
    assert report.complete_hit_complementarity.bm25_only == 2
