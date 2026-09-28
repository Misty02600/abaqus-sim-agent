from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog import (
    AbqpyApiRecord,
    ApiCatalog,
    ApiParameter,
    ApiRuntimeNote,
    ApiSignature,
    ApiSourceRef,
)
from abaqus_sim_agent.coding.api_lookup import _api_lookup_payload

_ROOT = Path(__file__).parent.parent
_BENCHMARK_ROOT = _ROOT / "benchmarks" / "api_catalog"
_RUNTIME_NOTES = _BENCHMARK_ROOT / "abaqus_2024_runtime_notes.jsonl"
_FINAL15_RUNTIME_NOTES = (
    _BENCHMARK_ROOT / "abaqus_2024_runtime_notes_final15_20260828.jsonl"
)
_ORIENTATION_NOTE_ID = "runtime:2024:Part.assignBeamSectionOrientation:region-binding"
_FINAL15_EVIDENCE = _BENCHMARK_ROOT / "abaqus_api_catalog_final15_20260828.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _notes(path: Path) -> dict[str, ApiRuntimeNote]:
    notes = [
        ApiRuntimeNote.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len({note.note_id for note in notes}) == len(notes)
    return {note.note_id: note for note in notes}


def test_final15_verified_notes_are_scoped_and_traceable() -> None:
    evidence = json.loads(_FINAL15_EVIDENCE.read_text(encoding="utf-8"))
    notes = _notes(_FINAL15_RUNTIME_NOTES)
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


@pytest.mark.parametrize("parameter_query", [False, True])
def test_current_orientation_note_keeps_probe_scope_in_model_payload(
    tmp_path: Path, parameter_query: bool
) -> None:
    current = _notes(_RUNTIME_NOTES)
    frozen = _notes(_FINAL15_RUNTIME_NOTES)
    note = current[_ORIENTATION_NOTE_ID]
    assert current.keys() == frozen.keys()
    for note_id, historical in frozen.items():
        if note_id == _ORIENTATION_NOTE_ID:
            assert note.model_dump(exclude={"summary", "guidance"}) == (
                historical.model_dump(exclude={"summary", "guidance"})
            )
        else:
            assert current[note_id] == historical

    record = AbqpyApiRecord(
        record_id="abqpy:2024.test:PropertyPart.assignBeamSectionOrientation",
        entity_kind="method",
        declaring_name="PropertyPart.assignBeamSectionOrientation",
        class_name="PropertyPart",
        method_name="assignBeamSectionOrientation",
        public_names=("Part.assignBeamSectionOrientation",),
        signatures=(
            ApiSignature(
                text="assignBeamSectionOrientation(region, method, n1)",
                parameters=tuple(
                    ApiParameter(name=name, required=True)
                    for name in ("region", "method", "n1")
                ),
            ),
        ),
        source=ApiSourceRef(
            kind="abqpy",
            release="2024",
            version="2024.test",
            source_path="abaqus/Property/PropertyPart.py",
        ),
    )
    catalog = ApiCatalog.build(
        runtime_release="2024", abqpy_records=(record,), runtime_notes=(note,)
    )
    output = tmp_path / "catalog"
    catalog.write(output, source_digests={"runtime_notes": _sha256(_RUNTIME_NOTES)})
    reopened = ApiCatalog.open(output)
    query = "Part.assignBeamSectionOrientation" + (".region" if parameter_query else "")
    payload = _api_lookup_payload(reopened.lookup(query), error_excerpt=None)
    assert payload["status"] == "found"
    if parameter_query:
        parameter = payload["parameter"]
        assert isinstance(parameter, dict)
        delivered = parameter["runtime_notes"]
    else:
        cards = payload["cards"]
        assert isinstance(cards, list)
        delivered = cards[0]["runtime_notes"]
    assert len(delivered) == 1
    assert delivered[0]["verified"] is True
    assert delivered[0]["evidence"]["ref"] == note.evidence_ref
    assert "without raising" in delivered[0]["summary"]
    assert (
        "did not inspect stored beam orientations or write an INP"
        in (delivered[0]["guidance"])
    )
    assert (
        "does not establish effective orientation assignment"
        in (delivered[0]["guidance"])
    )
    assert "successful input generation" in delivered[0]["guidance"]
    assert "probe or wrapper is unnecessary" not in delivered[0]["guidance"]
    direction = _api_lookup_payload(
        reopened.lookup("Part.assignBeamSectionOrientation.n1"), error_excerpt=None
    )
    direction_parameter = direction["parameter"]
    assert isinstance(direction_parameter, dict)
    assert direction_parameter["runtime_notes"] == []
