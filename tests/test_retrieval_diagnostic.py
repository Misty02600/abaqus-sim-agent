from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.documents import Document
from pydantic import ValidationError

import abaqus_sim_agent.retrieval.diagnostic as diagnostic_module
from abaqus_sim_agent.retrieval.diagnostic import (
    AuditedDiagnosticDataset,
    DiagnosticAuditReport,
    DiagnosticCase,
    DiagnosticCaseTargets,
    DiagnosticDependencyVersions,
    DiagnosticEvaluation,
    DiagnosticGoldPage,
    DiagnosticReviewManifest,
    DiagnosticSeal,
    DiagnosticSectionTarget,
    _build_diagnostic_seal,
    _resolve_output_path,
    _score_ranking,
    _validate_cli_arguments,
    _write_text_new_atomic,
    audit_diagnostic_dataset,
    audit_diagnostic_seal,
    build_parser,
    evaluate_diagnostic_dataset,
    load_diagnostic_cases,
    normalize_observed_diagnostic,
)
from abaqus_sim_agent.retrieval.index import AbaqusDocumentIndex, RetrievalHit


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_dict(
    *,
    case_id: str,
    group_id: str,
    variant: str,
    query: str,
    source_sha256: str = "0" * 64,
    gold_sha256: str = "1" * 64,
    gold_document_id: str = "gold-page",
    gold_source_path: str = "guide/gold.htm",
) -> dict[str, Any]:
    derivation = {
        "verbatim_excerpt": "verbatim_excerpt",
        "normalized": "deterministic_normalization",
        "condition_fragment": "reviewed_condition_paraphrase",
        "contextualized": "reviewed_contextualization",
    }[variant]
    return {
        "schema_version": "1",
        "dataset_id": "diagnostic-v1",
        "case_id": case_id,
        "need_group_id": group_id,
        "origin": "observed_solver_log",
        "variant": variant,
        "query_derivation": derivation,
        "trigger_source": "solver_failure",
        "runtime_stage": "standard_solver",
        "query": query,
        "source_binding": {
            "kind": "run_artifact",
            "path": "run-1/job.msg",
            "sha256": source_sha256,
            "locator": "lines 1-2",
            "encoding": "utf-8",
            "legacy_inferred": False,
        },
        "gold_pages": [
            {
                "document_id": gold_document_id,
                "source_path": gold_source_path,
                "source_sha256": gold_sha256,
                "section_ids": ["diagnostic-condition"],
                "rationale": "The official section explains this diagnostic.",
            }
        ],
        "notes": "",
    }


def _write_dataset(path: Path, cases: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(case) + "\n" for case in cases),
        encoding="utf-8",
    )


