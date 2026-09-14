import copy
import hashlib
import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError
from tests.test_equation_prose import DEFINITIONS, EQUATION, EXPLANATION
from tests.test_evidence_blocks import tokenizer
from tests.test_evidence_units import registry
from tests.test_unified_retrieval import Backend, Ranker

from abaqus_sim_agent.evaluation import rag_evidence_baseline as baseline
from abaqus_sim_agent.evaluation import rag_evidence_baseline_cli as cli
from abaqus_sim_agent.retrieval.equation_prose import (
    PARTITION_VERSION,
    EquationContextRelation,
    build_equation_prose_units,
)
from abaqus_sim_agent.retrieval.evidence_blocks import TokenBoundChildren
from abaqus_sim_agent.retrieval.structured_projection import project_evidence_unit


class PageBackend(Backend):
    def page_lexical(self, query, **kwargs):
        self.calls.append(("page", query))
        return []


def make(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    blocks, nodes = registry(
        EQUATION + DEFINITIONS + EXPLANATION + "<p>Ordinary statement.</p>"
    )
    partition = build_equation_prose_units(blocks, nodes)
    split = TokenBoundChildren(tokenizer())
    projected = [
        project_evidence_unit(u, split, allow_headerless=True) for u in partition.units
    ]
    mapped = {
        a.evidence.parent_id: b.evidence.parent_id
        for a, b in zip(partition.units, projected, strict=True)
    }
    relations = [
        EquationContextRelation(
            mapped[r.explanation_id],
            tuple(mapped[k] for k in r.context_ids),
            r.source_group_id,
        )
        for r in partition.relations
    ]
    units = {u.evidence.parent_id: u for u in projected}
    docs = [c for u in projected for c in split.split(u.evidence)]
    relation_file = candidate / "equation_context_relations.json"
    baseline.io.write_new(
        relation_file,
        {
            "partition_version": PARTITION_VERSION,
            "relations": [asdict(r) for r in relations],
        },
    )
    baseline.io.write_new(
        candidate / "prepared.json",
        {"equation_context_relations_sha256": baseline.io.sha(relation_file)},
    )
    backend = PageBackend(docs)
    backend.units = units
    backend.parents = {k: u.evidence for k, u in units.items()}
    backend.scopes = {}
    backend.verified_sources = {"page": "a" * 64}
    backend.split = split
    backend.dataset_binding = {}
    ranker = Ranker()
    monkeypatch.setattr(
        baseline.RagEvidenceBaseline, "_check_preset", lambda self: None
    )
    monkeypatch.setattr(baseline, "BoundCandidateBackend", lambda *args: backend)
    monkeypatch.setattr(baseline, "FixedRanker", lambda *args: ranker)
    monkeypatch.setattr(baseline.io, "_load", lambda *args: (docs, units))
    paths = (
        candidate,
        tmp_path / "symbol",
        tmp_path / "scope.json",
        tmp_path / "ranker",
    )
    return paths, backend, ranker, relations[0].explanation_id


def test_actual_composition_delivers_equation_reference_and_original_body(
    tmp_path, monkeypatch
):
    paths, backend, ranker, key = make(tmp_path, monkeypatch)
    service = baseline.RagEvidenceBaseline(*paths)
    request = baseline.BaselineCase(
        id="one", request={"kind": "question", "question": "Explain the initial term"}
    ).request
    result = service.search(request)
    item = next(e for e in result["evidence"] if e["id"] == key)
    assert item["body"] == backend.units[key].evidence.body
    assert item["equation_context"] == service.references[key]
    assert (
        item["body_scope"]
        == "complete_source_paragraph_with_equation_context_reference"
    )
    assert ranker.inputs
    assert result["context_tokens"] == service.count(
        json.dumps(result["evidence"], ensure_ascii=False, separators=(",", ":"))
    )
    assert [c[0] for c in backend.calls][:2] == ["dense", "lexical"]


@pytest.mark.parametrize("damage", ["missing", "hash", "ownership"])
def test_relation_failure_precedes_any_query(tmp_path, monkeypatch, damage):
    paths, backend, ranker, _ = make(tmp_path, monkeypatch)
    path = paths[0] / "equation_context_relations.json"
    if damage == "missing":
        path.unlink()
    elif damage == "hash":
        path.write_text("{}", encoding="utf-8")
    else:
        value = json.loads(path.read_bytes())
        value["relations"][0]["source_group_id"] = "0" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
        (paths[0] / "prepared.json").write_text(
            json.dumps({"equation_context_relations_sha256": baseline.io.sha(path)}),
            encoding="utf-8",
        )
    with pytest.raises((ValueError, FileNotFoundError)):
        baseline.RagEvidenceBaseline(*paths)
    assert backend.calls == []
    assert ranker.inputs == []


def test_unknown_preset_fails_before_backend_creation(tmp_path, monkeypatch):
    paths, backend, ranker, _ = make(tmp_path, monkeypatch)
    # Use the real method, not the fixture's relaxed preset.
    service = object.__new__(baseline.RagEvidenceBaseline)
    service.files = {"prepared": paths[0] / "prepared.json"}
    with pytest.raises(ValueError, match="content mismatch"):
        REAL_CHECK_PRESET(service)
    assert not backend.calls
    assert not ranker.inputs


REAL_CHECK_PRESET = baseline.RagEvidenceBaseline._check_preset


def case_for(key, *, alternatives=None):
    return baseline.BaselineCase(
        id="scored",
        request={"kind": "question", "question": "Explain"},
        quality_scope="annotated",
        requirements=(
            baseline.EvidenceRequirement(
                id="need", acceptable_unit_sets=alternatives or ((key,),)
            ),
        ),
    )


def test_stage_scoring_rr_dedup_and_final_body_validation(tmp_path, monkeypatch):
    paths, backend, _, key = make(tmp_path, monkeypatch)
    service = baseline.RagEvidenceBaseline(*paths)
    case = case_for(key)
    result = service.search(case.request)
    score = baseline.score_case(case, result, backend.units, service.child_to_parent)
    assert score["requirement_coverage"] == {
        "candidate": 1.0,
        "rerank_top4": 1.0,
        "delivered": 1.0,
    }
    assert score["necessary_evidence_rr_at_64"] == 1
    result["evidence"] = []
    result["status"] = "not_found"
    score = baseline.score_case(case, result, backend.units, service.child_to_parent)
    assert score["requirement_coverage"]["delivered"] == 0
    assert score["requirement_coverage"]["candidate"] == 1
    assert score["complete_evidence"]["delivered"] is False
    # Repeated children of the same parent do not add rank positions.
    child = result["trace"]["ranking"][0]
    result["trace"]["ranking"].insert(0, copy.deepcopy(child))
    assert (
        baseline.score_case(case, result, backend.units, service.child_to_parent)[
            "first_necessary_parent_rank"
        ]
        == 1
    )
    result = service.search(case.request)
    result["evidence"][0]["body"] = "tampered"
    with pytest.raises(ValueError, match="unverified"):
        baseline.score_case(case, result, backend.units, service.child_to_parent)


def test_or_and_requirements_keep_denominators_and_do_not_change_search(
    tmp_path, monkeypatch
):
    paths, backend, ranker, key = make(tmp_path, monkeypatch)
    service = baseline.RagEvidenceBaseline(*paths)
    case = case_for(key, alternatives=((key, "0" * 64), ("1" * 64,)))
    result = service.search(case.request)
    seen = copy.deepcopy(ranker.inputs)
    changed = case_for(key)
    a = baseline.score_case(case, result, backend.units, service.child_to_parent)
    b = baseline.score_case(changed, result, backend.units, service.child_to_parent)
    assert a["requirement_coverage"]["delivered"] == 0
    assert b["requirement_coverage"]["delivered"] == 1
    assert ranker.inputs == seen
    error = baseline.score_case(
        case,
        {"status": "execution_error", "evidence": []},
        backend.units,
        service.child_to_parent,
    )
    unlabelled = baseline.BaselineCase(id="plain", request=case.request)
    plain = baseline.score_case(
        unlabelled, result, backend.units, service.child_to_parent
    )
    summary = baseline.aggregate_scores([a, b, error, plain])
    assert summary["quality_scored_cases"] == 3
    assert summary["stages"]["delivered"]["requirements_total"] == 3
    assert summary["stages"]["delivered"]["requirement_recall"] == pytest.approx(1 / 3)
    assert summary["execution_errors"] == 1
    assert plain["necessary_evidence_rr_at_64"] is None


def test_source_target_scores_and_context_requirement_are_separate(
    tmp_path, monkeypatch
):
    paths, backend, _, key = make(tmp_path, monkeypatch)
    service = baseline.RagEvidenceBaseline(*paths)
    unit = backend.units[key]
    target = baseline.SourceTarget(
        id="source",
        unit_id=key,
        member_id=unit.member_ids[0],
        source_sha256=unit.evidence.source_sha256,
        source_xpath=unit.member_xpaths[0],
        body_sha256=hashlib.sha256(unit.evidence.body.encode()).hexdigest(),
        require_context_reference=True,
    )
    case = case_for(key).model_copy(update={"source_targets": (target,)})
    result = service.search(case.request)
    score = baseline.score_case(case, result, backend.units, service.child_to_parent)
    assert score["source_targets"][0]["delivered_complete"] is True
    item = next(e for e in result["evidence"] if e["id"] == key)
    item.pop("equation_context")
    assert (
        baseline.score_case(case, result, backend.units, service.child_to_parent)[
            "source_targets"
        ][0]["delivered_complete"]
        is False
    )
    result["context_tokens"] = service.count(
        json.dumps(result["evidence"], ensure_ascii=False, separators=(",", ":"))
    )
    with pytest.raises(ValueError, match="context delivery"):
        service.validate_result(case.request, result)


@pytest.mark.parametrize(
    ("scope", "requirements"), [("annotated", ()), ("unanswerable", ("label",))]
)
def test_invalid_label_scope_is_rejected(scope, requirements):
    with pytest.raises(ValidationError):
        baseline.BaselineCase(
            id="one",
            request={"kind": "question", "question": "Q"},
            quality_scope=scope,
            requirements=requirements,
        )


@pytest.mark.parametrize(
    "groups", [(), ((),), (("a" * 64, "a" * 64),), (("a" * 64,), ("a" * 64,))]
)
def test_empty_or_duplicate_requirement_groups_rejected(groups):
    with pytest.raises(ValidationError):
        baseline.EvidenceRequirement(id="need", acceptable_unit_sets=groups)


def test_empty_score_summary_is_unscored_not_perfect():
    summary = baseline.aggregate_scores([])
    assert summary["necessary_evidence_mrr_at_64"] is None
    assert summary["stages"]["delivered"]["complete_evidence_rate"] is None


def test_failed_request_cannot_gain_credit_from_partial_evidence(tmp_path, monkeypatch):
    paths, backend, _, key = make(tmp_path, monkeypatch)
    service = baseline.RagEvidenceBaseline(*paths)
    case = case_for(key)
    result = service.search(case.request)
    result["status"] = "execution_error"
    score = baseline.score_case(case, result, backend.units, service.child_to_parent)
    assert score["requirement_coverage"] == {
        "candidate": 0,
        "rerank_top4": 0,
        "delivered": 0,
    }
    assert score["necessary_evidence_rr_at_64"] == 0


@pytest.mark.parametrize("status", ["unexpected", "not_found"])
def test_invalid_success_status_is_not_scored(tmp_path, monkeypatch, status):
    paths, backend, _, key = make(tmp_path, monkeypatch)
    service = baseline.RagEvidenceBaseline(*paths)
    case = case_for(key)
    result = service.search(case.request)
    result["status"] = status
    with pytest.raises(ValueError, match="status"):
        baseline.score_case(case, result, backend.units, service.child_to_parent)


def test_composed_version_and_length_guards_do_not_call_retriever(
    tmp_path, monkeypatch
):
    paths, backend, ranker, _ = make(tmp_path, monkeypatch)
    service = baseline.RagEvidenceBaseline(*paths)
    for request, expected in [
        (
            {"kind": "question", "question": "Explain", "source_release": "missing"},
            "not_found",
        ),
        ({"kind": "question", "question": "word " * 300}, "query_too_long"),
    ]:
        typed = baseline.BaselineCase(id="q", request=request).request
        assert service.search(typed)["status"] == expected
    assert backend.calls == []
    assert ranker.inputs == []


def run_args(tmp_path, paths):
    return {
        "candidate": paths[0],
        "symbol_index": paths[1],
        "scope_rules": paths[2],
        "ranker_directory": paths[3],
        "requests": tmp_path / "requests.json",
        "output": tmp_path / "output",
    }


def test_composed_serialized_budget_exact_boundary_and_whole_unit_rejection(
    tmp_path, monkeypatch
):
    paths, _, _, _ = make(tmp_path, monkeypatch)
    request = baseline.BaselineCase(
        id="q", request={"kind": "question", "question": "Explain"}
    ).request
    first = baseline.RagEvidenceBaseline(*paths).search(request)
    required = first["context_tokens"]
    monkeypatch.setattr(
        baseline, "BUDGET", baseline.CandidateBudget(context_tokens=required)
    )
    exact = baseline.RagEvidenceBaseline(*paths).search(request)
    assert exact["evidence"] == first["evidence"]
    monkeypatch.setattr(
        baseline, "BUDGET", baseline.CandidateBudget(context_tokens=required - 1)
    )
    rejected = baseline.RagEvidenceBaseline(*paths).search(request)
    assert len(rejected["evidence"]) < len(first["evidence"])
    assert (
        rejected["trace"]["rejected"][-1]["reason"]
        == "serialized_context_budget_including_equation_references"
    )
    assert all(e in first["evidence"] for e in rejected["evidence"])


def test_cli_keyboard_interrupt_marks_incomplete_and_preserves_denominator_metadata(
    tmp_path, monkeypatch
):
    paths, _, _, key = make(tmp_path, monkeypatch)
    args = run_args(tmp_path, paths)
    args["requests"].write_text(
        baseline.BaselineBatch(cases=(case_for(key),)).model_dump_json(),
        encoding="utf-8",
    )

    def interrupt(self, request):
        raise KeyboardInterrupt()

    monkeypatch.setattr(baseline.RagEvidenceBaseline, "search", interrupt)
    with pytest.raises(KeyboardInterrupt):
        cli.run_batch(**args)
    record = baseline.io.read(args["output"] / "completion.json")
    assert record["status"] == "incomplete"
    assert record["expected_cases"] == 1
    assert record["remaining_cases"] == ["scored"]
    assert not (args["output"] / "summary.json").exists()


def test_cli_uses_real_composition_and_scores_same_output(tmp_path, monkeypatch):
    paths, _, _, key = make(tmp_path, monkeypatch)
    args = run_args(tmp_path, paths)
    batch = baseline.BaselineBatch(cases=(case_for(key),))
    args["requests"].write_text(batch.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        baseline.RagEvidenceBaseline, "assert_unchanged", lambda self: None
    )
    result = cli.run_batch(**args)
    assert result["engineering_status"] == "passed"
    assert result["quality"]["stages"]["delivered"]["requirement_recall"] == 1
    raw = baseline.io.read(args["output"] / "scored.result.json")
    assert any(e.get("equation_context") for e in raw["evidence"])
    assert baseline.io.read(args["output"] / "completion.json")["status"] == "complete"
    with pytest.raises(FileExistsError):
        cli.run_batch(**args)


def test_cli_final_drift_invalidates_batch(tmp_path, monkeypatch):
    paths, _, _, key = make(tmp_path, monkeypatch)
    args = run_args(tmp_path, paths)
    args["requests"].write_text(
        baseline.BaselineBatch(cases=(case_for(key),)).model_dump_json(),
        encoding="utf-8",
    )

    def changed(self):
        raise ValueError("changed")

    monkeypatch.setattr(baseline.RagEvidenceBaseline, "assert_unchanged", changed)
    with pytest.raises(ValueError, match="changed"):
        cli.run_batch(**args)
    assert (args["output"] / "scored.result.json").exists()
    assert not (args["output"] / "summary.json").exists()
    assert baseline.io.read(args["output"] / "completion.json")["status"] == "invalid"


def test_cli_runtime_failure_retains_scored_case(tmp_path, monkeypatch):
    paths, _, _, key = make(tmp_path, monkeypatch)
    args = run_args(tmp_path, paths)
    args["requests"].write_text(
        baseline.BaselineBatch(cases=(case_for(key),)).model_dump_json(),
        encoding="utf-8",
    )

    def broken(self, request):
        raise RuntimeError("failure")

    monkeypatch.setattr(baseline.RagEvidenceBaseline, "search", broken)
    monkeypatch.setattr(
        baseline.RagEvidenceBaseline, "assert_unchanged", lambda self: None
    )
    result = cli.run_batch(**args)
    assert result["engineering_status"] == "failed"
    assert result["quality"]["stages"]["delivered"]["requirements_total"] == 1
    assert result["quality"]["stages"]["delivered"]["requirement_recall"] == 0


def test_cli_invalid_input_never_calls_backend(tmp_path, monkeypatch):
    paths, backend, _, _ = make(tmp_path, monkeypatch)
    args = run_args(tmp_path, paths)
    args["requests"].write_text('{"cases":[]}', encoding="utf-8")
    with pytest.raises(ValidationError):
        cli.run_batch(**args)
    assert backend.calls == []
    assert (
        baseline.io.read(args["output"] / "completion.json")["stage"]
        == "input_validation"
    )
