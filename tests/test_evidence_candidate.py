import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from abaqus_sim_agent.evaluation import evidence_candidate as candidate
from abaqus_sim_agent.retrieval.evidence_blocks import TokenBoundChildren
from abaqus_sim_agent.retrieval.flat_store import FlatDocumentStore
from tests.test_evidence_blocks import tokenizer


def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    page = source / "page.htm"
    page.write_text(
        '<html><head><title>Manual</title></head><body id="page"><article><h2>Data</h2><p>Condition.</p><table><tr><th>Kind</th><th>E (MPa)</th></tr><tr><td>Steel</td><td>210000</td></tr></table><p>Unsupported <math><mtable/></math>.</p></article></body></html>',
        encoding="utf-8",
    )
    manifest = tmp_path / "sources.json"
    manifest.write_text(
        json.dumps(
            {
                "source_sha256": {
                    "page.htm": hashlib.sha256(page.read_bytes()).hexdigest()
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(candidate, "IMPLEMENTATIONS", ())
    monkeypatch.setattr(candidate, "_encoder_binding", lambda: {})
    monkeypatch.setattr(candidate, "splitter", lambda: TokenBoundChildren(tokenizer()))
    output = tmp_path / "candidate"
    candidate.prepare(source, manifest, "source_sha256", output, tmp_path)
    return output


def test_prepare_retains_unsupported_units_and_reopens_exact_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = prepared(tmp_path, monkeypatch)
    documents, units = candidate._load(output)
    coverage = candidate.read(output / "coverage.json")
    assert coverage["source_pages"] == 1
    assert coverage["new_supported_types"] == {"table": 1}
    assert coverage["unsupported_reasons"] == {"math_element:mtable": 1}
    assert any(u.evidence.projection_status == "deferred_math" for u in units.values())
    assert all(d.metadata["parent_id"] in units for d in documents)
    assert not (output / "index").exists()
    assert not candidate.read(output / "manifest.json")["product_tool_compatible"]
    with pytest.raises(FileExistsError):
        candidate.prepare(
            tmp_path / "source",
            tmp_path / "sources.json",
            "source_sha256",
            output,
            tmp_path,
        )


def test_prepared_artifact_tampering_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = prepared(tmp_path, monkeypatch)
    path = output / "children.jsonl"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="artifact changed"):
        candidate._load(output)


def test_source_changes_fail_before_build_or_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = prepared(tmp_path, monkeypatch)
    (tmp_path / "source/page.htm").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        candidate._check_binding(output)


def test_index_roundtrip_binds_all_embeddings_and_persists_before_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = prepared(tmp_path, monkeypatch)
    candidate.build_index(output)
    binding = candidate.read(output / "index-binding.json")
    vector = FlatDocumentStore(
        output / "index",
        embeddings=candidate.encoder(),
    )
    # Empty collections/default-embedding configuration regressions fail this real query.
    assert vector.similarity_search_with_score("Steel elastic modulus", k=1)
    assert candidate.embedding_digest(vector) == binding["embeddings_sha256"]
    assert (
        candidate.dense_vector_storage_snapshot(
            output / "index", collection_name=candidate.COLLECTION
        ).model_dump(mode="json")
        == binding["vector"]
    )


def test_paged_get_uses_bounded_queries_and_keeps_every_record():
    vector = MagicMock(spec=FlatDocumentStore)
    ids = [str(i) for i in range(7)]

    def page(*, include, limit, offset):
        assert limit == 2
        selected = ids[offset : offset + limit]
        return {
            "ids": selected,
            **{key: [f"{key}:{i}" for i in selected] for key in include},
        }

    vector.get.side_effect = page
    result = candidate.paged_get(
        vector, include=["documents", "metadatas"], page_size=2
    )
    assert result["ids"] == ids
    assert result["documents"] == [f"documents:{i}" for i in ids]
    assert vector.get.call_count == 5


def test_paged_get_rejects_incomplete_fields_and_duplicate_ids():
    vector = MagicMock(spec=FlatDocumentStore)
    vector.get.return_value = {"ids": ["a"], "embeddings": None}
    with pytest.raises(ValueError, match="incomplete paged"):
        candidate.paged_get(vector, include=["embeddings"])
    vector.get.side_effect = [{"ids": ["a"]}, {"ids": ["a"]}, {"ids": []}]
    with pytest.raises(ValueError, match="duplicate IDs"):
        candidate.paged_get(vector, include=[])
    with pytest.raises(ValueError, match="positive"):
        candidate.paged_get(vector, include=[], page_size=0)
