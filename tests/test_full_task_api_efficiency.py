import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = (
        Path(__file__).parents[1]
        / "benchmarks/api_catalog/observe_full_task_api_efficiency.py"
    )
    spec = importlib.util.spec_from_file_location("full_task_api_efficiency", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_observer_separates_discovery_candidates_from_exact_delivery(tmp_path):
    observer = module()
    cases = tmp_path / "cases.json"
    runs = tmp_path / "runs.json"
    state = tmp_path / "state"
    review = tmp_path / "review.json"
    write_json(
        cases,
        {
            "cases": [
                {
                    "case_id": "fixture",
                    "expected_stages": ["mesh", "postprocess"],
                }
            ]
        },
    )
    write_json(
        state / "request.json",
        {
            "run_id": "run-1",
            "workspace": str(tmp_path / "workspace"),
            "budget": {"max_api_lookup_calls": 8},
        },
    )
    write_json(state / "outcome.json", {"status": "completed"})
    write_jsonl(
        state / "api_discoveries.jsonl",
        [
            {
                "receiver": "Part",
                "query": "seed",
                "limit": 20,
                "status": "candidates",
                "candidate_card_ids": ["Part.seedPart"],
                "candidate_names": ["Part.seedPart"],
                "counted": True,
                "payload_bytes": 120,
            }
        ],
    )
    write_jsonl(
        state / "api_lookups.jsonl",
        [
            {
                "source": "active",
                "query": "Part.seedPart",
                "status": "found",
                "card_ids": ["Part.seedPart"],
                "delivered_card_ids": ["Part.seedPart"],
                "delivered_parameters": [],
                "delivered_note_ids": [],
                "counted": True,
                "payload_bytes": 480,
            },
            {
                "source": "active",
                "query": "Part.seedPart",
                "status": "duplicate",
                "card_ids": ["Part.seedPart"],
                "delivered_card_ids": [],
                "delivered_parameters": [],
                "delivered_note_ids": [],
                "counted": False,
                "payload_bytes": 40,
            },
        ],
    )
    write_json(
        review,
        {
            "case_id": "fixture",
            "run_id": "run-1",
            "api_usage_points_total": 20,
            "api_usage_points_catalog_assisted": 1,
            "stage_reviews": {"mesh": "correct", "postprocess": "correct"},
            "query_reviews": {
                "discovery:1": "useful",
                "lookup:1": "useful",
                "lookup:2": "redundant",
            },
            "missed_uncertainties": [],
            "notes": [],
        },
    )
    write_json(
        runs,
        {
            "runs": [
                {
                    "case_id": "fixture",
                    "profile": "full_with_discovery",
                    "state_dir": "state",
                    "review_path": "review.json",
                }
            ]
        },
    )

    observed = observer.observe_batch(case_manifest_path=cases, run_manifest_path=runs)
    row = observed["rows"][0]
    assert row["catalog_attempts"] == 3
    assert row["catalog_budget_calls"] == 2
    assert row["lookup_attempts"] == 2
    assert row["discovery_attempts"] == 1
    assert row["duplicate_attempts"] == 1
    assert row["catalog_payload_bytes"] == 640
    assert row["unique_discovery_candidate_card_ids"] == 1
    assert row["unique_delivered_card_ids"] == 1
    assert row["candidate_exact_delivery_overlap"] == 1
    assert row["api_usage_points_direct"] == 19
    assert row["useful_queries"] == 2
    assert row["redundant_queries"] == 1
    assert row["main_api_path"] == "correct"
    assert observed["summary"]["api_usage_points_direct"] == 19


def test_observer_allows_missing_review_without_inventing_semantic_score(tmp_path):
    observer = module()
    cases = tmp_path / "cases.json"
    runs = tmp_path / "runs.json"
    state = tmp_path / "state"
    write_json(
        cases,
        {"cases": [{"case_id": "fixture", "expected_stages": ["geometry"]}]},
    )
    write_json(
        state / "request.json",
        {
            "run_id": "run-2",
            "workspace": str(tmp_path / "workspace"),
            "budget": {"max_api_lookup_calls": 4},
        },
    )
    write_json(
        runs,
        {
            "runs": [
                {
                    "case_id": "fixture",
                    "profile": "full",
                    "state_dir": "state",
                }
            ]
        },
    )

    observed = observer.observe_batch(case_manifest_path=cases, run_manifest_path=runs)
    row = observed["rows"][0]
    assert row["review_status"] == "missing"
    assert row["main_api_path"] == "not_reviewed"
    assert row["api_usage_points_total"] is None
    assert row["useful_queries"] is None
    assert observed["summary"]["reviewed_runs"] == 0


def test_observer_rejects_review_that_references_nonexistent_query(tmp_path):
    observer = module()
    cases = tmp_path / "cases.json"
    runs = tmp_path / "runs.json"
    state = tmp_path / "state"
    review = tmp_path / "review.json"
    write_json(
        cases,
        {"cases": [{"case_id": "fixture", "expected_stages": ["mesh"]}]},
    )
    write_json(
        state / "request.json",
        {"run_id": "run-3", "workspace": "workspace", "budget": {}},
    )
    write_json(
        review,
        {
            "case_id": "fixture",
            "run_id": "run-3",
            "api_usage_points_total": 3,
            "api_usage_points_catalog_assisted": 0,
            "stage_reviews": {"mesh": "correct"},
            "query_reviews": {"lookup:1": "useful"},
            "missed_uncertainties": [],
            "notes": [],
        },
    )
    write_json(
        runs,
        {
            "runs": [
                {
                    "case_id": "fixture",
                    "profile": "full_with_discovery",
                    "state_dir": "state",
                    "review_path": "review.json",
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="unknown Catalog queries"):
        observer.observe_batch(case_manifest_path=cases, run_manifest_path=runs)
