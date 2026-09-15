"""Audit a bounded modeling catalog candidate without importing or running Abaqus."""

import argparse
import hashlib
import json
from pathlib import Path

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.api_catalog.evaluation import (
    evaluate_api_catalog,
    load_api_catalog_cases,
)
from abaqus_sim_agent.coding.persistence import write_text_atomic


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_core(catalog, scope):
    rows = []
    for group, names in scope["method_groups"].items():
        for name in names:
            result = catalog.lookup(name)
            cards = result.cards
            rows.append(
                {
                    "query": name,
                    "group": group,
                    "kind": "method",
                    "status": result.status,
                    "canonical": [c.canonical_name for c in cards],
                    "passed": result.status == "found"
                    and len(cards) == 1
                    and bool(cards[0].signatures)
                    and bool(cards[0].official_records),
                    "has_signature": any(c.signatures for c in cards),
                    "has_official": any(c.official_records for c in cards),
                    "official_summaries": [
                        r.summary for c in cards for r in c.official_records
                    ],
                    "parameter_names": sorted(
                        {
                            p.name
                            for c in cards
                            for s in c.signatures
                            for p in s.parameters
                        }
                    ),
                    "sources": sorted(
                        {
                            r.source.source_path
                            for c in cards
                            for r in c.official_records
                        }
                    ),
                }
            )
    for name in scope["members"]:
        result = catalog.lookup(name)
        cards = result.members
        expected = scope["known_member_types"].get(name)
        rows.append(
            {
                "query": name,
                "kind": "member",
                "status": result.status,
                "annotation": cards[0].annotation if cards else None,
                "passed": result.status == "found"
                and len(cards) == 1
                and bool(cards[0].annotation)
                and bool(cards[0].description)
                and (expected is None or cards[0].annotation == expected),
                "source": cards[0].source.model_dump(mode="json") if cards else None,
            }
        )
    for name, expected in scope["factory_returns"].items():
        result = catalog.lookup(name)
        actual = [
            r.return_description for c in result.cards for r in c.official_records
        ]
        rows.append(
            {
                "query": name,
                "kind": "factory_return",
                "expected": expected,
                "actual": actual,
                "passed": any(expected in value for value in actual),
            }
        )
    for name, expected in scope["negative_queries"].items():
        actual = catalog.lookup(name).status
        rows.append(
            {
                "query": name,
                "kind": "negative_or_ambiguous",
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--scope",
        type=Path,
        default=Path(__file__).with_name("modeling_core_queries_v1.json"),
    )
    parser.add_argument(
        "--legacy-benchmark",
        type=Path,
        default=Path(__file__).with_name("abaqus_api_queries.jsonl"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("audit output must be new")
    base = ApiCatalog.open(args.base)
    candidate = ApiCatalog.open(args.candidate)
    scope = json.loads(args.scope.read_bytes())
    before = inspect_core(base, scope)
    after = inspect_core(candidate, scope)
    baseline_cases = load_api_catalog_cases(args.legacy_benchmark)
    legacy = evaluate_api_catalog(candidate, baseline_cases)
    # Preserve the old benchmark. Correct only its C++-derived provenance assertion.
    revised = tuple(
        case.model_copy(update={"expected_declaring_name": "Odb commands.openOdb"})
        if case.query == "openOdb"
        and case.expected_declaring_name == "Top level commands.openOdb"
        else case
        for case in baseline_cases
    )
    adjusted = evaluate_api_catalog(candidate, revised)
    old_cards = {c.card_id: c for c in base.cards}
    new_cards = {c.card_id: c for c in candidate.cards}
    old_official = {r.record_id: r for c in base.cards for r in c.official_records}
    new_official = {r.record_id: r for c in candidate.cards for r in c.official_records}
    removed_official = sorted(old_official.keys() - new_official.keys())
    old_stubs = {r.record_id for c in base.cards for r in c.abqpy_records}
    new_stubs = {r.record_id for c in candidate.cards for r in c.abqpy_records}
    old_notes = {n.note_id: n for c in base.cards for n in c.runtime_notes}
    new_notes = {n.note_id: n for c in candidate.cards for n in c.runtime_notes}
    changes = [
        {
            "card_id": key,
            "before_name": old_cards[key].canonical_name,
            "after_name": new_cards[key].canonical_name,
            "changed_fields": [
                f
                for f in type(old_cards[key]).model_fields
                if getattr(old_cards[key], f) != getattr(new_cards[key], f)
            ],
        }
        for key in sorted(old_cards.keys() & new_cards.keys())
        if old_cards[key] != new_cards[key]
    ]
    invariants = {
        "no_abqpy_record_lost": old_stubs <= new_stubs,
        "runtime_notes_preserved_exactly": old_notes == new_notes,
        "removed_official_records_are_cpp": all(
            old_official[k].source.source_path.endswith("cpp.htm")
            for k in removed_official
        ),
        "candidate_has_no_cpp_official_records": not any(
            r.source.source_path.endswith("cpp.htm") for r in new_official.values()
        ),
        "core_passed": all(r["passed"] for r in after),
        "adjusted_legacy_passed": adjusted.pass_count == adjusted.total,
    }
    report = {
        "scope": scope,
        "scope_sha256": digest(args.scope),
        "base_manifest": base.manifest.model_dump(mode="json"),
        "candidate_manifest": candidate.manifest.model_dump(mode="json"),
        "base_manifest_sha256": digest(args.base / "manifest.json"),
        "candidate_manifest_sha256": digest(args.candidate / "manifest.json"),
        "core": {
            "total": len(after),
            "before_pass": sum(r["passed"] for r in before),
            "after_pass": sum(r["passed"] for r in after),
            "before": before,
            "after": after,
        },
        "legacy_benchmark_sha256": digest(args.legacy_benchmark),
        "legacy_unmodified_evaluation": legacy.model_dump(mode="json"),
        "adjusted_legacy_evaluation": adjusted.model_dump(mode="json"),
        "benchmark_adjustment": "Only openOdb declaring-name provenance: use its Python Odb commands page rather than the removed C++ Top level commands page; no query, note or semantic gate relaxed",
        "diff": {
            "methods_before": len(base.cards),
            "methods_after": len(candidate.cards),
            "members_before": len(base.members),
            "members_after": len(candidate.members),
            "changed_cards": changes,
            "removed_card_ids": sorted(old_cards.keys() - new_cards.keys()),
            "added_card_ids": sorted(new_cards.keys() - old_cards.keys()),
            "removed_official_records": removed_official,
            "lost_abqpy_records": sorted(old_stubs - new_stubs),
            "runtime_note_count": len(new_notes),
        },
        "invariants": invariants,
        "passed": all(invariants.values()),
        "model_calls": 0,
        "abaqus_calls": 0,
        "scope_source_sha256": {p: digest(Path(p)) for p in scope["scope_sources"]},
        "implementation_sha256": {
            str(p): digest(p)
            for p in Path("src/abaqus_sim_agent/api_catalog").glob("*.py")
        },
    }
    write_text_atomic(args.output, json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "core_total": len(after),
                "before": report["core"]["before_pass"],
                "after": report["core"]["after_pass"],
                "legacy": legacy.pass_count,
                "adjusted_legacy": adjusted.pass_count,
                "invariants": invariants,
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
