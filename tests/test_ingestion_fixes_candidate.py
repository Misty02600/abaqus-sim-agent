import json
from pathlib import Path

import pytest
from tests.test_equation_prose import DEFINITIONS, EQUATION, EXPLANATION
from tests.test_evidence_blocks import tokenizer

from abaqus_sim_agent.evaluation import evidence_candidate as io
from abaqus_sim_agent.evaluation import ingestion_fixes_candidate as candidate
from abaqus_sim_agent.retrieval.evidence_blocks import TokenBoundChildren


def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "page.htm"
    first.write_text(
        '<html><head><title>Manual</title></head><body id="page"><table><tr><td><article id="section"><h2>Rule</h2>'
        + EQUATION
        + DEFINITIONS
        + EXPLANATION
        + "<pre>if condition:\n    run()</pre></article></td></tr></table></body></html>",
        encoding="utf-8",
    )
    second = source / candidate.ADDED_SOURCE
    second.parent.mkdir()
    second.write_text(
        '<html><head><title>Execution</title></head><body id="execution"><article><h2>Options</h2><dl><dt>output_precision</dt><dd><p>Field output rule. History output rule.</p></dd></dl></article></body></html>',
        encoding="utf-8",
    )
    sources = tmp_path / "sources.json"
    io.write_new(sources, {"sources": {"page.htm": io.sha(first)}})
    monkeypatch.setattr(io, "IMPLEMENTATIONS", ())
    monkeypatch.setattr(candidate, "NEW_IMPLEMENTATIONS", ())
    monkeypatch.setattr(io, "_encoder_binding", lambda: {})
    monkeypatch.setattr(io, "splitter", lambda: TokenBoundChildren(tokenizer()))
    old = tmp_path / "old"
    io.prepare(source, sources, "sources", old, tmp_path, allow_headerless=True)
    new = tmp_path / "new"
    candidate.prepare(old, new)
    return old, new, source


def test_independent_prepare_preserves_old_searchable_units_and_maps_projected_relations(
    tmp_path, monkeypatch
):
    old, new, _ = prepared(tmp_path, monkeypatch)
    old_docs, old_units = io._load(old)
    docs, units = io._load(new)
    assert all(d in docs for d in old_docs)
    assert all(
        units[k] == u
        for k, u in old_units.items()
        if u.evidence.projection_status == "text"
    )
    refs = candidate.check_relations(new, units)
    assert len(refs) == 1
    explanation = units[next(iter(refs))]
    assert explanation.evidence.projection_status == "text"
    assert explanation.projection_version != "text-only-v1"
    assert any(d.metadata["parent_id"] == explanation.evidence.parent_id for d in docs)
    assert any("    run()" in u.evidence.body for u in units.values())
    assert io.read(new / "coverage.json")["source_pages"] == 2
    assert io.read(new / "coverage.json")["searchable_explanations"] == 1
    assert not (new / "index").exists()
    assert candidate.ADDED_SOURCE not in io.read(old / "manifest.json")["sources"]
    with pytest.raises(FileExistsError):
        candidate.prepare(old, new)


def test_context_sidecar_tampering_is_rejected(tmp_path, monkeypatch):
    _, new, _ = prepared(tmp_path, monkeypatch)
    _, units = io._load(new)
    path = new / "equation_context_relations.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["relations"] = []
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="sidecar changed"):
        candidate.check_relations(new, units)


def test_source_inventory_checks_changed_hash_and_root_escape(tmp_path, monkeypatch):
    _, new, source = prepared(tmp_path, monkeypatch)
    file = source / candidate.ADDED_SOURCE
    file.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        io._check_binding(new)
    with pytest.raises(ValueError, match="outside root"):
        candidate.source_registry(source, "../outside.htm", "a" * 64)
