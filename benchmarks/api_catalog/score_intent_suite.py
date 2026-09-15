"""收集真实输出并在人工/主助手审查后评分；不将ready当语义正确。"""

import argparse
import hashlib
import json
from pathlib import Path

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.api_catalog.discovery import CatalogDiscovery
from abaqus_sim_agent.api_catalog.e5 import E5Encoder
from abaqus_sim_agent.api_catalog.intent import DiscoveryIntent

PROJECT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def collect(root, phase):
    seal = json.loads((root / "seal.json").read_bytes())
    rows = []
    for case in seal[phase]:
        folder = root / phase / case["id"]
        attempt = (
            json.loads((folder / "attempt.json").read_bytes())
            if (folder / "attempt.json").exists()
            else {}
        )
        outcome_path = folder / "state/outcome.json"
        outcome = json.loads(outcome_path.read_bytes()) if outcome_path.exists() else {}
        file = (
            Path(attempt["workspace"]) / "operations.json"
            if attempt
            else folder / "missing"
        )
        errors = []
        output = None
        if file.exists():
            try:
                values = json.loads(file.read_bytes())
                assert isinstance(values, list)
                assert len(values) == 1
                output = values[0]
                assert set(output) == {"id", "action", "operation", "clarification"}
                assert output["id"] == case["id"]
                if output["action"] == "search":
                    assert isinstance(output["operation"], str)
                    assert output["operation"].strip()
                    assert output["clarification"] is None
                else:
                    assert output["action"] == "clarify"
                    assert output["operation"] is None
                    assert isinstance(output["clarification"], str)
                    assert output["clarification"].strip()
            except (ValueError, AssertionError, TypeError, KeyError) as exc:
                errors.append(type(exc).__name__)
        else:
            errors.append("missing_output")
        if attempt:
            seed = json.loads((Path(attempt["workspace"]) / "cases.json").read_bytes())
            assert seed == [{"id": case["id"], "question": case["question"]}]
        rows.append(
            {
                "case": case,
                "output": output,
                "output_sha256": sha(file) if file.exists() else None,
                "status": outcome.get("status", "not_completed"),
                "failure_stage": outcome.get("failure_stage"),
                "usage": outcome.get("usage", {}),
                "structural_errors": errors,
            }
        )
    write_new(root / (phase + "-outputs.json"), rows)
    for row in rows:
        print(
            json.dumps(
                {
                    "id": row["case"]["id"],
                    "status": row["status"],
                    "output": row["output"],
                    "errors": row["structural_errors"],
                },
                ensure_ascii=False,
            )
        )


def score(root, phase):
    rows = json.loads((root / (phase + "-outputs.json")).read_bytes())
    review_path = root / (phase + "-review.json")
    review = json.loads(review_path.read_bytes())
    assert review["outputs_sha256"] == sha(root / (phase + "-outputs.json"))
    assert set(review["cases"]) == {r["case"]["id"] for r in rows}
    catalog = ApiCatalog.open(
        PROJECT / "var/api-catalog/abaqus-2024-reference-context-v2"
    )
    index_path = PROJECT / "var/api-catalog/discovery-reference-context-v2-e5"
    legacy = CatalogDiscovery.open(catalog, index_path)
    lexical = CatalogDiscovery.open(
        catalog, index_path, lexical_profile="english-snowball-v1"
    )
    encoder = E5Encoder(PROJECT / "var/models/multilingual-e5-base-d128750")
    scores = []
    for row in rows:
        case = row["case"]
        output = row["output"]
        grade = review["cases"][case["id"]]
        valid = row["status"] == "completed" and not row["structural_errors"]
        routing = valid and output["action"] == case["action"]
        retrieval = []
        queries = query_forms(case, output if valid else None)
        if queries:
            for form, operation in queries:
                intent = DiscoveryIntent(
                    original_request=case["question"], operation=operation
                )
                for name, index, strategy in [
                    ("bm25_legacy", legacy, "bm25"),
                    ("bm25_snowball", lexical, "bm25"),
                    ("dense", legacy, "dense"),
                ]:
                    result = index.search_intent(
                        intent, strategy=strategy, encoder=encoder
                    )
                    assert result["intent"]["original_request"] == case["question"]
                    rank = next(
                        (
                            i
                            for i, c in enumerate(result["candidates"], 1)
                            if c["name"] in case["expected"]
                        ),
                        None,
                    )
                    for candidate in result["candidates"]:
                        assert (
                            sha_card(index.read(candidate["card_id"]))
                            == candidate["content_sha256"]
                        )
                    retrieval.append(
                        {
                            "form": form,
                            "ranker": name,
                            "rank": rank,
                            "qualified_hit": qualified_hit(
                                form, rank, routing, grade["faithful"]
                            ),
                            "result": result,
                        }
                    )
        scores.append(
            {
                "id": case["id"],
                "expected_action": case["action"],
                "valid": valid,
                "routing_correct": routing,
                "fidelity": grade,
                "retrieval": retrieval,
            }
        )
    search = [r for r in scores if r["expected_action"] == "search"]
    summary = {
        "total": len(scores),
        "valid": sum(r["valid"] for r in scores),
        "routing_correct": sum(r["routing_correct"] for r in scores),
        "faithful": sum(r["valid"] and r["fidelity"]["faithful"] for r in scores),
        "expected_search": len(search),
        "expected_clarify": len(scores) - len(search),
        "qualified_clarify": sum(
            r["valid"] and r["routing_correct"] and r["fidelity"]["faithful"]
            for r in scores
            if r["expected_action"] == "clarify"
        ),
        "retrieval": {},
    }
    for form in ["original", "operation"]:
        for ranker in ["bm25_legacy", "bm25_snowball", "dense"]:
            records = [
                x
                for r in search
                for x in r["retrieval"]
                if x["form"] == form and x["ranker"] == ranker
            ]
            summary["retrieval"][form + ":" + ranker] = {
                "denominator": len(search),
                "scored": len(records),
                "raw_hits": sum(x["rank"] is not None for x in records),
                "qualified_hits": sum(x["qualified_hit"] for x in records),
            }
    write_new(
        root / (phase + "-scores-v2.json"),
        {
            "scorer_version": "independent-source-baseline-v2",
            "scorer_sha256": sha(Path(__file__)),
            "summary": summary,
            "scores": scores,
            "review_sha256": sha(review_path),
            "index_manifest": legacy.manifest.model_dump(mode="json"),
            "lexical_binding": lexical.lexical_binding,
        },
    )
    print(json.dumps(summary, ensure_ascii=False))


def sha_card(card):
    return hashlib.sha256(card.model_dump_json().encode()).hexdigest()


def query_forms(case, output):
    queries = [("original", case["question"])] if case["action"] == "search" else []
    if output is not None and output["action"] == "search":
        queries.append(("operation", output["operation"]))
    return queries


def qualified_hit(form, rank, routing, faithful):
    return rank is not None and (form == "original" or (routing and faithful))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["collect", "score"])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument(
        "--phase", required=True, choices=["regression", "acceptance", "repeat"]
    )
    args = p.parse_args()
    {"collect": collect, "score": score}[args.command](args.root.resolve(), args.phase)
