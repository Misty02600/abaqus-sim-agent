from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic_ai import Agent, RunContext

from abaqus_sim_agent.api_catalog import (
    ApiCatalog,
    ApiLookupResult,
    ApiMemberCard,
    ApiMethodCard,
    ApiRuntimeNote,
)
from abaqus_sim_agent.coding.api_traceback import (
    TracebackApiResolution,
    resolve_odb_runtime_behavior_query,
    resolve_traceback_api_context,
)
from abaqus_sim_agent.coding.contracts import ApiLookupTrace
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.execution.abaqus2024 import (
    AbaqusExecutionResult,
    AbaqusInvocation,
)

ApiCatalogMode = Literal[
    "off",
    "full",
    "automatic_verified_only",
    "automatic_exact_signature",
]

__all__ = [
    "ApiCatalogMode",
    "register_api_lookup_tool",
    "resolve_api_catalog_mode",
    "runtime_api_hint",
]


def resolve_api_catalog_mode(
    *,
    api_catalog_enabled: bool,
    api_catalog_mode: ApiCatalogMode | None,
) -> ApiCatalogMode:
    if api_catalog_mode is not None:
        return api_catalog_mode
    return "full" if api_catalog_enabled else "off"


def register_api_lookup_tool[AgentOutputT](
    agent: Agent[CodingAgentDependencies, AgentOutputT],
) -> None:
    @agent.tool
    async def lookup_abaqus_api(
        ctx: RunContext[CodingAgentDependencies],
        name: str,
        error_excerpt: str | None = None,
    ) -> dict[str, object]:
        """Look up an exact Abaqus Scripting API card.

        Use a qualified method name such as ``Part.seedEdgeByNumber`` or a
        parameter focus such as ``Part.seedEdgeByNumber.edges`` or an indexed
        member such as ``OdbAssembly.nodeSets`` whenever
        possible. This catalog combines official reference semantics,
        abqpy AST signatures, and verified runtime notes. It never supplies
        missing physical values.
        """
        query = name.strip()
        if not query:
            payload: dict[str, object] = {
                "status": "invalid",
                "query": name,
                "error": "A qualified Abaqus API name is required",
            }
            _record_api_lookup(
                ctx.deps,
                source="active",
                query=name,
                payload=payload,
                card_ids=(),
                counted=False,
            )
            return payload
        if not ctx.deps.register_api_lookup_query(query):
            payload = {
                "status": "duplicate",
                "query": query,
                "error": "This exact API query was already attempted",
            }
            _record_api_lookup(
                ctx.deps,
                source="active",
                query=query,
                payload=payload,
                card_ids=(),
                counted=False,
            )
            return payload
        catalog = ctx.deps.api_catalog
        if catalog is None:
            payload = {
                "status": "unavailable",
                "query": query,
                "error": "Abaqus API catalog is not configured",
            }
            _record_api_lookup(
                ctx.deps,
                source="active",
                query=query,
                payload=payload,
                card_ids=(),
                counted=False,
            )
            return payload
        result = catalog.lookup(query)
        card_ids = tuple(card.card_id for card in (*result.cards, *result.members))
        if result.status == "found":
            parameter_name = result.parameter_name
            if parameter_name is not None and ctx.deps.api_parameter_returned(
                card_ids[0],
                parameter_name,
            ):
                payload = _duplicate_api_parameter_payload(
                    query,
                    card_ids[0],
                    parameter_name,
                )
                _record_api_lookup(
                    ctx.deps,
                    source="active",
                    query=query,
                    payload=payload,
                    card_ids=card_ids,
                    counted=False,
                )
                return payload
            if parameter_name is None and ctx.deps.all_api_cards_returned(card_ids):
                payload = _duplicate_api_cards_payload(query, card_ids)
                _record_api_lookup(
                    ctx.deps,
                    source="active",
                    query=query,
                    payload=payload,
                    card_ids=card_ids,
                    counted=False,
                )
                return payload
            exhausted = ctx.deps.reserve_api_lookup()
            if exhausted:
                payload = {
                    "status": "unavailable",
                    "query": query,
                    "error": exhausted,
                }
                _record_api_lookup(
                    ctx.deps,
                    source="active",
                    query=query,
                    payload=payload,
                    card_ids=(),
                    counted=False,
                )
                return payload
            if parameter_name is None:
                ctx.deps.register_returned_api_cards(card_ids)
            else:
                ctx.deps.register_returned_api_parameter(
                    card_ids[0],
                    parameter_name,
                )
        payload = _api_lookup_payload(result, error_excerpt=error_excerpt)
        _record_api_lookup(
            ctx.deps,
            source="active",
            query=query,
            payload=payload,
            card_ids=card_ids,
            counted=result.status == "found",
        )
        return payload


