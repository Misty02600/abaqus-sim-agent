from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import ApiCatalog


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ApiCatalogEvaluationCase(_StrictModel):
    name: str = Field(min_length=1)
    query: str = Field(min_length=1)
    expected_status: Literal["found", "ambiguous", "not_found"] = "found"
    expected_canonical_name: str | None = Field(default=None, min_length=1)
    expected_declaring_name: str | None = Field(default=None, min_length=1)
    expected_parameter_name: str | None = Field(default=None, min_length=1)
    expected_suggestions: tuple[str, ...] = ()
    require_official: bool = False
    require_abqpy: bool = False
    require_runtime_note: bool = False
    require_verified_runtime_note: bool = False
    min_signatures: int = Field(default=0, ge=0)
    required_official_access_terms: tuple[str, ...] = ()
    required_official_parameter_description_terms: dict[
        str,
        tuple[str, ...],
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_status_requirements(self) -> "ApiCatalogEvaluationCase":
        card_requirements = (
            self.expected_canonical_name is not None
            or self.expected_declaring_name is not None
            or self.expected_parameter_name is not None
            or self.require_official
            or self.require_abqpy
            or self.require_runtime_note
            or self.require_verified_runtime_note
            or self.min_signatures > 0
            or self.required_official_access_terms
            or self.required_official_parameter_description_terms
        )
        if self.expected_parameter_name is not None and self.expected_status != "found":
            raise ValueError("parameter requirements need expected_status found")
        if self.expected_status == "not_found" and card_requirements:
            raise ValueError("not_found cases cannot require a returned card")
        if self.expected_status != "not_found" and self.expected_suggestions:
            raise ValueError("only not_found cases can require suggestions")
        if (
            self.expected_status == "ambiguous"
            and self.expected_canonical_name is None
            and card_requirements
        ):
            raise ValueError("ambiguous card requirements need expected_canonical_name")
        if any(not term.strip() for term in self.required_official_access_terms):
            raise ValueError("official access terms must not be empty")
        if any(
            not parameter.strip()
            or not terms
            or any(not term.strip() for term in terms)
            for parameter, terms in (
                self.required_official_parameter_description_terms.items()
            )
        ):
            raise ValueError(
                "official parameter description requirements need non-empty names "
                "and terms"
            )
        return self


class ApiCatalogCaseResult(_StrictModel):
    name: str
    query: str
    passed: bool
    actual_status: Literal["found", "ambiguous", "not_found"]
    actual_parameter_name: str | None = Field(default=None, min_length=1)
    reasons: tuple[str, ...] = ()


class ApiCatalogEvaluation(_StrictModel):
    total: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    results: tuple[ApiCatalogCaseResult, ...]

    @model_validator(mode="after")
    def check_aggregate(self) -> "ApiCatalogEvaluation":
        if self.total != len(self.results):
            raise ValueError("total must match the number of results")
        actual_pass_count = sum(result.passed for result in self.results)
        if self.pass_count != actual_pass_count:
            raise ValueError("pass_count must match passing results")
        expected_rate = actual_pass_count / self.total if self.total else 0.0
        if abs(self.pass_rate - expected_rate) > 1e-12:
            raise ValueError("pass_rate must match pass_count / total")
        return self


def load_api_catalog_cases(path: str | Path) -> tuple[ApiCatalogEvaluationCase, ...]:
    source = Path(path).resolve(strict=True)
    cases: list[ApiCatalogEvaluationCase] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            cases.append(ApiCatalogEvaluationCase.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(
                f"invalid API catalog case at {source}:{line_number}"
            ) from exc
    if not cases:
        raise ValueError("API catalog evaluation set must not be empty")
    names = [case.name for case in cases]
    if len(names) != len(set(names)):
        raise ValueError("API catalog evaluation case names must be unique")
    return tuple(cases)


def evaluate_api_catalog(
    catalog: ApiCatalog,
    cases: tuple[ApiCatalogEvaluationCase, ...],
) -> ApiCatalogEvaluation:
    results: list[ApiCatalogCaseResult] = []
    for case in cases:
        lookup = catalog.lookup(case.query)
        reasons: list[str] = []
        if lookup.status != case.expected_status:
            reasons.append(
                f"expected status {case.expected_status}, got {lookup.status}"
            )
        if lookup.parameter_name != case.expected_parameter_name:
            reasons.append(
                "expected parameter "
                f"{case.expected_parameter_name}, got {lookup.parameter_name}"
            )
        missing_suggestions = tuple(
            suggestion
            for suggestion in case.expected_suggestions
            if suggestion not in lookup.suggestions
        )
        if missing_suggestions:
            reasons.append("missing suggestions: " + ", ".join(missing_suggestions))
        cards = list(lookup.cards)
        selected = None
        if case.expected_canonical_name is not None:
            selected = next(
                (
                    card
                    for card in cards
                    if card.canonical_name == case.expected_canonical_name
                ),
                None,
            )
            if selected is None:
                reasons.append(f"missing canonical card {case.expected_canonical_name}")
        elif len(cards) == 1:
            selected = cards[0]
        if selected is not None:
            if (
                case.expected_declaring_name is not None
                and selected.declaring_name != case.expected_declaring_name
            ):
                reasons.append(
                    "expected declaring name "
                    f"{case.expected_declaring_name}, got {selected.declaring_name}"
                )
            if case.require_official and not selected.official_records:
                reasons.append("official source is missing")
            if case.require_abqpy and not selected.abqpy_records:
                reasons.append("abqpy source is missing")
            scoped_runtime_notes = tuple(
                note
                for note in selected.runtime_notes
                if case.expected_parameter_name is None
                or not note.applies_to_parameters
                or case.expected_parameter_name in note.applies_to_parameters
            )
            if case.require_runtime_note and not scoped_runtime_notes:
                reasons.append("runtime note is missing")
            if case.require_verified_runtime_note and not any(
                note.verified for note in scoped_runtime_notes
            ):
                reasons.append("verified runtime note is missing")
            missing_access_terms = _missing_terms(
                case.required_official_access_terms,
                (
                    access
                    for record in selected.official_records
                    for access in record.access
                ),
            )
            if missing_access_terms:
                reasons.append(
                    "missing official access terms: " + ", ".join(missing_access_terms)
                )
            for (
                parameter_name,
                required_terms,
            ) in case.required_official_parameter_description_terms.items():
                descriptions = (
                    parameter.description
                    for record in selected.official_records
                    for parameter in (
                        *record.required_parameters,
                        *record.optional_parameters,
                    )
                    if parameter.name == parameter_name
                )
                missing_description_terms = _missing_terms(
                    required_terms,
                    descriptions,
                )
                if missing_description_terms:
                    reasons.append(
                        f"missing official {parameter_name} description terms: "
                        + ", ".join(missing_description_terms)
                    )
            if len(selected.signatures) < case.min_signatures:
                reasons.append(
                    f"expected at least {case.min_signatures} signatures, "
                    f"got {len(selected.signatures)}"
                )
        results.append(
            ApiCatalogCaseResult(
                name=case.name,
                query=case.query,
                passed=not reasons,
                actual_status=lookup.status,
                actual_parameter_name=lookup.parameter_name,
                reasons=tuple(reasons),
            )
        )
    pass_count = sum(result.passed for result in results)
    return ApiCatalogEvaluation(
        total=len(results),
        pass_count=pass_count,
        pass_rate=pass_count / len(results) if results else 0.0,
        results=tuple(results),
    )


def _missing_terms(
    required_terms: tuple[str, ...],
    source_values: Iterable[str],
) -> tuple[str, ...]:
    haystack = "\n".join(str(value) for value in source_values).casefold()
    return tuple(term for term in required_terms if term.casefold() not in haystack)
