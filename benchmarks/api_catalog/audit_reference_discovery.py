"""核对v2正文、旧字段不变与固定开发集；复用既有评分，不回填旧报告。"""

import argparse
import json
from collections import Counter
from pathlib import Path

from audit_docstring_discovery import evaluate
from audit_modeling_candidate import inspect_core

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.api_catalog.discovery import (
    PROJECTION_V2,
    CatalogDiscovery,
    make_views,
    sha,
)
from abaqus_sim_agent.api_catalog.e5 import E5Encoder
from abaqus_sim_agent.coding.api_lookup import _api_lookup_payload


def audit(base, catalog, docs_root):
    old = {c.card_id: c.model_dump(mode="json") for c in base.cards}
    new = {c.card_id: c.model_dump(mode="json") for c in catalog.cards}
    contexts, counts, warnings, file_hashes = {}, Counter(), Counter(), {}
    for card in catalog.cards:
        for record in card.official_records:
            assert record.context is not None
            for context in record.context:
                p = docs_root / context.source.source_path
                if p not in file_hashes:
                    file_hashes[p] = sha(p.read_bytes())
                assert file_hashes[p] == context.source_sha256
                assert context.source.document_id == record.source.document_id
                assert context.source.release == record.source.release
                assert "This page discusses:" not in context.text
                key = (
                    context.kind,
                    context.source.source_path,
                    context.source.section_id,
                )
                if key not in contexts:
                    contexts[key] = context
                    counts[context.kind] += 1
                    warnings.update(context.text_warnings)
        for record in new[card.card_id]["official_records"]:
            record.pop("context")
    assert old == new, "existing method fields changed"
    assert base.members == catalog.members
    assert base.manifest.source_digests == catalog.manifest.source_digests
    table = _api_lookup_payload(
        catalog.lookup("Material.Elastic.table"), error_excerpt=None
    )["parameter"]["table_data"][0]
    assert (
        table["text"].index("ISOTROPIC")
        < table["text"].index("Young")
        < table["text"].index("Poisson")
        < table["text"].index("SHEAR")
    )
    section = _api_lookup_payload(
        catalog.lookup("Part.SectionAssignment"), error_excerpt=None
    )["cards"][0]["object_descriptions"][0]
    assert "connector elements only" in section["text"]
    assert (
        "table_data"
        not in _api_lookup_payload(
            catalog.lookup("Material.Elastic.type"), error_excerpt=None
        )["parameter"]
    )
    core = inspect_core(
        catalog,
        json.loads(
            Path("benchmarks/api_catalog/modeling_core_queries_v1.json").read_bytes()
        ),
    )
    assert all(row["passed"] for row in core)
    views = make_views(catalog, PROJECTION_V2)
    return {
        "old_fields_members_sources_unchanged": True,
        "unique_contexts": dict(counts),
        "source_files_verified": len(file_hashes),
        "warnings": dict(warnings),
        "core": core,
        "read_samples": {"table": table, "section": section},
        "shortened_views": sum(v.shortened for v in views),
        "target_views": [
            v.model_dump()
            for v in views
            if v.name
            in {
                "Material.Elastic",
                "Part.SectionAssignment",
                "Assembly.Instance",
                "Model.ConstrainedSketch",
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--encoder-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("choose a new output, do not overwrite prior evidence")
    catalog = ApiCatalog.open(args.catalog_dir)
    base = ApiCatalog.open(args.base)
    report = {
        "catalog": catalog.manifest.model_dump(mode="json"),
        "base": base.manifest.model_dump(mode="json"),
        "content": audit(base, catalog, args.docs_root),
    }
    if args.index_dir:
        index = CatalogDiscovery.open(catalog, args.index_dir)
        encoder = E5Encoder(args.encoder_dir)
        questions = Path("benchmarks/api_catalog/discovery_development_v1.json")
        report["index"] = index.manifest.model_dump(mode="json")
        report["query_sha256"] = sha(questions.read_bytes())
        report["evaluation"] = evaluate(
            index, encoder, json.loads(questions.read_bytes())
        )
    files = [
        Path(__file__),
        Path(__file__).with_name("audit_docstring_discovery.py"),
        Path(__file__).with_name("audit_modeling_candidate.py"),
    ]
    files += [
        Path("src/abaqus_sim_agent/api_catalog") / name
        for name in (
            "models.py",
            "official.py",
            "reference_text.py",
            "discovery.py",
            "e5.py",
            "catalog.py",
        )
    ]
    files.append(Path("src/abaqus_sim_agent/coding/api_lookup.py"))
    report["implementation_sha256"] = {str(p): sha(p.read_bytes()) for p in files}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            report.get("evaluation", {}).get(
                "summary", report["content"]["unique_contexts"]
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