def runtime_api_hint(
    dependencies: CodingAgentDependencies,
    result: AbaqusExecutionResult,
    *,
    mode: ApiCatalogMode = "full",
) -> dict[str, object] | None:
    if mode == "off":
        return None
    if result.timed_out:
        return None
    catalog = dependencies.api_catalog
    if catalog is None:
        return None
    if result.invocation not in {
        AbaqusInvocation.CAE_NOGUI,
        AbaqusInvocation.ABAQUS_PYTHON,
    }:
        return None
    behavior_query = resolve_odb_runtime_behavior_query(result.stderr)
    resolution = (
        None
        if behavior_query is not None
        else resolve_traceback_api_context(
            result.stderr,
            workspace=result.workspace,
            runtime_failed=not result.stage_succeeded,
            input_fingerprints={
                item.path: item.sha256 for item in result.input_fingerprints
            },
        )
    )
    if behavior_query is None and resolution is None:
        return None
    if behavior_query is not None:
        query = behavior_query
    else:
        assert resolution is not None
        query = resolution.qualified_query
    lookup = catalog.lookup(query)
    if lookup.status != "found":
        if (
            resolution is None
            or lookup.status != "not_found"
            or resolution.error_kind != "attribute_error"
        ):
            return None
        return _attribute_error_repair_hint(
            dependencies,
            resolution=resolution,
            catalog=catalog,
            mode=mode,
        )
    if not lookup.cards:
        # 成员声明不自动升级为错误修复规则或 verified runtime note。
        return None
    relevant_parameters = (
        frozenset() if resolution is None else _relevant_error_parameters(resolution)
    )
    compact_payload: dict[str, object] | None = None
    if mode == "automatic_verified_only":
        compact_payload = _compact_verified_runtime_note_payload(
            lookup.cards[0],
            query=query,
            error_excerpt=result.stderr,
            relevant_parameters=relevant_parameters,
        )
        if compact_payload is None:
            return None
    elif mode == "automatic_exact_signature":
        compact_payload = _compact_exact_signature_payload(
            lookup.cards[0],
            query=query,
            error_excerpt=result.stderr,
            relevant_parameters=relevant_parameters,
        )
        if compact_payload is None:
            return None
    if not dependencies.register_api_lookup_query(query):
        payload: dict[str, object] = {
            "status": "duplicate",
            "query": query,
            "error": "This exact API query was already attempted",
        }
        _record_api_lookup(
            dependencies,
            source="automatic",
            query=query,
            payload=payload,
            card_ids=(),
            counted=False,
        )
        return None if _is_automatic_api_catalog_mode(mode) else payload
    card_ids = tuple(card.card_id for card in lookup.cards)
    if dependencies.all_api_cards_returned(card_ids):
        payload = _duplicate_api_cards_payload(query, card_ids)
        _record_api_lookup(
            dependencies,
            source="automatic",
            query=query,
            payload=payload,
            card_ids=card_ids,
            counted=False,
        )
        return None if _is_automatic_api_catalog_mode(mode) else payload
    exhausted = dependencies.reserve_api_lookup()
    if exhausted:
        payload = {
            "status": "unavailable",
            "query": query,
            "error": exhausted,
        }
        _record_api_lookup(
            dependencies,
            source="automatic",
            query=query,
            payload=payload,
            card_ids=(),
            counted=False,
        )
        return None if _is_automatic_api_catalog_mode(mode) else payload
    dependencies.register_returned_api_cards(card_ids)
    payload = (
        compact_payload
        if compact_payload is not None
        else _api_lookup_payload(
            lookup,
            error_excerpt=result.stderr,
            verified_notes_only=True,
            relevant_parameters=relevant_parameters,
        )
    )
    _record_api_lookup(
        dependencies,
        source="automatic",
        query=query,
        payload=payload,
        card_ids=card_ids,
        counted=True,
    )
    return payload


