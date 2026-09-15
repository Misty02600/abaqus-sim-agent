"""离线核查说明保真与固定开发问法；不运行生成模型或Abaqus。"""

import argparse
import ast
import json
import time
import zipfile
from collections import Counter
from pathlib import Path

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.api_catalog.discovery import CatalogDiscovery, sha
from abaqus_sim_agent.api_catalog.e5 import E5Encoder


def audit_docs(base, candidate, wheel):
    before = {c.card_id: c.model_dump(mode="json") for c in base.cards}
    after = {c.card_id: c.model_dump(mode="json") for c in candidate.cards}
    statuses, examples, checked, cache = Counter(), [], 0, {}
    with zipfile.ZipFile(wheel) as archive:
        for card in candidate.cards:
            for record in card.abqpy_records:
                path = record.source.source_path
                if path not in cache:
                    data = archive.read(path)
                    nodes = {
                        n.lineno: n
                        for n in ast.walk(ast.parse(data))
                        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                    }
                    cache[path] = (sha(data), nodes)
                digest, nodes = cache[path]
                assert record.documentation is not None
                for doc in record.documentation:
                    node = nodes[doc.source_line]
                    assert digest == doc.source_sha256
                    assert node.end_lineno == doc.source_end_line
                    assert ast.get_docstring(node, clean=False) == doc.raw
                    statuses[doc.parse_status] += 1
                    checked += 1
                    if doc.parse_status == "partial":
                        examples.append(
                            {"api": card.canonical_name, "line": doc.source_line}
                        )
            for record in after[card.card_id]["abqpy_records"]:
                record.pop("documentation")
    assert before == after, "non-documentation method fields changed"
    assert base.members == candidate.members
    scope = json.loads(
        Path("benchmarks/api_catalog/modeling_core_queries_v1.json").read_bytes()
    )
    core = []
    for names in scope["method_groups"].values():
        for name in names:
            docs = [
                d
                for r in candidate.lookup(name).cards[0].abqpy_records
                for d in r.documentation
            ]
            core.append(
                {
                    "api": name,
                    "raw": any(d.raw for d in docs),
                    "summary": any(d.summary for d in docs),
                    "parameter_count": sum(len(d.parameters) for d in docs),
                }
            )
    assert all(row["raw"] and row["summary"] for row in core)
    return {
        "definition_associations_checked": checked,
        "parse_status_counts": dict(statuses),
        "partial_examples": examples,
        "non_documentation_fields_unchanged": True,
        "members_unchanged": True,
        "core": core,
        "wheel_sha256": sha(wheel.read_bytes()),
    }


def evaluate(index, encoder, spec):
    rows = []
    for family in spec["families"]:
        for language in ("zh", "en", "mixed"):
            for strategy in ("bm25", "dense"):
                start = time.perf_counter()
                result = index.search(
                    family[language], strategy=strategy, encoder=encoder
                )
                rank = next(
                    (
                        i
                        for i, c in enumerate(result["candidates"], 1)
                        if c["name"] in family["expected"]
                    ),
                    None,
                )
                for candidate in result["candidates"]:
                    card = index.read(candidate["card_id"])
                    assert (
                        sha(card.model_dump_json().encode())
                        == candidate["content_sha256"]
                    )
                rows.append(
                    {
                        "family": family["id"],
                        "language": language,
                        "strategy": strategy,
                        "rank": rank,
                        "seconds": time.perf_counter() - start,
                        "result": result,
                    }
                )
    exact = []
    for question, status in spec["exact_checks"].items():
        result = index.search(question, strategy="dense")
        assert result["route"] == "exact"
        assert result["status"] == status
        exact.append(result)
    negatives = [
        {
            "query": q,
            "strategy": s,
            "result": index.search(q, strategy=s, encoder=encoder),
        }
        for q in spec["negative_observations"]
        for s in ("bm25", "dense")
    ]
    summary = {}
    for strategy in ("bm25", "dense"):
        summary[strategy] = {}
        for language in ("zh", "en", "mixed", "all"):
            selected = [
                r
                for r in rows
                if r["strategy"] == strategy
                and (language == "all" or r["language"] == language)
            ]
            summary[strategy][language] = {
                "total": len(selected),
                "hit5": sum(r["rank"] is not None for r in selected),
                "mrr5": sum(1 / r["rank"] for r in selected if r["rank"])
                / len(selected),
            }
    critical = {
        family: any(
            r["family"] == family
            and r["language"] == spec["critical_checks"]["language"]
            and r["rank"] is not None
            for r in rows
        )
        for family in spec["critical_checks"]["families"]
    }
    return {
        "summary": summary,
        "critical_checks": critical,
        "rows": rows,
        "exact": exact,
        "negative_observations": negatives,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--encoder-dir", type=Path)
    parser.add_argument(
        "--queries",
        type=Path,
        default=Path("benchmarks/api_catalog/discovery_development_v1.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("audit output already exists; choose a new file")
    catalog = ApiCatalog.open(args.catalog_dir)
    report = {
        "catalog": catalog.manifest.model_dump(mode="json"),
        "docs": audit_docs(ApiCatalog.open(args.base), catalog, args.wheel),
    }
    if args.index_dir:
        index = CatalogDiscovery.open(catalog, args.index_dir)
        encoder = E5Encoder(args.encoder_dir)
        report["index"] = index.manifest.model_dump(mode="json")
        report["shortened_views"] = sum(v.shortened for v in index.views)
        report["query_sha256"] = sha(args.queries.read_bytes())
        report["evaluation"] = evaluate(
            index, encoder, json.loads(args.queries.read_bytes())
        )
    report["driver_sha256"] = sha(Path(__file__).read_bytes())
    report["implementation_sha256"] = {
        str(p): sha(p.read_bytes())
        for p in [
            Path("src/abaqus_sim_agent/api_catalog") / f
            for f in (
                "discovery.py",
                "e5.py",
                "docstrings.py",
                "catalog.py",
                "models.py",
                "abqpy.py",
            )
        ]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            report.get("evaluation", {}).get(
                "summary", report["docs"]["parse_status_counts"]
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
