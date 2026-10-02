import json
import subprocess
import sys
from copy import deepcopy

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.evaluation import public_mechanics_cases as cases_module
from abaqus_sim_agent.evaluation import public_mechanics_suite as suite
from abaqus_sim_agent.evaluation.case_metadata import CaseMetadata, SourceRef
from abaqus_sim_agent.evaluation.public_mechanics_cases import (
    BASIC_LINEAR_CASE_IDS,
    PUBLIC_MECHANICS_PROTOCOL,
    get_case,
    load_frozen_cases,
    mechanics_cases,
    selected_case_ids,
)
from abaqus_sim_agent.evaluation.public_mechanics_control import (
    build_report,
    read_json,
    save_json,
)


def metadata_payload():
    return {
        "description": "  检查均布载荷与挠度  ",
        "tags": ["distributed-load", "displacement"],
        "source": {
            "kind": "project_analytic",
            "reference": "  Euler–Bernoulli梁  ",
        },
    }


def test_metadata_json_roundtrip_is_normalized_and_immutable():
    metadata = CaseMetadata.model_validate_json(json.dumps(metadata_payload()))
    assert metadata.description == "检查均布载荷与挠度"
    assert metadata.source.reference == "Euler–Bernoulli梁"
    assert metadata.tags == ("distributed-load", "displacement")
    assert isinstance(metadata.model_dump(mode="json")["tags"], list)
    assert CaseMetadata.model_validate_json(metadata.model_dump_json()) == metadata
    with pytest.raises(ValidationError, match="frozen"):
        metadata.description = "changed"


@pytest.mark.parametrize(
    "patch",
    [
        {"tag": ["distributed-load"]},
        {"description": " \n "},
        {"description": 42},
        {"tags": []},
        {"tags": ["distributed-load", "distributed-load"]},
        {"tags": ["beam"]},  # family不是独立维护一遍的能力标签。
        {"tags": "distributed-load"},
        {"status": "scored"},
        {"adaptation": "CPS4R only"},
    ],
)
def test_invalid_metadata_fails_at_loading_boundary(patch):
    with pytest.raises(ValidationError):
        CaseMetadata.model_validate_json(json.dumps(metadata_payload() | patch))


def test_metadata_python_validation_does_not_coerce_bytes_or_mutable_tag_lists():
    payload = metadata_payload()
    payload["tags"] = ("distributed-load", "displacement")
    payload["description"] = b"not text"
    with pytest.raises(ValidationError):
        CaseMetadata.model_validate(payload)
    payload["description"] = "text"
    assert CaseMetadata.model_validate(payload).description == "text"
    payload["tags"] = ["distributed-load"]
    with pytest.raises(ValidationError):
        CaseMetadata.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "ansys", "reference": "VM184"},
        {"kind": "nafems", "reference": "P18.LE6", "url": None},
        {"kind": "project_analytic", "reference": " "},
        {"kind": "project_analytic", "reference": "formula", "document_version": " "},
        {"kind": "other", "reference": "formula"},
        {"kind": "ansys", "reference": "VM184", "url": "file:///private.inp"},
        {"kind": "nafems", "reference": "LE6", "url": "not-a-url"},
        {"kind": "project_analytic", "reference": "formula", "runtime_version": "2024"},
    ],
)
def test_invalid_source_is_rejected(payload):
    with pytest.raises(ValidationError):
        SourceRef.model_validate_json(json.dumps(payload))


def test_external_source_keeps_document_version_separate_from_runtime():
    source = SourceRef.model_validate_json(
        json.dumps(
            {
                "kind": "ansys",
                "reference": "VM184",
                "url": "https://example.org/vm184",
                "document_version": "2025 R2",
            }
        )
    )
    assert source.document_version == "2025 R2"
    assert source.model_dump(mode="json")["url"] == "https://example.org/vm184"