def _api_lookup_payload(
    result: ApiLookupResult,
    *,
    error_excerpt: str | None,
    verified_notes_only: bool = False,
    relevant_parameters: frozenset[str] | None = None,
) -> dict[str, object]:
    if result.parameter_name is not None:
        return {
            "status": result.status,
            "query": result.query,
            "parameter": _api_parameter_payload(
                result.cards[0],
                result.parameter_name,
                error_excerpt=error_excerpt,
                verified_notes_only=verified_notes_only,
            ),
            "suggestions": list(result.suggestions),
        }
    payload: dict[str, object] = {
        "status": result.status,
        "query": result.query,
        "cards": [
            _api_card_payload(
                card,
                error_excerpt=error_excerpt,
                verified_notes_only=verified_notes_only,
                relevant_parameters=relevant_parameters,
            )
            for card in result.cards
        ],
        "suggestions": list(result.suggestions),
    }
    if result.members:
        payload["members"] = [_api_member_payload(member) for member in result.members]
    if result.status == "not_found":
        payload["coverage_note"] = (
            "No exact record in this configured catalog; this does not establish "
            "that the API is absent from Abaqus. Member coverage is opt-in and bounded."
        )
    return payload


def _api_member_payload(member: ApiMemberCard) -> dict[str, object]:
    payload = member.model_dump(mode="json")
    description = member.description
    payload["description"] = description[:1800]
    payload["description_truncated"] = len(description) > 1800
    payload["evidence_limit"] = (
        "Static abqpy annotation and source comment, not licensed runtime verification. "
        "Repository/container annotations are stub types, not Python implementation guarantees. "
        "No unindexed or dynamic members are implied absent."
    )
    return payload


def _api_parameter_payload(
    card: ApiMethodCard,
    parameter_name: str,
    *,
    error_excerpt: str | None,
    verified_notes_only: bool,
) -> dict[str, object]:
    signature_variants: list[dict[str, object]] = []
    seen_signature_variants: set[tuple[object, ...]] = set()
    for signature in card.signatures:
        for parameter in signature.parameters:
            if parameter.name != parameter_name:
                continue
            key = (
                parameter.kind,
                parameter.annotation,
                parameter.default,
                parameter.required,
            )
            if key in seen_signature_variants:
                continue
            seen_signature_variants.add(key)
            signature_variants.append(
                {
                    "kind": parameter.kind,
                    "annotation": parameter.annotation,
                    "default": parameter.default,
                    "required": parameter.required,
                }
            )

    official_descriptions: list[dict[str, object]] = []
    seen_official_descriptions: set[tuple[object, ...]] = set()
    for record in card.official_records:
        for parameter in (
            *record.required_parameters,
            *record.optional_parameters,
        ):
            if parameter.name != parameter_name:
                continue
            key = (
                parameter.description,
                parameter.required,
                record.source.source_url,
                record.source.release,
            )
            if key in seen_official_descriptions:
                continue
            seen_official_descriptions.add(key)
            official_descriptions.append(
                {
                    "description": parameter.description,
                    "required": parameter.required,
                    "source_url": record.source.source_url,
                    "release": record.source.release,
                }
            )

    matching_notes = _matching_runtime_notes(
        card,
        error_excerpt=error_excerpt,
        verified_notes_only=verified_notes_only,
        relevant_parameters=frozenset({parameter_name}),
    )
    return {
        "card_id": card.card_id,
        "canonical_name": card.canonical_name,
        "declaring_name": card.declaring_name,
        "name": parameter_name,
        "signature_variants": signature_variants,
        "abqpy_sources": [
            record.source.model_dump(mode="json") for record in card.abqpy_records
        ],
        "official_descriptions": official_descriptions,
        "runtime_notes": [_runtime_note_payload(note) for note in matching_notes],
        "imports": _api_card_imports(card),
        "release_alignment": card.release_alignment,
    }


