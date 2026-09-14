from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from abaqus_sim_agent.retrieval.heldout import HeldoutEvaluation
from abaqus_sim_agent.retrieval.reproduction import (
    ArtifactBinding,
    DenseVectorFile,
    DenseVectorStorageSnapshot,
    ExternalFileBinding,
    HeldoutReproductionSeal,
    _resolve_output_paths,
    _verify_query_encoder,
    _write_text_new_atomic,
    compare_reproduction,
    dense_vector_storage_snapshot,
)

BENCHMARK_ROOT = Path(__file__).parent.parent / "benchmarks" / "rag"


def _write_chroma_fixture(root: Path) -> tuple[str, str]:
    root.mkdir()
    target_collection = "target-collection-id"
    other_collection = "other-collection-id"
    target_segment = "target-segment-id"
    other_segment = "other-segment-id"
    with sqlite3.connect(root / "chroma.sqlite3") as connection:
        connection.executescript(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                dimension INTEGER,
                config_json_str TEXT,
                schema_str TEXT
            );
            CREATE TABLE segments (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                collection TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO collections VALUES (?, ?, ?, ?, ?)",
            (
                (target_collection, "target", 384, '{"space":"l2"}', "schema-a"),
                (other_collection, "other", 128, '{"space":"cosine"}', "schema-b"),
            ),
        )
        connection.executemany(
            "INSERT INTO segments VALUES (?, ?, ?, ?)",
            (
                (
                    target_segment,
                    "urn:chroma:segment/vector/hnsw-local-persisted",
                    "VECTOR",
                    target_collection,
                ),
                (
                    other_segment,
                    "urn:chroma:segment/vector/hnsw-local-persisted",
                    "VECTOR",
                    other_collection,
                ),
            ),
        )
    for segment, marker in ((target_segment, b"target"), (other_segment, b"other!")):
        directory = root / segment
        directory.mkdir()
        (directory / "data_level0.bin").write_bytes(marker)
        (directory / "header.bin").write_bytes(b"header")
    return target_segment, other_segment


def test_dense_vector_snapshot_selects_only_the_target_collection(
    tmp_path: Path,
) -> None:
    target_segment, other_segment = _write_chroma_fixture(tmp_path / "index")

    first = dense_vector_storage_snapshot(tmp_path / "index", collection_name="target")
    second = dense_vector_storage_snapshot(tmp_path / "index", collection_name="target")
    (tmp_path / "index" / other_segment / "data_level0.bin").write_bytes(b"unused")
    after_other_change = dense_vector_storage_snapshot(
        tmp_path / "index",
        collection_name="target",
    )
    (tmp_path / "index" / target_segment / "data_level0.bin").write_bytes(b"change")
    after_target_change = dense_vector_storage_snapshot(
        tmp_path / "index",
        collection_name="target",
    )

    assert first == second == after_other_change
    assert first.sha256 != after_target_change.sha256
    assert first.segment_ids == (target_segment,)
    assert all(item.path.startswith(f"{target_segment}/") for item in first.files)
    with pytest.raises(ValueError, match="exactly one Chroma collection"):
        dense_vector_storage_snapshot(tmp_path / "index", collection_name="missing")


def test_reproduction_seal_requires_complete_runtime_and_sorted_encoder_files() -> None:
    snapshot = DenseVectorStorageSnapshot(
        algorithm="chroma-target-hnsw-segment-bytes-v1",
        collection_name="target",
        collection_id="collection",
        dimension=384,
        collection_config_sha256="0" * 64,
        collection_schema_sha256="0" * 64,
        segment_ids=("segment",),
        sha256="0" * 64,
        files=(
            DenseVectorFile(
                path="segment/data_level0.bin",
                size_bytes=1,
                sha256="0" * 64,
            ),
        ),
    )
    base: dict[str, Any] = {
        "schema_version": "1",
        "dataset_id": "heldout-v3",
        "purpose": "identical_reproduction",
        "status": "sealed",
        "parent_seal": {"path": "parent.json", "sha256": "0" * 64},
        "reference_report": {"path": "report.json", "sha256": "0" * 64},
        "reference_attempts": {"path": "attempts.jsonl", "sha256": "0" * 64},
        "reference_consumption": {
            "path": "consumption.json",
            "sha256": "0" * 64,
        },
        "reference_posthoc_vector_snapshot": {
            "path": "snapshot.json",
            "sha256": "0" * 64,
        },
        "uv_lock": {"path": "uv.lock", "sha256": "0" * 64},
        "python_version": "3.12.0",
        "runtime_versions": {
            "chromadb": "1",
            "langchain-chroma": "1",
            "langchain-core": "1",
            "numpy": "1",
            "onnxruntime": "1",
            "tokenizers": "1",
            "tqdm": "1",
        },
        "query_encoder_type": ("chromadb.DefaultEmbeddingFunction/all-MiniLM-L6-v2"),
        "query_encoder_root": str(Path.cwd().resolve()),
        "query_encoder_files": [
            {"path": path, "size_bytes": 1, "sha256": "0" * 64}
            for path in (
                "onnx.tar.gz",
                "onnx/config.json",
                "onnx/model.onnx",
                "onnx/special_tokens_map.json",
                "onnx/tokenizer.json",
                "onnx/tokenizer_config.json",
                "onnx/vocab.txt",
            )
        ],
        "onnx_providers": ["CPUExecutionProvider"],
        "dense_vector_storage": snapshot.model_dump(mode="json"),
        "reproduction_source_sha256": "0" * 64,
        "sealed_at": "2026-09-03T00:00:00Z",
    }
    seal = HeldoutReproductionSeal.model_validate(base)

    assert seal.purpose == "identical_reproduction"
    missing_runtime = dict(base)
    missing_runtime["runtime_versions"] = {"chromadb": "1"}
    with pytest.raises(ValueError, match="runtime package set is incomplete"):
        HeldoutReproductionSeal.model_validate(missing_runtime)
    unsorted = dict(base)
    unsorted["query_encoder_files"] = list(reversed(base["query_encoder_files"]))
    with pytest.raises(ValueError, match="exact Chroma MiniLM file set"):
        HeldoutReproductionSeal.model_validate(unsorted)
    duplicate_providers = dict(base)
    duplicate_providers["onnx_providers"] = [
        "CPUExecutionProvider",
        "CPUExecutionProvider",
    ]
    with pytest.raises(ValueError, match="providers must be unique"):
        HeldoutReproductionSeal.model_validate(duplicate_providers)

    custom_index = SimpleNamespace(embeddings=object())
    with pytest.raises(ValueError, match="default embedding function"):
        _verify_query_encoder(cast(Any, custom_index), seal)
    wrong_collection_embedding = SimpleNamespace(
        embeddings=None,
        read_manifest=lambda: SimpleNamespace(
            embedding="chromadb.DefaultEmbeddingFunction/all-MiniLM-L6-v2"
        ),
        vector_store=SimpleNamespace(
            _collection=SimpleNamespace(_embedding_function=object())
        ),
    )
    with pytest.raises(ValueError, match="not using DefaultEmbeddingFunction"):
        _verify_query_encoder(cast(Any, wrong_collection_embedding), seal)