def _case_ids_sha256(cases: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for case in cases:
        encoded = str(case["case_id"]).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _write_review(
    path: Path,
    dataset_path: Path,
    cases: list[dict[str, Any]],
) -> DiagnosticReviewManifest:
    manifest = DiagnosticReviewManifest(
        dataset_id="diagnostic-v1",
        dataset_sha256=_sha256(dataset_path),
        status="reviewed",
        review_method=("source_derivation_and_direct_html_without_retrieval_rankings"),
        reviewer_id="test-reviewer",
        case_count=len(cases),
        need_group_count=len({str(case["need_group_id"]) for case in cases}),
        case_ids_sha256=_case_ids_sha256(cases),
        reviewed_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def _document(document_id: str) -> Document:
    return Document(
        id=f"{document_id}#chunk",
        page_content=document_id,
        metadata={
            "document_id": document_id,
            "section_id": (
                "diagnostic-condition" if document_id == "gold-page" else "noise"
            ),
            "section_title": (
                "Diagnostic Condition" if document_id == "gold-page" else "Noise"
            ),
        },
    )


class _VectorStore:
    def __init__(
        self,
        document_ids: list[str],
        *,
        section_id: str = "diagnostic-condition",
        section_title: str = "Diagnostic Condition",
    ) -> None:
        self.document_ids = document_ids
        self.section_id = section_id
        self.section_title = section_title

    def get(self, **_kwargs: object) -> dict[str, object]:
        return {
            "metadatas": [
                {
                    "document_id": document_id,
                    "section_id": self.section_id,
                    "section_title": self.section_title,
                }
                for document_id in self.document_ids
            ]
        }


class _AuditIndex:
    def __init__(
        self,
        document_ids: list[str],
        *,
        section_id: str = "diagnostic-condition",
        section_title: str = "Diagnostic Condition",
    ) -> None:
        self.vector_store = _VectorStore(
            document_ids,
            section_id=section_id,
            section_title=section_title,
        )


class _EvaluationIndex:
    def __init__(self, root: Path, gold_at_five_queries: set[str]) -> None:
        self.manifest_path = root / "manifest.json"
        self.bm25_manifest_path = root / "bm25_manifest.json"
        self.manifest_path.write_text("dense", encoding="utf-8")
        self.bm25_manifest_path.write_text("bm25", encoding="utf-8")
        self.gold_at_five_queries = gold_at_five_queries
        self.bm25_calls: Counter[str] = Counter()
        self.dense_calls: Counter[str] = Counter()

    def verify_bm25_corpus(self) -> str:
        return "corpus"

    def read_manifest(self) -> SimpleNamespace:
        return SimpleNamespace(
            release="2026 FD02",
            collection_name="test",
            embedding="fake",
        )

    def read_bm25_manifest(self) -> SimpleNamespace:
        return SimpleNamespace(
            database_sha256="2" * 64,
            semantic_corpus_sha256="3" * 64,
            algorithm="bm25",
            tokenizer="test",
            k1=1.2,
            b=0.75,
        )

    def search_bm25(self, query: str, *, top_k: int) -> list[RetrievalHit]:
        self.bm25_calls[query] += 1
        if top_k == 1:
            return [RetrievalHit(document=_document("warmup-noise"), match_type="bm25")]
        assert top_k == 20
        ids = [f"noise-{query}-{number}" for number in range(1, 21)]
        if query in self.gold_at_five_queries:
            ids[4] = "gold-page"
        return [
            RetrievalHit(document=_document(document_id), match_type="bm25")
            for document_id in ids
        ]

    def search_concepts(
        self,
        query: str,
        *,
        top_k: int,
    ) -> list[tuple[Document, float]]:
        self.dense_calls[query] += 1
        if top_k == 1:
            return [(_document("warmup-noise"), 1.0)]
        assert top_k == 20
        ids = ["gold-page"] + [
            f"dense-noise-{query}-{number}" for number in range(1, 20)
        ]
        return [
            (_document(document_id), float(rank))
            for rank, document_id in enumerate(ids)
        ]


def test_diagnostic_schema_is_strict_and_origin_binding_is_typed() -> None:
    valid = _case_dict(
        case_id="case-1",
        group_id="group-1",
        variant="verbatim_excerpt",
        query="too many attempts made",
    )
    case = DiagnosticCase.model_validate(valid)
    assert case.source_binding.kind == "run_artifact"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DiagnosticCase.model_validate({**valid, "retrieval_hint": "prefer BM25"})

    invalid_binding = json.loads(json.dumps(valid))
    invalid_binding["source_binding"]["kind"] = "official_html"
    with pytest.raises(ValidationError, match="must bind a run artifact"):
        DiagnosticCase.model_validate(invalid_binding)


def test_loader_enforces_unique_queries_and_group_contract(tmp_path: Path) -> None:
    source = tmp_path / "diagnostic.jsonl"
    first = _case_dict(
        case_id="case-1",
        group_id="group-1",
        variant="verbatim_excerpt",
        query="First diagnostic query",
    )
    second = _case_dict(
        case_id="case-2",
        group_id="group-1",
        variant="normalized",
        query="Second diagnostic query",
    )
    third = _case_dict(
        case_id="case-3",
        group_id="group-1",
        variant="contextualized",
        query="Third diagnostic query",
    )
    _write_dataset(source, [first, second, third])
    assert len(load_diagnostic_cases(source)) == 3

    third["query"] = "  FIRST  diagnostic QUERY "
    _write_dataset(source, [first, second, third])
    with pytest.raises(ValueError, match="normalized queries must be unique"):
        load_diagnostic_cases(source)

    third["query"] = "Third diagnostic query"
    third["runtime_stage"] = "input_processor"
    _write_dataset(source, [first, second, third])
    with pytest.raises(ValueError, match="share one runtime stage"):
        load_diagnostic_cases(source)


def test_audit_verifies_bound_log_hash_html_identity_sections_and_semantic_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_root = tmp_path / "docs"
    run_root = tmp_path / "runs"
    gold_source = docs_root / "guide" / "gold.htm"
    log_source = run_root / "run-1" / "job.msg"
    gold_source.parent.mkdir(parents=True)
    log_source.parent.mkdir(parents=True)
    gold_source.write_text(
        "<html><head><meta name='DC.identifier' content='gold-page'></head>"
        "<body id='gold-page'><section id='diagnostic-condition'>"
        "<h2>Diagnostic Condition</h2>Text</section>"
        "</body></html>",
        encoding="utf-8",
    )
    log_source.write_text(
        "Too many attempts made for this increment.\nThe analysis stopped.\n",
        encoding="utf-8",
    )
    dataset_path = tmp_path / "diagnostic.jsonl"
    common = {
        "source_sha256": _sha256(log_source),
        "gold_sha256": _sha256(gold_source),
    }
    cases = [
        _case_dict(
            case_id="case-1",
            group_id="group-1",
            variant="verbatim_excerpt",
            query="Too many attempts made for this increment.",
            **common,
        ),
        _case_dict(
            case_id="case-2",
            group_id="group-1",
            variant="normalized",
            query=normalize_observed_diagnostic(
                "Too many attempts made for this increment. The analysis stopped."
            ),
            **common,
        ),
        _case_dict(
            case_id="case-3",
            group_id="group-1",
            variant="contextualized",
            query="Abaqus stopped after exhausting increment attempts.",
            **common,
        ),
    ]
    _write_dataset(dataset_path, cases)
    review_path = tmp_path / "review.json"
    _write_review(review_path, dataset_path, cases)
    environment_seal = tmp_path / "environment-seal.json"
    environment_seal.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        diagnostic_module,
        "_verify_environment_seal",
        lambda _index, _seal, _repository: "2" * 64,
    )
    index = cast(AbaqusDocumentIndex, _AuditIndex(["gold-page"]))
    audit_paths = {
        "review_path": review_path,
        "environment_seal_path": environment_seal,
        "repository_root": tmp_path,
        "docs_root": docs_root,
        "run_root": run_root,
    }

    audited = audit_diagnostic_dataset(
        index,
        dataset_path,
        **audit_paths,
    )
    assert audited.audit.case_count == 3
    assert audited.audit.need_group_count == 1
    assert audited.audit.source_bindings_verified == 3
    assert audited.audit.unique_source_files_verified == 1
    assert audited.audit.semantic_gold_documents_verified == 1

    log_source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match=r"diagnostic source.*hash mismatch"):
        audit_diagnostic_dataset(
            index,
            dataset_path,
            **audit_paths,
        )

    log_source.write_text(
        "Too many attempts made for this increment.\nThe analysis stopped.\n",
        encoding="utf-8",
    )
    gold_source.write_text(
        "<html><head><meta name='DC.identifier' content='gold-page'></head>"
        "<body id='wrong'><section id='diagnostic-condition'>Text</section>"
        "</body></html>",
        encoding="utf-8",
    )
    bad_gold_hash = _sha256(gold_source)
    broken_cases = [
        _case_dict(
            case_id=f"broken-{number}",
            group_id="broken-group",
            variant=variant,
            query=query,
            source_sha256=_sha256(log_source),
            gold_sha256=bad_gold_hash,
        )
        for number, (variant, query) in enumerate(
            (
                (
                    "verbatim_excerpt",
                    "Too many attempts made for this increment.",
                ),
                (
                    "normalized",
                    normalize_observed_diagnostic(
                        "Too many attempts made for this increment. "
                        "The analysis stopped."
                    ),
                ),
                (
                    "contextualized",
                    "Abaqus stopped after exhausting increment attempts.",
                ),
            ),
            start=1,
        )
    ]
    _write_dataset(dataset_path, broken_cases)
    _write_review(review_path, dataset_path, broken_cases)
    with pytest.raises(ValueError, match="body id mismatch"):
        audit_diagnostic_dataset(
            index,
            dataset_path,
            **audit_paths,
        )


