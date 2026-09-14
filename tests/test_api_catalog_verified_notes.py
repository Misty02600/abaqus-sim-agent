from __future__ import annotations

import hashlib
import json
from pathlib import Path

from abaqus_sim_agent.api_catalog import ApiRuntimeNote
from abaqus_sim_agent.api_catalog.evaluation import load_api_catalog_cases

_ROOT = Path(__file__).parent.parent
_BENCHMARK_ROOT = _ROOT / "benchmarks" / "api_catalog"
_RUNTIME_NOTES = _BENCHMARK_ROOT / "abaqus_2024_runtime_notes.jsonl"
_CONSUMER_EVIDENCE = _BENCHMARK_ROOT / "abaqus_2024_api_consumer_observations.json"
_BEAM_ORIENTATION_EVIDENCE = (
    _BENCHMARK_ROOT / "abaqus_2024_beam_section_orientation_observations.json"
)
_ODB_EVIDENCE = _BENCHMARK_ROOT / "abaqus_2024_odb_behavior_observations.json"
_REGION_EVIDENCE = _BENCHMARK_ROOT / "abaqus_2024_region_consumer_observations.json"
_FINAL15_EVIDENCE = _BENCHMARK_ROOT / "abaqus_api_catalog_final15_20260828.json"
_QUERY_BENCHMARK = _BENCHMARK_ROOT / "abaqus_api_queries.jsonl"
_QUERY_RESULT = _BENCHMARK_ROOT / "abaqus_api_evaluation_final15.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _notes() -> dict[str, ApiRuntimeNote]:
    notes = [
        ApiRuntimeNote.model_validate_json(line)
        for line in _RUNTIME_NOTES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len({note.note_id for note in notes}) == len(notes)
    return {note.note_id: note for note in notes}


def test_final15_verified_notes_are_scoped_and_traceable() -> None:
    evidence = json.loads(_FINAL15_EVIDENCE.read_text(encoding="utf-8"))
    notes = _notes()
    expected_ids = set(evidence["verified_notes_added_since_final9"])

    assert len(expected_ids) == 10
    for note_id in expected_ids:
        note = notes[note_id]
        assert note.verified
        assert note.runtime_release == "2024"
        assert note.evidence_ref in {
            "benchmarks/api_catalog/abaqus_2024_api_consumer_observations.json",
            "benchmarks/api_catalog/abaqus_2024_odb_behavior_observations.json",
            "benchmarks/api_catalog/abaqus_2024_region_consumer_observations.json",
            "benchmarks/api_catalog/abaqus_2024_beam_section_orientation_observations.json",
        }
        assert note.source.source_path == note.evidence_ref

    assert (
        "one-wrapper"
        in notes["runtime:2024:Part.setElementType:region-dispatch"].guidance
    )
    assert (
        "flat Python tuple or list"
        in notes["runtime:2024:Assembly.Surface:side1-edge-container"].guidance
    )
    assert (
        "bounded to B31/Static"
        in notes["runtime:2024:Model.FieldOutputRequest:b31-companion-fields"].guidance
    )
    assert (
        "not slicing"
        in notes["runtime:2024:FieldOutput.getSubset:field-value-boundaries"].guidance
    )
    assert (
        "per-instance OdbMeshNodeArray"
        in notes["runtime:2024:openOdb:node-array-nesting"].guidance
    )
    assert (
        "side1Faces"
        in notes["runtime:2024:Assembly.Surface:side1-face-container"].guidance
    )
    assert (
        "side2Edges"
        in notes["runtime:2024:Assembly.Surface:side2-edge-container"].guidance
    )
    assert (
        "side2Faces"
        in notes["runtime:2024:Assembly.Surface:side2-face-container"].guidance
    )
    assert (
        "assignBeamSectionOrientation"
        in notes["runtime:2024:Model.BeamSection:orientation-boundary"].guidance
    )
    assert (
        "beamOrientation"
        in notes["runtime:2024:Model.BeamSection:orientation-boundary"].guidance
    )
    pruned_ids = {
        item["note_id"]
        for item in evidence["note_policy"]["pruned_after_product_evaluation"]
    }
    assert pruned_ids.isdisjoint(notes)
    assert len(notes) == evidence["note_policy"]["runtime_notes_total"] == 16


def test_final15_evidence_matches_fixtures_notes_and_benchmark() -> None:
    consumer = json.loads(_CONSUMER_EVIDENCE.read_text(encoding="utf-8"))
    beam_orientation = json.loads(
        _BEAM_ORIENTATION_EVIDENCE.read_text(encoding="utf-8")
    )
    odb = json.loads(_ODB_EVIDENCE.read_text(encoding="utf-8"))
    region = json.loads(_REGION_EVIDENCE.read_text(encoding="utf-8"))
    final15 = json.loads(_FINAL15_EVIDENCE.read_text(encoding="utf-8"))
    evaluation = json.loads(_QUERY_RESULT.read_text(encoding="utf-8"))

    consumer_fixture = _ROOT / consumer["reproduction"]["fixture"]
    assert consumer["reproduction"]["fixture_sha256"] == _sha256(consumer_fixture)
    model_fixture = _ROOT / odb["reproduction"]["model_fixture"]
    probe_fixture = _ROOT / odb["reproduction"]["probe_fixture"]
    assert odb["reproduction"]["model_fixture_sha256"] == _sha256(model_fixture)
    assert odb["reproduction"]["probe_fixture_sha256"] == _sha256(probe_fixture)
    region_fixture = _ROOT / region["reproduction"]["fixture"]
    assert region["reproduction"]["fixture_sha256"] == _sha256(region_fixture)
    beam_orientation_fixture = _ROOT / beam_orientation["reproduction"]["fixture"]
    assert beam_orientation["reproduction"]["fixture_sha256"] == _sha256(
        beam_orientation_fixture
    )
    assert final15["licensed_evidence"]["api_consumer"]["sha256"] == _sha256(
        _CONSUMER_EVIDENCE
    )
    assert final15["licensed_evidence"]["odb_behavior"]["sha256"] == _sha256(
        _ODB_EVIDENCE
    )
    assert final15["licensed_evidence"]["region_consumer"]["sha256"] == _sha256(
        _REGION_EVIDENCE
    )
    assert final15["licensed_evidence"]["beam_section_orientation"][
        "sha256"
    ] == _sha256(_BEAM_ORIENTATION_EVIDENCE)

    catalog = final15["immutable_catalog"]
    deterministic = final15["deterministic_evaluation"]
    assert catalog["source_digests"]["runtime_notes"] == _sha256(_RUNTIME_NOTES)
    assert deterministic["benchmark_sha256"] == _sha256(_QUERY_BENCHMARK)
    assert deterministic["result_sha256"] == _sha256(_QUERY_RESULT)
    assert len(load_api_catalog_cases(_QUERY_BENCHMARK)) == 27
    assert evaluation["total"] == evaluation["pass_count"] == 27
    assert all(result["passed"] for result in evaluation["results"])
    assert catalog["card_count"] == 5480
    assert catalog["conflict_count"] == 48
