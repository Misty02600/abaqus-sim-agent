from pathlib import Path

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.api_catalog import (
    AbaqusScriptingReferenceLoader,
    ApiCatalog,
    load_abqpy_records,
)
from abaqus_sim_agent.api_catalog.evaluation import (
    ApiCatalogEvaluation,
    ApiCatalogEvaluationCase,
    evaluate_api_catalog,
    load_api_catalog_cases,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "api_catalog"


def test_api_catalog_evaluation_reports_field_level_failures(tmp_path: Path) -> None:
    catalog = ApiCatalog.build(
        runtime_release="2024",
        official_records=AbaqusScriptingReferenceLoader(
            FIXTURE_ROOT / "official"
        ).load(),
        abqpy_records=load_abqpy_records(
            FIXTURE_ROOT / "abqpy" / "source",
            version="2024.test",
            release="2024",
        ),
    )
    benchmark = tmp_path / "cases.jsonl"
    benchmark.write_text(
        "\n".join(
            [
                '{"name":"found","query":"Assembly.Set",'
                '"expected_status":"found",'
                '"expected_canonical_name":"Assembly.Set",'
                '"expected_declaring_name":"RegionAssembly.Set",'
                '"require_official":true,"require_abqpy":true,'
                '"require_runtime_note":false,"min_signatures":1,'
                '"required_official_access_terms":["import assembly"],'
                '"required_official_parameter_description_terms":'
                '{"edges":["sequence of Edge"]}}',
                '{"name":"missing-note","query":"Part.seedEdgeByNumber",'
                '"expected_status":"found",'
                '"expected_canonical_name":"Part.seedEdgeByNumber",'
                '"expected_declaring_name":"MeshPart.seedEdgeByNumber",'
                '"require_official":true,"require_abqpy":true,'
                '"require_runtime_note":true,"min_signatures":1}',
                '{"name":"missing-official-semantics",'
                '"query":"Part.seedEdgeByNumber",'
                '"expected_status":"found",'
                '"expected_canonical_name":"Part.seedEdgeByNumber",'
                '"required_official_access_terms":["import nonexistent"],'
                '"required_official_parameter_description_terms":'
                '{"edges":["Vertex objects"]}}',
                '{"name":"parameter","query":"Part.seedEdgeByNumber.edges",'
                '"expected_status":"found",'
                '"expected_canonical_name":"Part.seedEdgeByNumber",'
                '"expected_parameter_name":"edges",'
                '"required_official_parameter_description_terms":'
                '{"edges":["sequence of Edge"]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    evaluation = evaluate_api_catalog(
        catalog,
        load_api_catalog_cases(benchmark),
    )

    assert evaluation.total == 4
    assert evaluation.pass_count == 2
    assert evaluation.pass_rate == pytest.approx(1 / 2)
    assert evaluation.results[0].passed
    assert not evaluation.results[1].passed
    assert evaluation.results[1].reasons == ("runtime note is missing",)
    assert evaluation.results[2].reasons == (
        "missing official access terms: import nonexistent",
        "missing official edges description terms: Vertex objects",
    )
    assert evaluation.results[3].passed
    assert evaluation.results[3].actual_parameter_name == "edges"


def test_evaluation_case_rejects_inapplicable_requirements() -> None:
    with pytest.raises(ValidationError, match="cannot require a returned card"):
        ApiCatalogEvaluationCase(
            name="invalid-not-found",
            query="missing",
            expected_status="not_found",
            require_abqpy=True,
        )
    with pytest.raises(ValidationError, match="need expected_canonical_name"):
        ApiCatalogEvaluationCase(
            name="invalid-ambiguous",
            query="findAt",
            expected_status="ambiguous",
            min_signatures=1,
        )
    with pytest.raises(ValidationError, match="must not be empty"):
        ApiCatalogEvaluationCase(
            name="invalid-access-term",
            query="Part.Set",
            required_official_access_terms=("",),
        )
    with pytest.raises(ValidationError, match="expected_status found"):
        ApiCatalogEvaluationCase(
            name="invalid-parameter-status",
            query="Part.Set.faces",
            expected_status="not_found",
            expected_parameter_name="faces",
        )


def test_evaluation_aggregate_rejects_inconsistent_counts() -> None:
    with pytest.raises(ValidationError, match="total"):
        ApiCatalogEvaluation(
            total=1,
            pass_count=0,
            pass_rate=0.0,
            results=(),
        )