def test_audit_maps_nested_h3_anchor_to_indexed_h2_id_and_h3_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_root = tmp_path / "docs"
    run_root = tmp_path / "runs"
    gold_source = docs_root / "guide" / "gold.htm"
    log_source = run_root / "run-1" / "job.msg"
    gold_source.parent.mkdir(parents=True)
    log_source.parent.mkdir(parents=True)
    gold_source.write_text(
        "<html><head><meta name='DC.identifier' content='gold-page'></head>"
        "<body id='gold-page'><article id='parent-h2'>"
        "<h2>Parent Procedure</h2><article id='fine-h3'>"
        "<h3>Specific Failure Condition</h3><p>Evidence.</p>"
        "</article></article></body></html>",
        encoding="utf-8",
    )
    log_source.write_text("Specific failure.\nAnalysis stopped.\n", encoding="utf-8")
    common = {
        "source_sha256": _sha256(log_source),
        "gold_sha256": _sha256(gold_source),
    }
    cases = [
        _case_dict(
            case_id="nested-verbatim",
            group_id="nested-group",
            variant="verbatim_excerpt",
            query="Specific failure.",
            **common,
        ),
        _case_dict(
            case_id="nested-normalized",
            group_id="nested-group",
            variant="normalized",
            query=normalize_observed_diagnostic("Specific failure. Analysis stopped."),
            **common,
        ),
        _case_dict(
            case_id="nested-contextualized",
            group_id="nested-group",
            variant="contextualized",
            query="Abaqus stops for a specific failure condition",
            **common,
        ),
    ]
    for case in cases:
        case["gold_pages"][0]["section_ids"] = ["fine-h3"]
    dataset_path = tmp_path / "diagnostic.jsonl"
    _write_dataset(dataset_path, cases)
    review_path = tmp_path / "review.json"
    _write_review(review_path, dataset_path, cases)
    environment_seal = tmp_path / "environment-seal.json"
    environment_seal.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        diagnostic_module,
        "_verify_environment_seal",
        lambda _index, _seal, _repository: "2" * 64,
    )
    audit_kwargs = {
        "review_path": review_path,
        "environment_seal_path": environment_seal,
        "repository_root": tmp_path,
        "docs_root": docs_root,
        "run_root": run_root,
    }
    mapped_index = cast(
        AbaqusDocumentIndex,
        _AuditIndex(
            ["gold-page"],
            section_id="parent-h2",
            section_title="Specific Failure Condition",
        ),
    )

    audited = audit_diagnostic_dataset(
        mapped_index,
        dataset_path,
        **audit_kwargs,
    )

    expected = DiagnosticSectionTarget(
        document_id="gold-page",
        section_id="parent-h2",
        section_title="Specific Failure Condition",
    )
    assert all(binding.targets == (expected,) for binding in audited.case_targets)
    assert audited.audit.semantic_section_targets_verified == 1

    unmappable_index = cast(
        AbaqusDocumentIndex,
        _AuditIndex(
            ["gold-page"],
            section_id="parent-h2",
            section_title="Different Indexed Section",
        ),
    )
    with pytest.raises(ValueError, match="no index-representable section target"):
        audit_diagnostic_dataset(
            unmappable_index,
            dataset_path,
            **audit_kwargs,
        )


