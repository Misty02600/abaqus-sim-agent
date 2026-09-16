import math
import re
from pathlib import Path

from abaqus_sim_agent.retrieval import (
    AbaqusDocumentIndex,
    AbaqusDocumentLoader,
)
from abaqus_sim_agent.retrieval.evaluation import (
    RetrievalCase,
    evaluate_retrieval,
    load_cases,
)
from abaqus_sim_agent.retrieval.types import Embeddings

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus_docs"
BENCHMARK_PATH = (
    Path(__file__).parent.parent / "benchmarks" / "rag" / "abaqus_2026_retrieval.jsonl"
)
GUIDES = [
    "SIMACAEANLRefMap",
    "SIMACAEKEYRefMap",
    "SIMACAEELMRefMap",
    "SIMACAEOUTRefMap",
]
VOCABULARY = (
    "static",
    "inertia",
    "equilibrium",
    "output",
    "displacement",
    "stress",
    "element",
    "reaction",
)


class FixtureEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        words = re.findall(r"[a-z]+", text.casefold())
        vector = [float(words.count(token)) for token in VOCABULARY]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return [0.0] * (len(VOCABULARY) - 1) + [1.0]
        return [value / norm for value in vector]


def build_fixture_index(index_dir: Path) -> AbaqusDocumentIndex:
    index, _manifest = AbaqusDocumentIndex.build(
        AbaqusDocumentLoader(FIXTURE_ROOT, guide_ids=GUIDES),
        index_dir,
        embeddings=FixtureEmbeddings(),
        collection_name="fixture_docs",
    )
    return index


def test_evaluation_reports_each_retrieval_path(tmp_path: Path) -> None:
    index = build_fixture_index(tmp_path / "index")
    cases = [
        RetrievalCase(
            case_id="concept-static",
            mode="concept",
            query="static inertia equilibrium",
            gold_document_ids=["simaanl-c-static"],
        ),
        RetrievalCase(
            case_id="dense-element",
            mode="dense",
            query="Which quadrilateral should be used?",
            exact_terms=["CPS4R"],
            gold_document_ids=["simaelm-r-2delem"],
            pair_id="element",
        ),
        RetrievalCase(
            case_id="augmented-element",
            mode="augmented",
            query="Which quadrilateral should be used?",
            exact_terms=["CPS4R"],
            gold_document_ids=["simaelm-r-2delem"],
            pair_id="element",
        ),
        RetrievalCase(
            case_id="symbol-output",
            mode="symbol",
            query="RF",
            gold_document_ids=["simaout-c-std-nodalvariables"],
        ),
    ]

    evaluation = evaluate_retrieval(index, cases, top_k=3)

    assert evaluation.concept_cases == 1
    assert evaluation.concept_recall_at_k == 1
    assert evaluation.dense_cases == 1
    assert evaluation.augmented_cases == 1
    assert evaluation.augmented_mrr == 1
    assert evaluation.symbol_cases == 1
    assert evaluation.symbol_accuracy == 1
    assert evaluation.paired_cases == 1
    assert evaluation.paired_recall_delta >= 0


def test_load_cases_reads_jsonl(tmp_path: Path) -> None:
    benchmark = tmp_path / "cases.jsonl"
    benchmark.write_text(
        '{"case_id":"one","mode":"symbol","query":"U",'
        '"gold_document_ids":["simaout-c-std-nodalvariables"]}\n',
        encoding="utf-8",
    )

    cases = load_cases(benchmark)

    assert cases[0].case_id == "one"
    assert cases[0].exact_terms == []


def test_repository_benchmark_is_valid_and_unique() -> None:
    cases = load_cases(BENCHMARK_PATH)

    assert len(cases) == 42
    assert len({case.case_id for case in cases}) == len(cases)
    assert {case.mode for case in cases} == {"concept", "dense", "augmented", "symbol"}
    dense_pairs = {case.pair_id for case in cases if case.mode == "dense"}
    augmented_pairs = {case.pair_id for case in cases if case.mode == "augmented"}
    assert dense_pairs == augmented_pairs
    assert None not in dense_pairs
    assert len(dense_pairs) == 11
