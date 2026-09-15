from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    AbqpyApiRecord,
    ApiCatalogManifest,
    ApiLookupResult,
    ApiMemberCard,
    ApiMethodCard,
    ApiReleaseAlignment,
    ApiRuntimeNote,
    ApiSourceConflict,
    ApiSourceRef,
    OfficialApiRecord,
)

CARDS_NAME = "methods.jsonl"
MEMBERS_NAME = "members.jsonl"
MANIFEST_NAME = "manifest.json"
_RELEASE_YEAR = re.compile(r"\b(20\d{2})\b")


class ApiCatalog:
    def __init__(
        self,
        cards: Sequence[ApiMethodCard],
        *,
        runtime_release: str,
        manifest: ApiCatalogManifest | None = None,
        members: Sequence[ApiMemberCard] = (),
    ) -> None:
        if not runtime_release.strip():
            raise ValueError("runtime_release must not be empty")
        ordered = tuple(
            sorted(cards, key=lambda card: (card.canonical_name, card.card_id))
        )
        self._members = tuple(
            sorted(members, key=lambda card: (card.canonical_name, card.card_id))
        )
        ids = [card.card_id for card in (*ordered, *self._members)]
        if len(ids) != len(set(ids)):
            raise ValueError("API catalog card ids must be unique")
        self._cards = ordered
        self.runtime_release = runtime_release
        self.manifest = manifest
        self._name_index: dict[str, tuple[ApiMethodCard, ...]] = {}
        self._short_index: dict[str, tuple[ApiMethodCard, ...]] = {}
        names: dict[str, list[ApiMethodCard]] = defaultdict(list)
        short_names: dict[str, list[ApiMethodCard]] = defaultdict(list)
        for card in ordered:
            for name in card.all_names():
                names[name].append(card)
            short_names[card.method_name].append(card)
        self._name_index = {
            name: _unique_cards(values) for name, values in names.items()
        }
        self._short_index = {
            name: _unique_cards(values) for name, values in short_names.items()
        }
        member_names: dict[str, list[ApiMemberCard]] = defaultdict(list)
        for member in self._members:
            for name in (*member.all_names(), member.member_name):
                if member not in member_names[name]:
                    member_names[name].append(member)
        self._member_index = {
            name: tuple(values) for name, values in member_names.items()
        }

    @property
    def cards(self) -> tuple[ApiMethodCard, ...]:
        return self._cards

    @property
    def members(self) -> tuple[ApiMemberCard, ...]:
        return self._members

    @classmethod
    def build(
        cls,
        *,
        runtime_release: str,
        official_records: Iterable[OfficialApiRecord] = (),
        abqpy_records: Iterable[AbqpyApiRecord] = (),
        runtime_notes: Iterable[ApiRuntimeNote] = (),
        members: Sequence[ApiMemberCard] = (),
    ) -> "ApiCatalog":
        cards = _merge_records(
            runtime_release=runtime_release,
            official_records=tuple(official_records),
            abqpy_records=tuple(abqpy_records),
            runtime_notes=tuple(runtime_notes),
        )
        return cls(cards, runtime_release=runtime_release, members=members)

    @classmethod
    def open(cls, directory: str | Path) -> "ApiCatalog":
        root = Path(directory).expanduser().resolve()
        manifest_path = root / MANIFEST_NAME
        cards_path = root / CARDS_NAME
        if not manifest_path.is_file() or not cards_path.is_file():
            raise FileNotFoundError(f"API catalog artifact is incomplete: {root}")
        manifest = ApiCatalogManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        payload = cards_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != manifest.cards_sha256:
            raise ValueError(
                "API catalog methods.jsonl checksum does not match manifest"
            )
        cards: list[ApiMethodCard] = []
        for line_number, raw_line in enumerate(payload.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                cards.append(ApiMethodCard.model_validate_json(raw_line))
            except ValueError as exc:
                raise ValueError(
                    f"invalid API catalog card at {cards_path}:{line_number}"
                ) from exc
        if len(cards) != manifest.card_count:
            raise ValueError("API catalog card count does not match manifest")
        members: tuple[ApiMemberCard, ...] = ()
        if manifest.members_sha256 is not None:
            member_payload = (root / MEMBERS_NAME).read_bytes()
            if hashlib.sha256(member_payload).hexdigest() != manifest.members_sha256:
                raise ValueError(
                    "API catalog members.jsonl checksum does not match manifest"
                )
            members = tuple(
                ApiMemberCard.model_validate_json(line)
                for line in member_payload.splitlines()
                if line.strip()
            )
            if len(members) != manifest.member_count:
                raise ValueError("API catalog member count does not match manifest")
        return cls(
            cards,
            runtime_release=manifest.runtime_release,
            manifest=manifest,
            members=members,
        )

    def write(
        self,
        directory: str | Path,
        *,
        source_digests: dict[str, str] | None = None,
    ) -> ApiCatalogManifest:
        root = Path(directory).expanduser().resolve()
        if root.exists():
            raise FileExistsError(f"API catalog output already exists: {root}")
        root.mkdir(parents=True)
        payload = "".join(f"{card.model_dump_json()}\n" for card in self._cards).encode(
            "utf-8"
        )
        cards_path = root / CARDS_NAME
        cards_path.write_bytes(payload)
        member_payload = "".join(
            f"{card.model_dump_json()}\n" for card in self._members
        ).encode("utf-8")
        if self._members:
            (root / MEMBERS_NAME).write_bytes(member_payload)
        sources = _catalog_sources(self._cards, self._members)
        manifest = ApiCatalogManifest(
            schema_version="2" if self._members else "1",
            built_at=datetime.now(UTC),
            runtime_release=self.runtime_release,
            official_release=_single_source_value(
                sources,
                kind="official",
                field="release",
            ),
            abqpy_version=_single_source_value(
                sources,
                kind="abqpy",
                field="version",
            ),
            card_count=len(self._cards),
            conflict_count=sum(len(card.conflicts) for card in self._cards),
            cards_sha256=hashlib.sha256(payload).hexdigest(),
            member_count=len(self._members),
            members_sha256=hashlib.sha256(member_payload).hexdigest()
            if self._members
            else None,
            source_digests=source_digests or {},
            sources=sources,
        )
        (root / MANIFEST_NAME).write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        self.manifest = manifest
        return manifest

    def lookup(self, name: str, *, limit: int = 3) -> ApiLookupResult:
        query = name.strip()
        if not query:
            raise ValueError("API lookup name must not be empty")
        if limit < 2:
            raise ValueError("API lookup limit must be at least 2")
        if query.endswith(".__doc__"):
            base = query.removesuffix(".__doc__")
            if base.endswith(".__doc__"):
                raise ValueError("nested documentation queries are unsupported")
            result = self.lookup(base, limit=limit)
            if result.members or result.parameter_name is not None:
                raise ValueError(
                    "documentation queries require a method, not a member or parameter"
                )
            return result.model_copy(
                update={"query": query, "documentation_requested": True}
            )
        cards = self._method_cards(query)
        members = self._member_index.get(query, ())
        if cards or members:
            selected = (*cards, *members)[:limit]
            return ApiLookupResult(
                status="found" if len(cards) + len(members) == 1 else "ambiguous",
                query=query,
                cards=tuple(
                    card for card in selected if isinstance(card, ApiMethodCard)
                ),
                members=tuple(
                    card for card in selected if isinstance(card, ApiMemberCard)
                ),
            )

        method_query, separator, parameter_name = query.rpartition(".")
        if separator and method_query and parameter_name:
            method_cards = self._method_cards(method_query)
            matching_cards = tuple(
                card
                for card in method_cards
                if parameter_name in _card_parameter_names(card)
            )
            if matching_cards:
                result = _card_lookup_result(query, matching_cards, limit=limit)
                if result.status == "found":
                    return result.model_copy(update={"parameter_name": parameter_name})
                return result
            parameter_suggestions = _parameter_suggestions(
                method_cards,
                parameter_name,
                limit=limit,
            )
            if parameter_suggestions:
                return ApiLookupResult(
                    status="not_found",
                    query=query,
                    suggestions=parameter_suggestions,
                )

        return ApiLookupResult(
            status="not_found",
            query=query,
            suggestions=self._method_suggestions(query, limit=limit),
        )

    def _method_cards(self, query: str) -> tuple[ApiMethodCard, ...]:
        cards = self._name_index.get(query)
        if cards is None and "." not in query:
            cards = self._short_index.get(query)
        return cards or ()

    def _method_suggestions(self, query: str, *, limit: int) -> tuple[str, ...]:
        folded = query.casefold()
        return tuple(
            sorted(
                {
                    card.canonical_name
                    for card in self._cards
                    if any(
                        folded in candidate.casefold() for candidate in card.all_names()
                    )
                }
            )[:limit]
        )

    def browse_methods(
        self,
        receiver: str,
        *,
        query: str | None = None,
        limit: int = 20,
    ) -> tuple[tuple[str, str], ...]:
        """按公共接收对象列出可调用方法的紧凑目录。

        Args:
            receiver: 模型实际持有或推断出的公共对象/类名，如 ``Part``。
            query: 可选的方法名片段，只用于确定性的名称过滤和排序。
            limit: 最多返回的候选数量。

        Returns:
            ``(public_name, card_id)`` 元组。这里只返回目录项，不返回 Card 正文。

        Note:
            目录依据 Card 的公共名称，因此声明在 mixin 上的方法仍可通过其公共
            receiver 被发现。Abaqus 文档把 ``Assembly.Set`` 这类对象工厂标成
            ``constructor``，但它们仍是 receiver 上的可调用入口，所以目录保留；
            仅排除真正的 ``__init__``，成员属性仍由独立 member 索引负责。
        """
        owner = receiver.strip()
        if not owner:
            raise ValueError("API receiver must not be empty")
        if limit < 1 or limit > 100:
            raise ValueError("API method directory limit must be between 1 and 100")
        folded_query = query.strip().casefold() if query is not None else ""

        candidates: list[tuple[int, str, str, str]] = []
        for card in self._cards:
            if card.method_name == "__init__":
                continue
            public_name = _card_name_for_owner(card, owner)
            if public_name is None:
                continue
            method_folded = card.method_name.casefold()
            if folded_query:
                if method_folded == folded_query:
                    rank = 0
                elif method_folded.startswith(folded_query):
                    rank = 1
                elif folded_query in method_folded:
                    rank = 2
                else:
                    continue
            else:
                rank = 0
            candidates.append(
                (rank, method_folded, public_name, card.card_id)
            )

        candidates.sort()
        return tuple(
            (public_name, card_id)
            for _rank, _method, public_name, card_id in candidates[:limit]
        )

    def repair_alternatives(self, name: str, *, limit: int = 3) -> tuple[str, ...]:
        """为不存在的限定方法名返回少量、确定性的合法候选。

        候选只来自 Catalog 的精确 owner/method 元数据：优先同 owner 且共享
        方法词干的 API，其次是同名方法的合法 owner。这里不做向量检索，也
        不返回方法卡正文，因此适合在 AttributeError 后作为短修复提示。

        Args:
            name: 形如 ``Part.BaseSolidBox`` 的限定查询名。
            limit: 最多返回的候选数量。

        Returns:
            排序稳定且去重后的 Catalog 限定名。
        """
        query = name.strip()
        owner, separator, method = query.rpartition(".")
        if not separator or not owner or not method or limit < 1:
            return ()

        same_owner: list[tuple[int, str]] = []
        same_method: list[tuple[int, str]] = []
        for card in self._cards:
            candidate = _card_name_for_owner(card, owner)
            if candidate is not None and card.method_name != method:
                stem_length = _common_prefix_length(
                    method.casefold(), card.method_name.casefold()
                )
                if stem_length >= 5:
                    same_owner.append((-stem_length, candidate))
            if card.method_name == method:
                candidate_owner = card.canonical_name.rpartition(".")[0]
                if candidate_owner and candidate_owner != owner:
                    same_method.append(
                        (
                            _alternative_owner_rank(owner, candidate_owner),
                            card.canonical_name,
                        )
                    )

        ordered = (
            name
            for _rank, name in (
                *sorted(same_owner, key=lambda item: (item[0], item[1])),
                *sorted(same_method, key=lambda item: (item[0], item[1])),
            )
        )
        return tuple(dict.fromkeys(ordered))[:limit]


def _merge_records(
    *,
    runtime_release: str,
    official_records: tuple[OfficialApiRecord, ...],
    abqpy_records: tuple[AbqpyApiRecord, ...],
    runtime_notes: tuple[ApiRuntimeNote, ...],
) -> tuple[ApiMethodCard, ...]:
    mismatched_notes = sorted(
        note.note_id
        for note in runtime_notes
        if note.runtime_release != runtime_release
    )
    if mismatched_notes:
        raise ValueError(
            "runtime notes do not match catalog runtime_release: "
            + ", ".join(mismatched_notes)
        )
    cards: list[ApiMethodCard] = []
    for record in sorted(abqpy_records, key=lambda item: item.record_id):
        public_names = _unique_strings(record.public_names)
        aliases = _unique_strings(record.aliases)
        canonical_name = public_names[0] if public_names else record.declaring_name
        identity = aliases[0] if aliases else canonical_name
        cards.append(
            ApiMethodCard(
                card_id=f"api:{runtime_release}:{identity}",
                entity_kind=record.entity_kind,
                canonical_name=canonical_name,
                declaring_name=record.declaring_name,
                class_name=record.class_name,
                method_name=record.method_name,
                public_names=public_names,
                aliases=aliases,
                signatures=record.signatures,
                abqpy_records=(record,),
                release_alignment=_release_alignment(
                    runtime_release,
                    (),
                    (record.source,),
                ),
            )
        )

    for official in official_records:
        candidates = _official_merge_candidates(official, cards)
        if candidates:
            for index in candidates:
                cards[index] = _merge_official_card(
                    cards[index],
                    official,
                    runtime_release=runtime_release,
                )
            continue
        existing_index = next(
            (
                index
                for index, card in enumerate(cards)
                if not card.abqpy_records
                and card.canonical_name == official.canonical_name
            ),
            None,
        )
        if existing_index is None:
            cards.append(_official_only_card(official, runtime_release))
        else:
            card = cards[existing_index]
            cards[existing_index] = card.model_copy(
                update={
                    "official_records": (*card.official_records, official),
                    "public_names": _unique_strings(
                        (*card.public_names, *official.public_names)
                    ),
                    "aliases": _unique_strings((*card.aliases, *official.aliases)),
                }
            )

    cards = list(
        _merge_public_receiver_overloads(
            cards,
            runtime_release=runtime_release,
        )
    )
    note_groups: dict[str, list[ApiRuntimeNote]] = defaultdict(list)
    for note in runtime_notes:
        note_groups[note.qualified_name].append(note)
    updated: list[ApiMethodCard] = []
    for card in cards:
        notes = list(card.runtime_notes)
        for name in card.all_names():
            notes.extend(note_groups.get(name, ()))
        updated.append(card.model_copy(update={"runtime_notes": _unique_notes(notes)}))
    return tuple(updated)


def _merge_public_receiver_overloads(
    cards: Sequence[ApiMethodCard],
    *,
    runtime_release: str,
) -> tuple[ApiMethodCard, ...]:
    grouped: dict[str, list[ApiMethodCard]] = defaultdict(list)
    for card in cards:
        grouped[card.canonical_name].append(card)

    merged: list[ApiMethodCard] = []
    for canonical_name, candidates in grouped.items():
        if not _can_merge_public_receiver_overloads(canonical_name, candidates):
            merged.extend(candidates)
            continue
        owner, _separator, method_name = canonical_name.rpartition(".")
        official_records = tuple(
            {
                record.record_id: record
                for card in candidates
                for record in card.official_records
            }.values()
        )
        abqpy_records = tuple(
            {
                record.record_id: record
                for card in candidates
                for record in card.abqpy_records
            }.values()
        )
        signatures = tuple(
            {
                signature.model_dump_json(): signature
                for card in candidates
                for signature in card.signatures
            }.values()
        )
        conflicts = tuple(
            {
                conflict.model_dump_json(): conflict
                for card in candidates
                for conflict in card.conflicts
            }.values()
        )
        runtime_notes = _unique_notes(
            note for card in candidates for note in card.runtime_notes
        )
        merged.append(
            ApiMethodCard(
                card_id=(f"api:{runtime_release}:public-overload:{canonical_name}"),
                entity_kind=candidates[0].entity_kind,
                canonical_name=canonical_name,
                declaring_name=canonical_name,
                class_name=owner,
                method_name=method_name,
                public_names=_unique_strings(
                    (
                        canonical_name,
                        *(name for card in candidates for name in card.public_names),
                    )
                ),
                aliases=_unique_strings(
                    alias for card in candidates for alias in card.aliases
                ),
                signatures=signatures,
                official_records=official_records,
                abqpy_records=abqpy_records,
                runtime_notes=runtime_notes,
                conflicts=conflicts,
                release_alignment=_release_alignment(
                    runtime_release,
                    tuple(record.source for record in official_records),
                    tuple(record.source for record in abqpy_records),
                ),
            )
        )
    return tuple(merged)


def _can_merge_public_receiver_overloads(
    canonical_name: str,
    cards: Sequence[ApiMethodCard],
) -> bool:
    owner, separator, method_name = canonical_name.rpartition(".")
    if len(cards) < 2 or not separator or not owner or method_name == "__init__":
        return False
    if any(
        card.entity_kind != cards[0].entity_kind
        or card.method_name != method_name
        or canonical_name not in card.public_names
        or not card.abqpy_records
        for card in cards
    ):
        return False
    base_sets = [
        {base for record in card.abqpy_records for base in record.bases}
        for card in cards
    ]
    return bool(base_sets) and bool(set.intersection(*base_sets))


def _official_merge_candidates(
    official: OfficialApiRecord,
    cards: Sequence[ApiMethodCard],
) -> list[int]:
    scored: list[tuple[int, int]] = []
    official_names = {
        official.canonical_name,
        *official.public_names,
        *official.aliases,
    }
    for index, card in enumerate(cards):
        if not card.abqpy_records:
            continue
        constructor_init = (
            official.entity_kind == "constructor"
            and official.owner_name == official.callable_name
            and f"{official.owner_name}.__init__" in official_names
            and card.method_name == "__init__"
        )
        if card.method_name != official.callable_name and not constructor_init:
            continue
        card_names = set(card.all_names())
        if official_names & card_names:
            score = 100
        elif card.class_name == official.owner_name:
            score = 90
        elif card.class_name.endswith(official.owner_name):
            score = 80
        else:
            continue
        scored.append((score, index))
    if not scored:
        return []
    best = max(score for score, _index in scored)
    best_indices = [index for score, index in scored if score == best]
    return best_indices


def _merge_official_card(
    card: ApiMethodCard,
    official: OfficialApiRecord,
    *,
    runtime_release: str,
) -> ApiMethodCard:
    official_records = (*card.official_records, official)
    conflicts = (*card.conflicts, *_source_conflicts(official, card.abqpy_records))
    official_names = (
        official.canonical_name,
        *official.public_names,
        *official.aliases,
    )
    card_names = set(card.all_names())
    matched_public_names = _unique_strings(
        name for name in official_names if name in card_names
    )
    canonical_name = (
        matched_public_names[0] if matched_public_names else official.canonical_name
    )
    return card.model_copy(
        update={
            "entity_kind": official.entity_kind,
            "canonical_name": canonical_name,
            "public_names": _unique_strings(
                (*matched_public_names, *card.public_names)
            ),
            "aliases": _unique_strings((*card.aliases, *official.aliases)),
            "official_records": official_records,
            "conflicts": conflicts,
            "release_alignment": _release_alignment(
                runtime_release,
                tuple(record.source for record in official_records),
                tuple(record.source for record in card.abqpy_records),
            ),
        }
    )


def _official_only_card(
    record: OfficialApiRecord,
    runtime_release: str,
) -> ApiMethodCard:
    class_name, _separator, method_name = record.canonical_name.rpartition(".")
    if not class_name:
        class_name = record.owner_name
        method_name = record.callable_name
    return ApiMethodCard(
        card_id=f"api:{runtime_release}:{record.canonical_name}",
        entity_kind=record.entity_kind,
        canonical_name=record.canonical_name,
        declaring_name=f"{record.owner_name}.{record.callable_name}",
        class_name=class_name,
        method_name=method_name,
        public_names=record.public_names,
        aliases=record.aliases,
        official_records=(record,),
        release_alignment=_release_alignment(
            runtime_release,
            (record.source,),
            (),
        ),
    )


def _source_conflicts(
    official: OfficialApiRecord,
    abqpy_records: tuple[AbqpyApiRecord, ...],
) -> tuple[ApiSourceConflict, ...]:
    conflicts: list[ApiSourceConflict] = []
    official_names = {
        parameter.name
        for parameter in (
            *official.required_parameters,
            *official.optional_parameters,
        )
    }
    signature_names = {
        parameter.name
        for record in abqpy_records
        for signature in record.signatures
        for parameter in signature.parameters
    }
    missing = sorted(official_names - signature_names)
    if missing and signature_names:
        conflicts.append(
            ApiSourceConflict(
                field_name="parameters",
                official_value=", ".join(sorted(official_names)),
                abqpy_value=", ".join(sorted(signature_names)),
                message=(
                    "official arguments are absent from every abqpy signature: "
                    f"{', '.join(missing)}"
                ),
            )
        )
    return tuple(conflicts)


def _release_alignment(
    runtime_release: str,
    official_sources: tuple[ApiSourceRef, ...],
    abqpy_sources: tuple[ApiSourceRef, ...],
) -> ApiReleaseAlignment:
    runtime_year = _release_year(runtime_release)
    source_values = tuple(
        source.release for source in (*official_sources, *abqpy_sources)
    )
    source_years = tuple(_release_year(value) for value in source_values)
    if (
        runtime_year is None
        or not source_years
        or any(year is None for year in source_years)
    ):
        return "unknown"
    if any(year != runtime_year for year in source_years):
        return "mismatch"
    return "matched"


def _release_year(value: str) -> str | None:
    match = _RELEASE_YEAR.search(value)
    return match.group(1) if match else None


def _catalog_sources(
    cards: Sequence[ApiMethodCard],
    members: Sequence[ApiMemberCard] = (),
) -> tuple[ApiSourceRef, ...]:
    sources: dict[tuple[str, str, str | None], ApiSourceRef] = {}
    for card in cards:
        records = (*card.official_records, *card.abqpy_records)
        for record in records:
            source = record.source
            _add_catalog_source_summary(sources, source)
        for note in card.runtime_notes:
            _add_catalog_source_summary(sources, note.source)
    for member in members:
        _add_catalog_source_summary(sources, member.source)
    return tuple(
        sorted(
            sources.values(),
            key=lambda source: (
                source.kind,
                source.release,
                source.version or "",
                source.source_path,
            ),
        )
    )


def _add_catalog_source_summary(
    summaries: dict[tuple[str, str, str | None], ApiSourceRef],
    source: ApiSourceRef,
) -> None:
    key = (source.kind, source.release, source.version)
    if key in summaries:
        return
    source_path = {
        "official": "SIMACAEKERRefMap",
        "abqpy": f"abqpy-{source.version or source.release}",
        "runtime": "runtime-notes",
    }[source.kind]
    summaries[key] = ApiSourceRef(
        kind=source.kind,
        release=source.release,
        version=source.version,
        source_path=source_path,
    )


def _single_source_value(
    sources: Sequence[ApiSourceRef],
    *,
    kind: str,
    field: str,
) -> str | None:
    values = {
        value
        for source in sources
        if source.kind == kind
        if (value := getattr(source, field)) is not None
    }
    return next(iter(values)) if len(values) == 1 else None


def _unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _unique_cards(cards: Iterable[ApiMethodCard]) -> tuple[ApiMethodCard, ...]:
    return tuple({card.card_id: card for card in cards}.values())


def _unique_notes(notes: Iterable[ApiRuntimeNote]) -> tuple[ApiRuntimeNote, ...]:
    return tuple({note.note_id: note for note in notes}.values())


def _card_lookup_result(
    query: str,
    cards: tuple[ApiMethodCard, ...],
    *,
    limit: int,
) -> ApiLookupResult:
    selected = cards[:limit]
    if len(cards) == 1:
        return ApiLookupResult(
            status="found",
            query=query,
            cards=selected,
        )
    return ApiLookupResult(
        status="ambiguous",
        query=query,
        cards=selected,
    )


def _card_parameter_names(card: ApiMethodCard) -> tuple[str, ...]:
    return _unique_strings(
        parameter.name
        for parameter in (
            *(
                parameter
                for signature in card.signatures
                for parameter in signature.parameters
            ),
            *(
                parameter
                for record in card.official_records
                for parameter in (
                    *record.required_parameters,
                    *record.optional_parameters,
                )
            ),
        )
    )


def _parameter_suggestions(
    cards: tuple[ApiMethodCard, ...],
    parameter_name: str,
    *,
    limit: int,
) -> tuple[str, ...]:
    folded = parameter_name.casefold()
    return tuple(
        sorted(
            {
                f"{card.canonical_name}.{candidate}"
                for card in cards
                for candidate in _card_parameter_names(card)
                if folded in candidate.casefold()
            }
        )[:limit]
    )


def _card_name_for_owner(card: ApiMethodCard, owner: str) -> str | None:
    return next(
        (
            name
            for name in card.all_names()
            if name.rpartition(".")[0] == owner
            and name.rpartition(".")[2] == card.method_name
        ),
        None,
    )


def _common_prefix_length(left: str, right: str) -> int:
    length = 0
    for left_character, right_character in zip(left, right, strict=False):
        if left_character != right_character:
            break
        length += 1
    return length


def _alternative_owner_rank(query_owner: str, candidate_owner: str) -> int:
    if query_owner.endswith("Instance"):
        if candidate_owner == "Assembly":
            return 0
        if candidate_owner == query_owner.removesuffix("Instance"):
            return 1
    return 2