def test_reproduction_outputs_are_isolated_and_create_new(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    index_root = evidence_root / "index"
    evidence_root.mkdir()
    index_root.mkdir()
    report = evidence_root / "report.json"
    comparison = evidence_root / "comparison.json"

    assert _resolve_output_paths(
        report,
        comparison,
        evidence_root=evidence_root,
        index_root=index_root,
    ) == (report.resolve(), comparison.resolve())
    with pytest.raises(ValueError, match="different paths"):
        _resolve_output_paths(
            report,
            report,
            evidence_root=evidence_root,
            index_root=index_root,
        )
    with pytest.raises(ValueError, match="seal directory"):
        _resolve_output_paths(
            tmp_path / "outside.json",
            comparison,
            evidence_root=evidence_root,
            index_root=index_root,
        )
    with pytest.raises(ValueError, match="outside the index"):
        _resolve_output_paths(
            index_root / "report.json",
            comparison,
            evidence_root=evidence_root,
            index_root=index_root,
        )

    linked_output = evidence_root / "linked.json"
    linked_target = evidence_root / "linked-target.json"
    try:
        linked_output.symlink_to(linked_target)
    except OSError:
        pass
    else:
        with pytest.raises(ValueError, match="already exists"):
            _resolve_output_paths(
                linked_output,
                comparison,
                evidence_root=evidence_root,
                index_root=index_root,
            )

    _write_text_new_atomic(report, "first")
    with pytest.raises(ValueError, match="already exists"):
        _resolve_output_paths(
            report,
            comparison,
            evidence_root=evidence_root,
            index_root=index_root,
        )
    with pytest.raises(ValueError, match="already exists"):
        _write_text_new_atomic(report, "second")
    assert report.read_text(encoding="utf-8") == "first"


def test_reproduction_output_concurrent_publish_never_overwrites(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.json"

    def publish(content: str) -> str:
        try:
            _write_text_new_atomic(destination, content)
        except ValueError:
            return "conflict"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, ("first", "second")))

    assert sorted(outcomes) == ["conflict", "published"]
    assert destination.read_text(encoding="utf-8") in {"first", "second"}


def test_reproduction_comparison_ignores_latency_but_gates_all_rankings() -> None:
    reference = HeldoutEvaluation.model_validate_json(
        (
            BENCHMARK_ROOT
            / "abaqus_2026_bm25_dense_rrf_heldout_evaluation_v3_20260903.json"
        ).read_text(encoding="utf-8")
    )
    data = reference.model_dump(mode="json")
    data["evaluated_at"] = "2026-09-04T00:00:00Z"
    for retriever in ("bm25", "dense", "hybrid"):
        for stratum in ("all", "concept", "identifier_context", "multi_document"):
            data[retriever][stratum]["p50_latency_ms"] += 100
            data[retriever][stratum]["p95_latency_ms"] += 100
    for case in data["cases"]:
        case["fusion_elapsed_ms"] += 100
        for retriever in ("bm25", "dense", "hybrid"):
            case[retriever]["elapsed_ms"] += 100
    reproduced = HeldoutEvaluation.model_validate(data)

    identical = compare_reproduction(
        reference,
        reproduced,
        reference_report_sha256="0" * 64,
        reproduction_report_sha256="1" * 64,
    )
    assert identical.identical is True
    assert identical.rankings_compared == 90

    changed = reproduced.model_dump(mode="json")
    changed["cases"][0]["dense"]["retrieved_document_ids"][0] = "different"
    mismatch = compare_reproduction(
        reference,
        HeldoutEvaluation.model_validate(changed),
        reference_report_sha256="0" * 64,
        reproduction_report_sha256="2" * 64,
    )
    assert mismatch.identical is False
    assert mismatch.ranking_mismatches
    assert mismatch.effect_mismatches


def test_binding_models_are_strict() -> None:
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ArtifactBinding.model_validate(
            {"path": "artifact", "sha256": "0" * 64, "unexpected": True}
        )
    with pytest.raises(ValueError, match="String should match pattern"):
        ExternalFileBinding(path="encoder", size_bytes=1, sha256="bad")
