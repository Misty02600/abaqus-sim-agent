"""离线汇总整任务中的 API Catalog 使用成本与人工审查结果。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

STAGE_STATUSES = {"correct", "incorrect", "not_applicable", "not_reviewed"}
QUERY_ASSESSMENTS = {"useful", "redundant", "unclear"}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def _query_ref(kind: str, ordinal: int) -> str:
    return f"{kind}:{ordinal}"


def _case_map(case_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = case_manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("case manifest must contain a `cases` list")
    result: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            raise ValueError("each case must contain a string `case_id`")
        case_id = case["case_id"]
        if case_id in result:
            raise ValueError(f"duplicate case_id: {case_id}")
        result[case_id] = case
    return result


def _catalog_metrics(state_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lookups = _load_jsonl(state_dir / "api_lookups.jsonl")
    discoveries = _load_jsonl(state_dir / "api_discoveries.jsonl")

    query_rows: list[dict[str, Any]] = []
    lookup_keys: list[str] = []
    discovery_keys: list[tuple[str | None, str | None, int]] = []
    delivered_card_ids: set[str] = set()
    delivered_parameters: set[tuple[str, str]] = set()
    delivered_note_ids: set[str] = set()
    discovery_candidate_card_ids: set[str] = set()

    for ordinal, row in enumerate(lookups, start=1):
        query = row.get("query")
        if not isinstance(query, str):
            raise ValueError(f"lookup row {ordinal} has no string query")
        lookup_keys.append(query.casefold())
        delivered_cards = [
            value for value in row.get("delivered_card_ids", []) if isinstance(value, str)
        ]
        delivered_card_ids.update(delivered_cards)
        parameters = []
        for item in row.get("delivered_parameters", []):
            if not isinstance(item, dict):
                continue
            card_id = item.get("card_id")
            parameter_name = item.get("parameter_name")
            if isinstance(card_id, str) and isinstance(parameter_name, str):
                delivered_parameters.add((card_id, parameter_name))
                parameters.append(f"{card_id}.{parameter_name}")
        notes = [
            value for value in row.get("delivered_note_ids", []) if isinstance(value, str)
        ]
        delivered_note_ids.update(notes)
        query_rows.append(
            {
                "ref": _query_ref("lookup", ordinal),
                "kind": "lookup",
                "query": query,
                "status": row.get("status"),
                "counted": bool(row.get("counted", False)),
                "payload_bytes": int(row.get("payload_bytes", 0)),
                "delivered_card_ids": delivered_cards,
                "delivered_parameters": parameters,
                "delivered_note_ids": notes,
            }
        )

    for ordinal, row in enumerate(discoveries, start=1):
        receiver = row.get("receiver")
        query = row.get("query")
        limit = int(row.get("limit", 0))
        if receiver is not None and not isinstance(receiver, str):
            raise ValueError(f"discovery row {ordinal} has invalid receiver")
        if query is not None and not isinstance(query, str):
            raise ValueError(f"discovery row {ordinal} has invalid query")
        discovery_keys.append(
            (
                receiver.casefold() if isinstance(receiver, str) else None,
                query.casefold() if isinstance(query, str) else None,
                limit,
            )
        )
        candidate_ids = [
            value for value in row.get("candidate_card_ids", []) if isinstance(value, str)
        ]
        discovery_candidate_card_ids.update(candidate_ids)
        query_rows.append(
            {
                "ref": _query_ref("discovery", ordinal),
                "kind": "discovery",
                "receiver": receiver,
                "query": query,
                "limit": limit,
                "status": row.get("status"),
                "counted": bool(row.get("counted", False)),
                "payload_bytes": int(row.get("payload_bytes", 0)),
                "candidate_card_ids": candidate_ids,
                "candidate_names": [
                    value
                    for value in row.get("candidate_names", [])
                    if isinstance(value, str)
                ],
            }
        )

    lookup_duplicates = sum(count - 1 for count in Counter(lookup_keys).values() if count > 1)
    discovery_duplicates = sum(
        count - 1 for count in Counter(discovery_keys).values() if count > 1
    )
    exact_payload_bytes = sum(int(row.get("payload_bytes", 0)) for row in lookups)
    discovery_payload_bytes = sum(
        int(row.get("payload_bytes", 0)) for row in discoveries
    )

    metrics = {
        "lookup_attempts": len(lookups),
        "lookup_counted": sum(bool(row.get("counted", False)) for row in lookups),
        "discovery_attempts": len(discoveries),
        "discovery_counted": sum(
            bool(row.get("counted", False)) for row in discoveries
        ),
        "catalog_attempts": len(lookups) + len(discoveries),
        "catalog_budget_calls": sum(bool(row.get("counted", False)) for row in lookups)
        + sum(bool(row.get("counted", False)) for row in discoveries),
        "lookup_duplicate_attempts": lookup_duplicates,
        "discovery_duplicate_attempts": discovery_duplicates,
        "duplicate_attempts": lookup_duplicates + discovery_duplicates,
        "lookup_payload_bytes": exact_payload_bytes,
        "discovery_payload_bytes": discovery_payload_bytes,
        "catalog_payload_bytes": exact_payload_bytes + discovery_payload_bytes,
        "unique_delivered_card_ids": len(delivered_card_ids),
        "unique_delivered_parameters": len(delivered_parameters),
        "unique_delivered_note_ids": len(delivered_note_ids),
        "unique_discovery_candidate_card_ids": len(discovery_candidate_card_ids),
        "candidate_exact_delivery_overlap": len(
            discovery_candidate_card_ids & delivered_card_ids
        ),
    }
    return metrics, query_rows


def _review_metrics(
    review: dict[str, Any] | None,
    *,
    expected_stages: list[str],
    query_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if review is None:
        return {
            "review_status": "missing",
            "api_usage_points_total": None,
            "api_usage_points_catalog_assisted": None,
            "api_usage_points_direct": None,
            "main_api_path": "not_reviewed",
            "stage_reviews": dict.fromkeys(expected_stages, "not_reviewed"),
            "useful_queries": None,
            "redundant_queries": None,
            "unclear_queries": None,
            "unreviewed_queries": len(query_rows),
            "missed_uncertainties": None,
            "review_notes": [],
        }

    total = review.get("api_usage_points_total")
    assisted = review.get("api_usage_points_catalog_assisted")
    if not isinstance(total, int) or total < 0:
        raise ValueError("review api_usage_points_total must be a non-negative integer")
    if not isinstance(assisted, int) or assisted < 0 or assisted > total:
        raise ValueError(
            "review api_usage_points_catalog_assisted must be between 0 and total"
        )

    raw_stage_reviews = review.get("stage_reviews", {})
    if not isinstance(raw_stage_reviews, dict):
        raise ValueError("review stage_reviews must be an object")
    stage_reviews: dict[str, str] = {}
    for stage in expected_stages:
        status = raw_stage_reviews.get(stage, "not_reviewed")
        if status not in STAGE_STATUSES:
            raise ValueError(f"invalid stage review for {stage}: {status}")
        stage_reviews[stage] = status
    unexpected_stages = set(raw_stage_reviews) - set(expected_stages)
    if unexpected_stages:
        raise ValueError(
            "review contains stages outside the case manifest: "
            + ", ".join(sorted(unexpected_stages))
        )

    if any(status == "incorrect" for status in stage_reviews.values()):
        main_api_path = "incorrect"
    elif any(status == "not_reviewed" for status in stage_reviews.values()):
        main_api_path = "not_reviewed"
    else:
        main_api_path = "correct"

    raw_query_reviews = review.get("query_reviews", {})
    if not isinstance(raw_query_reviews, dict):
        raise ValueError("review query_reviews must be an object")
    known_refs = {row["ref"] for row in query_rows}
    unknown_refs = set(raw_query_reviews) - known_refs
    if unknown_refs:
        raise ValueError(
            "review references unknown Catalog queries: " + ", ".join(sorted(unknown_refs))
        )
    query_counter: Counter[str] = Counter()
    for ref, assessment in raw_query_reviews.items():
        if assessment not in QUERY_ASSESSMENTS:
            raise ValueError(f"invalid query assessment for {ref}: {assessment}")
        query_counter[assessment] += 1

    missed = review.get("missed_uncertainties", [])
    if not isinstance(missed, list) or any(not isinstance(item, dict) for item in missed):
        raise ValueError("review missed_uncertainties must be a list of objects")
    notes = review.get("notes", [])
    if not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
        raise ValueError("review notes must be a list of strings")

    return {
        "review_status": "complete"
        if len(raw_query_reviews) == len(query_rows) and main_api_path != "not_reviewed"
        else "partial",
        "api_usage_points_total": total,
        "api_usage_points_catalog_assisted": assisted,
        "api_usage_points_direct": total - assisted,
        "main_api_path": main_api_path,
        "stage_reviews": stage_reviews,
        "useful_queries": query_counter["useful"],
        "redundant_queries": query_counter["redundant"],
        "unclear_queries": query_counter["unclear"],
        "unreviewed_queries": len(query_rows) - len(raw_query_reviews),
        "missed_uncertainties": len(missed),
        "missed_uncertainty_items": missed,
        "review_notes": notes,
    }


def observe_run(
    run: dict[str, Any],
    *,
    base_dir: Path,
    cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_id = run.get("case_id")
    profile = run.get("profile")
    state_dir_value = run.get("state_dir")
    if not isinstance(case_id, str) or case_id not in cases:
        raise ValueError(f"unknown case_id: {case_id}")
    if not isinstance(profile, str) or not profile:
        raise ValueError(f"run {case_id} must contain a non-empty profile")
    if not isinstance(state_dir_value, str) or not state_dir_value:
        raise ValueError(f"run {case_id} must contain state_dir")
    state_dir = (base_dir / state_dir_value).resolve()
    request_path = state_dir / "request.json"
    if not request_path.is_file():
        raise ValueError(f"state directory has no request.json: {state_dir}")
    request = _load_json(request_path)
    run_id = request.get("run_id")
    workspace = request.get("workspace")
    budget = request.get("budget") if isinstance(request.get("budget"), dict) else {}

    catalog_metrics, query_rows = _catalog_metrics(state_dir)
    review = None
    review_path_value = run.get("review_path")
    if review_path_value is not None:
        if not isinstance(review_path_value, str) or not review_path_value:
            raise ValueError(f"run {case_id} has invalid review_path")
        review_path = (base_dir / review_path_value).resolve()
        review = _load_json(review_path)
        if review.get("case_id") not in (None, case_id):
            raise ValueError(f"review case_id does not match run: {review_path}")
        if review.get("run_id") not in (None, run_id):
            raise ValueError(f"review run_id does not match request.json: {review_path}")

    expected_stages = cases[case_id].get("expected_stages", [])
    if not isinstance(expected_stages, list) or any(
        not isinstance(stage, str) for stage in expected_stages
    ):
        raise ValueError(f"case {case_id} has invalid expected_stages")
    review_metrics = _review_metrics(
        review, expected_stages=expected_stages, query_rows=query_rows
    )

    outcome_status = None
    outcome_path = state_dir / "outcome.json"
    if outcome_path.is_file():
        outcome_status = _load_json(outcome_path).get("status")

    max_api_lookup_calls = budget.get("max_api_lookup_calls")
    if not isinstance(max_api_lookup_calls, int):
        max_api_lookup_calls = None

    return {
        "case_id": case_id,
        "profile": profile,
        "run_id": run_id,
        "state_dir": str(state_dir),
        "workspace": workspace,
        "outcome_status": outcome_status,
        "max_api_lookup_calls": max_api_lookup_calls,
        **catalog_metrics,
        **review_metrics,
        "queries": query_rows,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [row for row in rows if row["review_status"] != "missing"]
    complete = [row for row in rows if row["review_status"] == "complete"]
    return {
        "runs": len(rows),
        "reviewed_runs": len(reviewed),
        "complete_reviews": len(complete),
        "catalog_attempts": sum(row["catalog_attempts"] for row in rows),
        "catalog_budget_calls": sum(row["catalog_budget_calls"] for row in rows),
        "lookup_attempts": sum(row["lookup_attempts"] for row in rows),
        "discovery_attempts": sum(row["discovery_attempts"] for row in rows),
        "catalog_payload_bytes": sum(row["catalog_payload_bytes"] for row in rows),
        "duplicate_attempts": sum(row["duplicate_attempts"] for row in rows),
        "api_usage_points_total": sum(
            row["api_usage_points_total"] or 0 for row in reviewed
        ),
        "api_usage_points_catalog_assisted": sum(
            row["api_usage_points_catalog_assisted"] or 0 for row in reviewed
        ),
        "api_usage_points_direct": sum(
            row["api_usage_points_direct"] or 0 for row in reviewed
        ),
        "useful_queries": sum(row["useful_queries"] or 0 for row in reviewed),
        "redundant_queries": sum(row["redundant_queries"] or 0 for row in reviewed),
        "unclear_queries": sum(row["unclear_queries"] or 0 for row in reviewed),
        "unreviewed_queries": sum(row["unreviewed_queries"] for row in rows),
        "missed_uncertainties": sum(
            row["missed_uncertainties"] or 0 for row in reviewed
        ),
        "main_api_path_correct_runs": sum(
            row["main_api_path"] == "correct" for row in reviewed
        ),
    }


def render_markdown(observation: dict[str, Any]) -> str:
    summary = observation["summary"]
    lines = [
        "# Full-task API efficiency observation",
        "",
        "This report separates deterministic Catalog telemetry from manual engineering review. Discovery candidates are navigation only and are never counted as delivered Exact evidence.",
        "",
        "## Batch summary",
        "",
        f"- Runs: {summary['runs']}; complete manual reviews: {summary['complete_reviews']}.",
        f"- Catalog calls: {summary['catalog_attempts']} attempts, {summary['catalog_budget_calls']} counted against the shared budget; Exact {summary['lookup_attempts']}, discovery {summary['discovery_attempts']}.",
        f"- Catalog payload: {summary['catalog_payload_bytes']} UTF-8 bytes; duplicate attempts: {summary['duplicate_attempts']}.",
        f"- Reviewed API usage points: {summary['api_usage_points_total']}; direct {summary['api_usage_points_direct']}; Catalog-assisted {summary['api_usage_points_catalog_assisted']}.",
        f"- Query review: useful {summary['useful_queries']}, redundant {summary['redundant_queries']}, unclear {summary['unclear_queries']}, unreviewed {summary['unreviewed_queries']}.",
        f"- Main API path correct: {summary['main_api_path_correct_runs']}/{summary['reviewed_runs']} reviewed runs; missed uncertainty items: {summary['missed_uncertainties']}.",
        "",
        "## Runs",
        "",
        "| Case | Profile | Outcome | API points | Direct | Assisted | Exact | Discovery | Payload bytes | Useful / redundant | Missed | Main path |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for row in observation["rows"]:
        useful = row["useful_queries"]
        redundant = row["redundant_queries"]
        query_review = "n/a" if useful is None else f"{useful} / {redundant}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["case_id"]),
                    str(row["profile"]),
                    str(row["outcome_status"] or "unknown"),
                    str(row["api_usage_points_total"] if row["api_usage_points_total"] is not None else "n/a"),
                    str(row["api_usage_points_direct"] if row["api_usage_points_direct"] is not None else "n/a"),
                    str(row["api_usage_points_catalog_assisted"] if row["api_usage_points_catalog_assisted"] is not None else "n/a"),
                    str(row["lookup_attempts"]),
                    str(row["discovery_attempts"]),
                    str(row["catalog_payload_bytes"]),
                    query_review,
                    str(row["missed_uncertainties"] if row["missed_uncertainties"] is not None else "n/a"),
                    str(row["main_api_path"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation rule",
            "",
            "Do not optimize toward a fixed Catalog-call cap in this first batch. A healthy result is a correct main API path with few, useful queries concentrated around real uncertainty, low redundant-query count, and no unreviewed incorrect/suspicious API usage. Runtime/verifier evidence remains separate from this API-efficiency review.",
            "",
        ]
    )
    return "\n".join(lines)


def observe_batch(
    *,
    case_manifest_path: Path,
    run_manifest_path: Path,
) -> dict[str, Any]:
    case_manifest = _load_json(case_manifest_path)
    cases = _case_map(case_manifest)
    run_manifest = _load_json(run_manifest_path)
    runs = run_manifest.get("runs")
    if not isinstance(runs, list):
        raise ValueError("run manifest must contain a `runs` list")
    rows = [
        observe_run(run, base_dir=run_manifest_path.parent, cases=cases)
        for run in runs
        if isinstance(run, dict)
    ]
    if len(rows) != len(runs):
        raise ValueError("every run manifest entry must be an object")
    return {
        "schema_version": "full-task-api-efficiency-observation-v1",
        "case_manifest": str(case_manifest_path.resolve()),
        "run_manifest": str(run_manifest_path.resolve()),
        "summary": _summary(rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    observation = observe_batch(
        case_manifest_path=args.cases.resolve(),
        run_manifest_path=args.runs.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(observation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(observation), encoding="utf-8")


if __name__ == "__main__":
    main()
