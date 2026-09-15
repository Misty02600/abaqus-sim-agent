from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from abaqus_sim_agent.evaluation.behavior_measurement import CaseRubric, Verdict

REPO_ROOT = Path(__file__).parents[3]
DEFAULT_CASES = REPO_ROOT / "benchmarks/coding/foundation_public_mechanics_cases_v1.jsonl"
DEFAULT_RUBRIC = REPO_ROOT / "benchmarks/coding/foundation_public_mechanics_rubric_v1.json"
DEFAULT_SOURCES = REPO_ROOT / "benchmarks/coding/foundation_public_mechanics_sources_v1.json"
DEFAULT_PROTOCOL = REPO_ROOT / "benchmarks/coding/foundation_public_mechanics_protocol_v1.md"
DEFAULT_SEAL = REPO_ROOT / "benchmarks/coding/foundation_public_mechanics_v1.seal.json"

BENCHMARK_VERSION = "foundation-public-mechanics-v1"
PILOT_PUBLIC_CASE_IDS = (
    "uniform-axial-bar",
    "cantilever-tip-load",
    "plate-with-hole-tension",
)
PILOT_DIAGNOSTIC_CASE_IDS = (
    "existing-property-load-edit",
    "stress-result-semantics",
    "missing-user-physical-input",
)

PairOutcome = Literal["win", "tie", "loss", "unpaired_unknown"]
Severity = Literal["S0", "S1", "S2", "S3"]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class MechanicsReference(StrictModel):
    assumptions: tuple[str, ...] = Field(min_length=1)
    equations: tuple[str, ...] = Field(min_length=1)
    values: dict[str, float] = Field(min_length=1)


class PublicMechanicsCase(StrictModel):
    benchmark_version: Literal["foundation-public-mechanics-v1"]
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    goal: str = Field(min_length=1)
    seed_files: dict[str, str]
    source_ids: tuple[str, ...] = Field(min_length=1)
    reference: MechanicsReference
    focus: tuple[
        Literal["formulation", "modeling", "discretization", "postprocess", "verification"],
        ...,
    ] = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_lists(self):
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("duplicate source id")
        if len(set(self.focus)) != len(self.focus):
            raise ValueError("duplicate focus")
        return self


class PublicSource(StrictModel):
    source_id: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str | None = None
    url: str = Field(pattern=r"^https://")
    source_type: Literal["open_textbook", "public_vendor_verification_manual"]
    used_for: tuple[str, ...] = Field(min_length=1)
    boundary: str = Field(min_length=1)


class SourceCatalog(StrictModel):
    schema_version: Literal["foundation-public-mechanics-sources-v1"]
    accessed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    sources: tuple[PublicSource, ...] = Field(min_length=1)


class BenchmarkSeal(StrictModel):
    benchmark_version: Literal["foundation-public-mechanics-v1"]
    sealed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    case_count: int = Field(gt=0)
    sha256: dict[Literal["cases", "rubric", "sources", "protocol"], str]
    scope: str = Field(min_length=1)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rubrics(path: Path) -> tuple[CaseRubric, ...]:
    raw = json.loads(path.read_bytes())
    if raw.get("schema_version") != "foundation-public-mechanics-rubric-v1":
        raise ValueError("unsupported public mechanics rubric")
    return tuple(
        CaseRubric.model_validate_json(json.dumps(item))
        for item in raw.get("cases", [])
    )


def load_benchmark(
    cases_path: Path = DEFAULT_CASES,
    rubric_path: Path = DEFAULT_RUBRIC,
    sources_path: Path = DEFAULT_SOURCES,
    protocol_path: Path = DEFAULT_PROTOCOL,
    seal_path: Path = DEFAULT_SEAL,
) -> tuple[tuple[PublicMechanicsCase, ...], tuple[CaseRubric, ...], SourceCatalog]:
    seal = BenchmarkSeal.model_validate_json(seal_path.read_bytes())
    actual_hashes = {
        "cases": file_digest(cases_path),
        "rubric": file_digest(rubric_path),
        "sources": file_digest(sources_path),
        "protocol": file_digest(protocol_path),
    }
    if actual_hashes != seal.sha256:
        raise ValueError("public mechanics benchmark no longer matches its frozen seal")

    cases = tuple(
        PublicMechanicsCase.model_validate_json(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    rubrics = _read_rubrics(rubric_path)
    sources = SourceCatalog.model_validate_json(sources_path.read_bytes())
    if len(cases) != seal.case_count:
        raise ValueError("public mechanics case count no longer matches its frozen seal")
    case_ids = tuple(case.case_id for case in cases)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate public mechanics case id")
    if {rubric.case_id for rubric in rubrics} != set(case_ids):
        raise ValueError("rubric must cover the exact public mechanics case set")
    source_ids = {source.source_id for source in sources.sources}
    if len(source_ids) != len(sources.sources):
        raise ValueError("duplicate public source id")
    for case in cases:
        if not set(case.source_ids) <= source_ids:
            raise ValueError(f"unknown source for {case.case_id}")
    for source in sources.sources:
        if not set(source.used_for) <= set(case_ids):
            raise ValueError(f"source {source.source_id} references unknown case")
    return cases, rubrics, sources


def paired_outcome(control: Verdict, foundation: Verdict) -> PairOutcome:
    if control not in {"pass", "fail"} or foundation not in {"pass", "fail"}:
        return "unpaired_unknown"
    if control == "fail" and foundation == "pass":
        return "win"
    if control == "pass" and foundation == "fail":
        return "loss"
    return "tie"


def severity_rank(severity: Severity) -> int:
    return {"S0": 0, "S1": 1, "S2": 2, "S3": 3}[severity]