def test_evaluation_retrieves_each_query_once_and_reports_top4_top5_group_macro(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = [f"query-{number}" for number in range(1, 6)]
    cases: list[DiagnosticCase] = []
    for number, query in enumerate(queries, start=1):
        data = _case_dict(
            case_id=f"case-{number}",
            group_id="three-variant-group" if number <= 3 else "two-variant-group",
            variant=(
                ("verbatim_excerpt", "normalized", "contextualized")[number - 1]
                if number <= 3
                else ("condition_fragment", "contextualized")[number - 4]
            ),
            query=query,
        )
        if number > 3:
            data.update(
                {
                    "origin": "official_document_condition",
                    "trigger_source": "official_diagnostic",
                    "source_binding": {
                        "kind": "official_html",
                        "path": "guide/gold.htm",
                        "sha256": "1" * 64,
                        "locator": "#diagnostic-condition",
                        "encoding": "utf-8",
                        "legacy_inferred": False,
                    },
                }
            )
        cases.append(DiagnosticCase.model_validate(data))
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text("fixture", encoding="utf-8")
    review_path = tmp_path / "review.json"
    review = DiagnosticReviewManifest(
        dataset_id="diagnostic-v1",
        dataset_sha256=_sha256(dataset_path),
        status="reviewed",
        review_method=("source_derivation_and_direct_html_without_retrieval_rankings"),
        reviewer_id="test-reviewer",
        case_count=5,
        need_group_count=2,
        case_ids_sha256="0" * 64,
        reviewed_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    review_path.write_text(review.model_dump_json(), encoding="utf-8")
    environment_seal_path = tmp_path / "environment-seal.json"
    environment_seal_path.write_text("{}", encoding="utf-8")

    audit = DiagnosticAuditReport(
        dataset_id="diagnostic-v1",
        dataset_sha256=_sha256(dataset_path),
        review_manifest_sha256=_sha256(review_path),
        environment_seal_sha256=_sha256(environment_seal_path),
        reviewer_id="test-reviewer",
        implementation=diagnostic_module._implementation_binding(),
        case_count=5,
        need_group_count=2,
        origin_counts={"observed_solver_log": 5},
        runtime_stage_counts={"standard_solver": 5},
        variant_counts=dict(Counter(case.variant for case in cases)),
        source_bindings_verified=5,
        unique_source_files_verified=1,
        gold_page_bindings_verified=5,
        unique_gold_html_files_verified=1,
        semantic_gold_documents_verified=1,
        semantic_section_targets_verified=1,
        legacy_inferred_case_count=0,
    )
    audited = AuditedDiagnosticDataset(
        source=dataset_path,
        cases=tuple(cases),
        case_targets=tuple(
            DiagnosticCaseTargets(
                case_id=case.case_id,
                targets=(
                    DiagnosticSectionTarget(
                        document_id="gold-page",
                        section_id="diagnostic-condition",
                        section_title="Diagnostic Condition",
                    ),
                ),
            )
            for case in cases
        ),
        audit=audit,
        review_manifest=review,
        review_path=review_path,
        environment_seal_path=environment_seal_path,
        repository_root=tmp_path,
        docs_root=tmp_path,
        run_root=tmp_path,
    )
    fake = _EvaluationIndex(tmp_path, set(queries[:3]))
    reference_report_path = tmp_path / "reference-report.json"
    reference_report = {
        "dataset_id": audited.audit.dataset_id,
        "dataset_sha256": audited.audit.dataset_sha256,
        "case_count": len(audited.cases),
        "need_group_count": 2,
        "top_k": 5,
        "product_context_depth": 4,
        "fetch_k": 20,
        "rrf_k": 60,
        "bootstrap_replicates": 20_000,
        "bootstrap_seed": 20_260_904,
        "warmup_performed": True,
        "warmup_query": (
            "Abaqus documentation retrieval warmup; no scored diagnostic case"
        ),
        "implementation": audited.audit.implementation.model_dump(mode="json"),
        "cases": [
            {
                "case_id": binding.case_id,
                "gold_targets": [
                    target.model_dump(mode="json") for target in binding.targets
                ],
            }
            for binding in audited.case_targets
        ],
    }
    reference_report_path.write_text(
        json.dumps(reference_report),
        encoding="utf-8",
    )
    reference_attempts_path = tmp_path / "reference-attempts.jsonl"
    reference_attempts_path.write_text(
        json.dumps(
            {
                "event": "completed_with_attestation_gap",
                "rankings_emitted": True,
                "report_sha256": _sha256(reference_report_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    diagnostic_seal_path = tmp_path / "diagnostic-seal.json"
    diagnostic_seal = _build_diagnostic_seal(
        audited,
        seal_directory=tmp_path,
        reference_report_path=reference_report_path,
        reference_attempts_path=reference_attempts_path,
        warmup=True,
    )
    _write_text_new_atomic(
        diagnostic_seal_path,
        diagnostic_seal.model_dump_json(indent=2) + "\n",
    )
    verification_calls = 0

    def verify_environment(
        _index: AbaqusDocumentIndex,
        _dataset: AuditedDiagnosticDataset,
    ) -> None:
        nonlocal verification_calls
        verification_calls += 1

    monkeypatch.setattr(
        diagnostic_module,
        "_verify_audited_dataset",
        verify_environment,
    )

    report = evaluate_diagnostic_dataset(
        cast(AbaqusDocumentIndex, fake),
        audited,
        diagnostic_seal_path=diagnostic_seal_path,
    )

    assert all(fake.bm25_calls[query] == 1 for query in queries)
    assert all(fake.dense_calls[query] == 1 for query in queries)
    assert sum(fake.bm25_calls.values()) == len(queries) + 1
    assert sum(fake.dense_calls.values()) == len(queries) + 1
    assert verification_calls == 2
    assert report.product_context_depth == 4
    assert report.top_k == 5
    assert report.dataset_role == "development_diagnostic_stress"
    assert report.diagnostic_seal_sha256 == _sha256(diagnostic_seal_path)
    assert report.evaluation_scope == "raw_query_algorithm_comparison"
    assert report.heldout is False
    assert report.product_path_evaluated is False
    assert report.production_traffic_representative is False
    assert report.warmup_performed is True
    assert report.warmup_query is not None
    assert report.warmup_query not in queries
    assert report.variant_level.bm25.at_4.section_hit_rate == 0
    assert report.variant_level.bm25.at_5.section_hit_rate == pytest.approx(3 / 5)
    assert report.variant_level.bm25.at_5.section_mrr == pytest.approx(3 / 25)
    assert report.need_group_macro.bm25.at_5.section_hit_rate == pytest.approx(1 / 2)
    assert report.need_group_macro.bm25.at_5.section_mrr == pytest.approx(1 / 10)
    assert report.paired_need_group_macro.bm25_vs_dense.at_5.groups == 2
    paired = report.paired_need_group_macro.bm25_vs_dense.at_5
    assert paired.section_hit_interval.replicates == 20_000
    assert paired.section_hit_interval.mean == paired.section_hit.mean_delta
    assert paired.section_hit_interval.lower <= paired.section_hit_interval.mean
    assert paired.section_hit_interval.upper >= paired.section_hit_interval.mean
    assert len(report.cases) == 5
    assert report.cases[0].bm25.retrieved_document_ids[4] == "gold-page"
    assert report.cases[0].bm25.retrieved_section_titles[4] == ("Diagnostic Condition")
    assert report.cases[0].gold_targets == (
        DiagnosticSectionTarget(
            document_id="gold-page",
            section_id="diagnostic-condition",
            section_title="Diagnostic Condition",
        ),
    )
    DiagnosticEvaluation.model_validate_json(report.model_dump_json())

    seal_data = diagnostic_seal.model_dump(mode="json")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DiagnosticSeal.model_validate({**seal_data, "rankings": []})

    current_versions = diagnostic_module._installed_dependency_versions()
    with monkeypatch.context() as version_patch:
        version_patch.setattr(
            diagnostic_module,
            "_installed_dependency_versions",
            lambda: DiagnosticDependencyVersions(
                lxml=f"{current_versions.lxml}-drift",
                pydantic=current_versions.pydantic,
            ),
        )
        with pytest.raises(ValueError, match="dependency versions do not match"):
            audit_diagnostic_seal(
                audited,
                diagnostic_seal_path,
                warmup=True,
            )

    first_binding = audited.case_targets[0]
    first_target = first_binding.targets[0]
    drifted_binding = first_binding.model_copy(
        update={
            "targets": (
                first_target.model_copy(
                    update={"section_title": f"{first_target.section_title} drift"}
                ),
                *first_binding.targets[1:],
            )
        }
    )
    drifted_dataset = replace(
        audited,
        case_targets=(drifted_binding, *audited.case_targets[1:]),
    )
    with pytest.raises(ValueError, match=r"case.targets do not match"):
        audit_diagnostic_seal(
            drifted_dataset,
            diagnostic_seal_path,
            warmup=True,
        )

    with monkeypatch.context() as implementation_patch:
        implementation_patch.setattr(
            diagnostic_module,
            "_implementation_binding",
            lambda: diagnostic_seal.implementation.model_copy(
                update={"diagnostic_py": "f" * 64}
            ),
        )
        with pytest.raises(ValueError, match="implementation hashes do not match"):
            audit_diagnostic_seal(
                audited,
                diagnostic_seal_path,
                warmup=True,
            )

    with pytest.raises(ValueError, match=r"reference report field.*warmup"):
        audit_diagnostic_seal(
            audited,
            diagnostic_seal_path,
            warmup=False,
        )

    review_path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match=r"review hash mismatch"):
        audit_diagnostic_seal(
            audited,
            diagnostic_seal_path,
            warmup=True,
        )


def test_create_new_output_never_overwrites_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "evidence" / "report.json"
    _write_text_new_atomic(output, "first")
    with pytest.raises(ValueError, match="already exists"):
        _write_text_new_atomic(output, "second")
    assert output.read_text(encoding="utf-8") == "first"


def test_section_metric_does_not_accept_an_unrelated_chunk_on_the_gold_page() -> None:
    gold = DiagnosticGoldPage(
        document_id="gold-page",
        source_path="guide/gold.htm",
        source_sha256="0" * 64,
        section_ids=("target-section",),
        rationale="Only the target section answers the diagnostic.",
    )
    wrong_section = RetrievalHit(
        document=Document(
            id="gold-page#wrong",
            page_content="unrelated",
            metadata={
                "document_id": "gold-page",
                "section_id": "other-section",
                "section_title": "Other Section",
            },
        ),
        match_type="semantic",
    )

    target = DiagnosticSectionTarget(
        document_id="gold-page",
        section_id="target-section",
        section_title="Target Section",
    )
    ranking = _score_ranking(
        [wrong_section],
        [gold],
        [target],
        elapsed_ms=1.0,
    )

    assert ranking.page_hit_at_4 is True
    assert ranking.section_hit_at_4 is False

    wrong_title = RetrievalHit(
        document=Document(
            id="gold-page#target",
            page_content="unrelated",
            metadata={
                "document_id": "gold-page",
                "section_id": "target-section",
                "section_title": "Different Section",
            },
        ),
        match_type="semantic",
    )
    normalized_title_match = RetrievalHit(
        document=Document(
            id="gold-page#target-2",
            page_content="relevant",
            metadata={
                "document_id": "gold-page",
                "section_id": "target-section",
                "section_title": "  TARGET   section ",
            },
        ),
        match_type="semantic",
    )

    wrong_title_ranking = _score_ranking(
        [wrong_title],
        [gold],
        [target],
        elapsed_ms=1.0,
    )
    matched_ranking = _score_ranking(
        [normalized_title_match],
        [gold],
        [target],
        elapsed_ms=1.0,
    )

    assert wrong_title_ranking.section_hit_at_4 is False
    assert matched_ranking.section_hit_at_4 is True
    assert matched_ranking.retrieved_section_titles == ("  TARGET   section ",)


def test_diagnostic_output_cannot_pollute_index_or_follow_leaf_symlink(
    tmp_path: Path,
) -> None:
    index_root = tmp_path / "index"
    evidence_root = tmp_path / "evidence"
    index_root.mkdir()
    evidence_root.mkdir()

    nested_index_output = index_root / "new" / "sub" / "report.json"
    with pytest.raises(ValueError, match="outside the retrieval index"):
        _resolve_output_path(nested_index_output, index_root)
    assert not (index_root / "new").exists()

    link = evidence_root / "linked-report.json"
    target = evidence_root / "target.json"
    try:
        link.symlink_to(target)
    except OSError:
        return
    with pytest.raises(ValueError, match="already exists"):
        _resolve_output_path(link, index_root)


def test_cli_requires_output_for_seal_and_diagnostic_seal_for_evaluate() -> None:
    parser = build_parser()
    common = [
        "--dataset",
        "dataset.jsonl",
        "--review",
        "review.json",
        "--environment-seal",
        "environment.json",
        "--docs-root",
        "docs",
        "--run-root",
        "runs",
    ]

    audit_args = parser.parse_args(["audit", *common])
    _validate_cli_arguments(audit_args)

    seal_args = parser.parse_args(["seal", *common])
    with pytest.raises(ValueError, match="seal requires --output"):
        _validate_cli_arguments(seal_args)

    seal_without_reference_args = parser.parse_args(
        ["seal", *common, "--output", "diagnostic-seal.json"]
    )
    with pytest.raises(ValueError, match="requires --reference-report"):
        _validate_cli_arguments(seal_without_reference_args)

    valid_seal_args = parser.parse_args(
        [
            "seal",
            *common,
            "--reference-report",
            "reference-report.json",
            "--reference-attempts",
            "reference-attempts.jsonl",
            "--output",
            "diagnostic-seal.json",
        ]
    )
    _validate_cli_arguments(valid_seal_args)

    evaluate_args = parser.parse_args(["evaluate", *common, "--output", "report.json"])
    with pytest.raises(ValueError, match="evaluate requires --diagnostic-seal"):
        _validate_cli_arguments(evaluate_args)

    valid_evaluate_args = parser.parse_args(
        [
            "evaluate",
            *common,
            "--diagnostic-seal",
            "diagnostic-seal.json",
            "--output",
            "report.json",
        ]
    )
    _validate_cli_arguments(valid_evaluate_args)