def _api_card_payload(
    card: ApiMethodCard,
    *,
    error_excerpt: str | None,
    verified_notes_only: bool,
    relevant_parameters: frozenset[str] | None,
) -> dict[str, object]:
    matching_notes = _matching_runtime_notes(
        card,
        error_excerpt=error_excerpt,
        verified_notes_only=verified_notes_only,
        relevant_parameters=relevant_parameters,
    )
    official = card.official_records[0] if card.official_records else None
    return {
        "card_id": card.card_id,
        "canonical_name": card.canonical_name,
        "declaring_name": card.declaring_name,
        "runtime_notes": [_runtime_note_payload(note) for note in matching_notes],
        "imports": _api_card_imports(card),
        "paths": [path for record in card.official_records for path in record.paths],
        "signatures": [signature.text for signature in card.signatures],
        "abqpy_sources": [
            record.source.model_dump(mode="json") for record in card.abqpy_records
        ],
        "summary": official.summary if official is not None else "",
        "required_arguments": (
            [parameter.name for parameter in official.required_parameters]
            if official is not None
            else []
        ),
        "optional_arguments": (
            [parameter.name for parameter in official.optional_parameters]
            if official is not None
            else []
        ),
        "return_description": (
            official.return_description if official is not None else ""
        ),
        "required_argument_details": [
            {
                "name": parameter.name,
                "description": parameter.description,
            }
            for record in card.official_records
            for parameter in record.required_parameters
        ],
        "official_variants": [
            {
                "canonical_name": record.canonical_name,
                "required_arguments": [
                    parameter.name for parameter in record.required_parameters
                ],
                "optional_arguments": [
                    parameter.name for parameter in record.optional_parameters
                ],
                "source_url": record.source.source_url,
            }
            for record in card.official_records
        ],
        "source_urls": [
            record.source.source_url
            for record in card.official_records
            if record.source.source_url is not None
        ],
        "release_alignment": card.release_alignment,
        "conflicts": [conflict.message for conflict in card.conflicts],
    }


def _matching_runtime_notes(
    card: ApiMethodCard,
    *,
    error_excerpt: str | None,
    verified_notes_only: bool,
    relevant_parameters: frozenset[str] | None,
) -> list[ApiRuntimeNote]:
    return [
        note
        for note in card.runtime_notes
        if (not verified_notes_only or note.verified)
        and (
            relevant_parameters is None
            or not note.applies_to_parameters
            or bool(relevant_parameters.intersection(note.applies_to_parameters))
        )
        and (
            error_excerpt is None
            or note.error_fingerprint is None
            or note.error_fingerprint.casefold() in error_excerpt.casefold()
        )
    ]


def _runtime_note_payload(note: ApiRuntimeNote) -> dict[str, object]:
    return {
        "note_id": note.note_id,
        "summary": note.summary,
        "guidance": note.guidance,
        "verified": note.verified,
        "runtime_release": note.runtime_release,
        "error_fingerprint": note.error_fingerprint,
        "applies_to_parameters": list(note.applies_to_parameters),
        "evidence": {
            "ref": note.evidence_ref,
            "source_path": note.source.source_path,
            "release": note.source.release,
            "provenance_only": True,
        },
    }


def _compact_verified_runtime_note_payload(
    card: ApiMethodCard,
    *,
    query: str,
    error_excerpt: str,
    relevant_parameters: frozenset[str],
) -> dict[str, object] | None:
    notes = [
        note
        for note in card.runtime_notes
        if note.verified
        and (
            not note.applies_to_parameters
            or bool(relevant_parameters.intersection(note.applies_to_parameters))
        )
    ]
    if not notes:
        return None

    folded_error = error_excerpt.casefold()

    def relevance(note: Any) -> int:
        fingerprint = note.error_fingerprint
        if fingerprint is not None and fingerprint.casefold() in folded_error:
            return 0
        if fingerprint is None:
            return 1
        return 2

    notes.sort(key=relevance)
    payload: dict[str, object] = {
        "status": "found",
        "query": query,
        "note_ids": [note.note_id for note in notes],
        "runtime_notes": [
            {
                "note_id": note.note_id,
                "guidance": note.guidance,
                "runtime_release": note.runtime_release,
                "applies_to_parameters": list(note.applies_to_parameters),
                "evidence": {
                    "ref": note.evidence_ref,
                    "release": note.source.release,
                    "provenance_only": True,
                },
            }
            for note in notes
        ],
    }
    if card.signatures:
        payload["signature"] = card.signatures[0].text
    imports = _api_card_imports(card)
    if imports:
        payload["imports"] = imports
    return payload


def _compact_exact_signature_payload(
    card: ApiMethodCard,
    *,
    query: str,
    error_excerpt: str,
    relevant_parameters: frozenset[str],
) -> dict[str, object] | None:
    if not card.signatures:
        return None
    payload: dict[str, object] = {
        "status": "found",
        "query": query,
        "card_id": card.card_id,
        "signature": card.signatures[0].text,
        "guidance": (
            "Use this exact callable signature to remove unsupported arguments. "
            "Preserve user physics inputs and the runtime receiver's ownership."
        ),
    }
    imports = _api_card_imports(card)
    if imports:
        payload["imports"] = imports
    note_payload = _compact_verified_runtime_note_payload(
        card,
        query=query,
        error_excerpt=error_excerpt,
        relevant_parameters=relevant_parameters,
    )
    if note_payload is not None:
        payload["note_ids"] = note_payload["note_ids"]
        payload["runtime_notes"] = note_payload["runtime_notes"]
    return payload