def test_all_cases_have_metadata_but_only_fifteen_are_scored_by_default():
    cases = mechanics_cases()
    assert len(cases) == 20
    assert len(BASIC_LINEAR_CASE_IDS) == len(set(BASIC_LINEAR_CASE_IDS)) == 15
    excluded = {
        "uniform-axial-bar",
        "stepped-axial-bar",
        "cantilever-tip-load",
        "plane-stress-uniform-tension",
        "nafems-le11-thermal-solid",
    }
    assert {c.case_id for c in cases} - set(BASIC_LINEAR_CASE_IDS) == excluded
    assert selected_case_ids() == BASIC_LINEAR_CASE_IDS
    assert selected_case_ids(["uniform-axial-bar"]) == ("uniform-axial-bar",)
    for case in cases:
        saved = case.model_dump(mode="json")
        assert "source" not in saved
        assert "source" in saved["metadata"]
        assert type(case).model_validate_json(case.model_dump_json()) == case


@pytest.mark.parametrize("names", [[], ["unknown"], ["uniform-axial-bar"] * 2])
def test_invalid_selection_is_rejected_before_creating_a_batch(tmp_path, names):
    root = tmp_path / "run"
    with pytest.raises(ValueError, match="selection"):
        suite.prepare(root, tmp_path / "missing-catalog", names)
    assert not root.exists()