def _is_automatic_api_catalog_mode(mode: ApiCatalogMode) -> bool:
    return mode in {"automatic_verified_only", "automatic_exact_signature"}


def _relevant_error_parameters(
    resolution: TracebackApiResolution,
) -> frozenset[str]:
    if resolution.error_parameter is not None:
        return frozenset((resolution.error_parameter,))
    return frozenset(resolution.call_keywords)


def _attribute_error_repair_hint(
    dependencies: CodingAgentDependencies,
    *,
    resolution: TracebackApiResolution,
    catalog: ApiCatalog,
    mode: ApiCatalogMode,
) -> dict[str, object] | None:
    query = resolution.qualified_query
    if not dependencies.register_api_lookup_query(query):
        payload: dict[str, object] = {
            "status": "duplicate",
            "query": query,
            "error": "This exact API query was already attempted",
        }
        _record_api_lookup(
            dependencies,
            source="automatic",
            query=query,
            payload=payload,
            card_ids=(),
            counted=False,
        )
        return None if _is_automatic_api_catalog_mode(mode) else payload

    owner, _separator, attribute = query.rpartition(".")
    payload = {
        "status": "not_found",
        "query": query,
        "receiver_type": owner,
        "missing_attribute": attribute,
        "alternatives": list(catalog.repair_alternatives(query)),
        "guidance": (
            "The runtime receiver does not expose this method. Choose a legal "
            "Catalog alternative and adapt the owning object; alternatives are "
            "names only, not interchangeable calls."
        ),
    }
    _record_api_lookup(
        dependencies,
        source="automatic",
        query=query,
        payload=payload,
        card_ids=(),
        counted=False,
    )
    return payload


def _api_card_imports(card: ApiMethodCard) -> list[str]:
    access = [item for record in card.official_records for item in record.access]
    return list(
        dict.fromkeys(
            module
            for item in access
            for module in re.findall(r"\bimport\s+([A-Za-z_][A-Za-z0-9_]*)", item)
        )
    )


def _duplicate_api_cards_payload(
    query: str,
    card_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "status": "duplicate",
        "query": query,
        "card_ids": list(card_ids),
        "error": "All matching API cards were already returned",
    }


def _duplicate_api_parameter_payload(
    query: str,
    card_id: str,
    parameter_name: str,
) -> dict[str, object]:
    return {
        "status": "duplicate",
        "query": query,
        "card_id": card_id,
        "parameter_name": parameter_name,
        "error": "This API parameter contract was already returned",
    }


def _record_api_lookup(
    dependencies: CodingAgentDependencies,
    *,
    source: Literal["active", "automatic"],
    query: str,
    payload: dict[str, object],
    card_ids: tuple[str, ...],
    counted: bool,
) -> None:
    payload_bytes = len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    trace = ApiLookupTrace.model_validate(
        {
            "source": source,
            "query": query,
            "status": str(payload.get("status", "unknown")),
            "card_ids": card_ids,
            "delivered_note_ids": _delivered_note_ids(payload),
            "counted": counted,
            "payload_bytes": payload_bytes,
        }
    )
    trace_path = dependencies.request.state_dir / "api_lookups.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(trace.model_dump_json())
        stream.write("\n")


def _delivered_note_ids(payload: dict[str, object]) -> tuple[str, ...]:
    direct_ids = payload.get("note_ids")
    if isinstance(direct_ids, list):
        return tuple(item for item in direct_ids if isinstance(item, str))

    note_ids: list[str] = []
    parameter = payload.get("parameter")
    if isinstance(parameter, dict):
        notes = parameter.get("runtime_notes")
        if isinstance(notes, list):
            note_ids.extend(_note_ids(notes))

    cards = payload.get("cards")
    if not isinstance(cards, list):
        return tuple(dict.fromkeys(note_ids))
    for card in cards:
        if not isinstance(card, dict):
            continue
        notes = card.get("runtime_notes")
        if not isinstance(notes, list):
            continue
        note_ids.extend(_note_ids(notes))
    return tuple(dict.fromkeys(note_ids))


def _note_ids(notes: list[object]) -> list[str]:
    return [
        note_id
        for note in notes
        if isinstance(note, dict)
        if isinstance(note_id := note.get("note_id"), str)
    ]