def test_cli_prepare_freezes_the_default_scoring_set_and_metadata(
    tmp_path, monkeypatch
):
    root = tmp_path / "run"
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    launcher = tmp_path / "launcher.bat"
    launcher.write_text("fixture only", encoding="ascii")
    monkeypatch.setattr(suite, "DEFAULT_ABAQUS_2024_LAUNCHER", launcher)
    monkeypatch.setattr(suite.ApiCatalog, "open", lambda path: object())
    monkeypatch.setattr(
        "sys.argv", ["suite", "prepare", "--root", str(root), "--catalog", str(catalog)]
    )
    suite.main()
    manifest = read_json(root / "manifest.json")
    assert manifest["version"] == PUBLIC_MECHANICS_PROTOCOL
    assert (
        manifest["case_ids"]
        == manifest["scoring_case_ids"]
        == list(BASIC_LINEAR_CASE_IDS)
    )
    frozen = load_frozen_cases(root, manifest["case_ids"])
    assert tuple(c.case_id for c in frozen) == BASIC_LINEAR_CASE_IDS
    assert all(c.metadata.description for c in frozen)
    assert read_json(root / "goals.json") == {c.case_id: c.goal() for c in frozen}
    report = build_report(root, bindings_unchanged=True)
    assert report["scoring"]["full_set_selected"]
    assert report["scoring"]["case_count"] == 15
    assert report["scoring"]["classification_counts"] == {"not_started": 15}

    # A freshly frozen worker must load the extracted CLI from its own snapshot.
    completed = subprocess.run(
        [sys.executable, "-I", str(root / "worker.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert b"--phoenix" in completed.stdout
    assert (
        root / "snapshot/src/abaqus_sim_agent/evaluation/public_mechanics_cli.py"
    ).is_file()


def test_prepare_keeps_explicit_historical_case_available_without_score(
    tmp_path, monkeypatch
):
    root = tmp_path / "history-run"
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    launcher = tmp_path / "launcher.bat"
    launcher.write_text("fixture only", encoding="ascii")
    monkeypatch.setattr(suite, "DEFAULT_ABAQUS_2024_LAUNCHER", launcher)
    monkeypatch.setattr(suite.ApiCatalog, "open", lambda path: object())
    suite.prepare(root, catalog, ["uniform-axial-bar"])
    report = build_report(root, bindings_unchanged=True)
    assert len(report["trials"]) == 1
    assert report["trials"][0]["case_id"] == "uniform-axial-bar"
    assert report["trials"][0]["included_in_score"] is False
    assert report["scoring"]["case_count"] == 0
    assert report["scoring"]["classification_counts"] == {}
    assert not report["scoring"]["full_set_selected"]


def frozen_fixture(root):
    names = ["solid-cantilever-directional-bending", "uniform-axial-bar"]
    save_json(
        root / "manifest.json",
        {
            "version": PUBLIC_MECHANICS_PROTOCOL,
            "case_ids": names,
            "scoring_case_ids": list(BASIC_LINEAR_CASE_IDS),
        },
    )
    save_json(root / "cases.json", [get_case(n).model_dump(mode="json") for n in names])
    seal_fixture(root)
    return names


def seal_fixture(root):
    save_json(
        root / "seal.json",
        {
            "files": {
                name: suite.file_digest(root / name)
                for name in ("manifest.json", "cases.json")
            }
        },
    )


def test_report_uses_frozen_metadata_and_explicit_case_membership(
    tmp_path, monkeypatch
):
    names = frozen_fixture(tmp_path)
    saved = read_json(tmp_path / "cases.json")
    saved[0]["metadata"]["description"] = "当批描述 | 保留\n另行"
    # 两题标签完全相同，历史题仍不能进入评分分母。
    saved[1]["metadata"]["tags"] = deepcopy(saved[0]["metadata"]["tags"])
    save_json(tmp_path / "cases.json", saved)
    seal_fixture(tmp_path)
    for name in names:
        save_json(tmp_path / name / "state/outcome.json", {"status": "completed"})
    original_outcomes = [
        (tmp_path / n / "state/outcome.json").read_bytes() for n in names
    ]

    def no_registry_lookup():
        raise AssertionError("report must not read today's case definitions")

    monkeypatch.setattr(cases_module, "mechanics_cases", no_registry_lookup)
    report = build_report(tmp_path, bindings_unchanged=True)
    assert (
        report["trials"][0]["metadata"]["description"]
        == saved[0]["metadata"]["description"]
    )
    assert [r["included_in_score"] for r in report["trials"]] == [True, False]
    assert report["scoring"]["case_ids"] == names[:1]
    assert report["scoring"]["case_count"] == 1  # VM184的三工况仍合计一道题。
    assert not report["scoring"]["full_set_selected"]
    assert report["scoring"]["classification_counts"] == {"completed": 1}
    assert "当批描述 \\| 保留 另行" in (tmp_path / "report.md").read_text(
        encoding="utf-8"
    )
    assert original_outcomes == [
        (tmp_path / n / "state/outcome.json").read_bytes() for n in names
    ]


@pytest.mark.parametrize("path", ["cases.json", "manifest.json"])
def test_report_rejects_unsealed_changes_without_overwriting_existing_report(
    tmp_path, path
):
    frozen_fixture(tmp_path)
    sentinel = b"existing report"
    (tmp_path / "report.json").write_bytes(sentinel)
    with (tmp_path / path).open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="digest mismatch"):
        build_report(tmp_path, bindings_unchanged=False)
    assert (tmp_path / "report.json").read_bytes() == sentinel


@pytest.mark.parametrize("defect", ["metadata", "duplicate", "reordered", "missing"])
def test_frozen_case_loading_rejects_invalid_contracts(tmp_path, defect):
    names = frozen_fixture(tmp_path)
    rows = read_json(tmp_path / "cases.json")
    if defect == "metadata":
        rows[0]["metadata"]["tag"] = rows[0]["metadata"].pop("tags")
    elif defect == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif defect == "reordered":
        rows.reverse()
    else:
        rows.pop()
    save_json(tmp_path / "cases.json", rows)
    seal_fixture(tmp_path)
    with pytest.raises(ValueError, match=r"metadata|frozen cases"):
        load_frozen_cases(tmp_path, names)


@pytest.mark.parametrize(
    "protocol",
    [
        "public-mechanics-e2e-v23-no-planning-request80-relaxed-limits",
        "public-mechanics-e2e-v25-native-result-grading",
    ],
)
def test_direct_report_builder_refuses_old_protocol_without_migration(
    tmp_path, protocol
):
    save_json(
        tmp_path / "manifest.json",
        {"version": "public-mechanics-e2e-v23-no-planning-request80-relaxed-limits"},
    )
    original = b'{"original":true}'
    (tmp_path / "report.json").write_bytes(original)
    with pytest.raises(ValueError, match="historical"):
        build_report(tmp_path, bindings_unchanged=True)
    assert (tmp_path / "report.json").read_bytes() == original
