from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.amendments import CodingRunAmendmentLedger
from abaqus_sim_agent.coding.contracts import (
    ApiLookupTrace,
    CodingRunRequest,
    ConceptLookupTrace,
    DiagnosticRetrievalTrace,
)
from abaqus_sim_agent.execution import AbaqusExecutionResult

SCANNER_VERSION = "retrieval-input-inventory-v1"

ExpectedLane = Literal[
    "concept_html",
    "exact_symbol",
    "bm25_diagnostic",
    "diagnostic_hybrid",
    "api_catalog",
    "runtime_evidence",
    "task_oracle",
    "query_formulation",
    "none",
    "unknown",
]
RuntimeOwner = Literal[
    "concept_candidate",
    "api_catalog",
    "local_code",
    "unknown",
]
ArtifactRole = Literal[
    "request",
    "amendments",
    "concept_lookup",
    "api_lookup",
    "diagnostic_retrieval",
    "events",
    "stderr",
    "stdout",
    "execution_journal",
    "solver_log",
]
SignalKind = Literal[
    "solver_diagnostic",
    "runtime_exception",
    "verification_failure",
    "infrastructure",
    "clarification",
]
EvidenceConfidence = Literal["structured", "legacy_inferred"]
ExclusionReason = Literal[
    "timeout_or_infrastructure",
    "missing_file",
    "clarification",
    "api_exception",
    "local_code",
    "verification_contract",
    "unknown_owner",
]
CoverageSurface = Literal[
    "semantic_goal",
    "exact_symbol_goal",
    "composite_goal",
    "negated_goal",
    "constraint_heavy_goal",
    "runtime_version_qualified_goal",
    "task_revision_goal",
    "cross_language_concept_lookup",
    "concept_lookup",
    "exact_symbol_lookup",
    "multi_document_lookup",
    "api_lookup",
    "diagnostic_advisory",
    "solver_diagnostic",
    "runtime_concept_exception",
    "runtime_api_exception",
    "runtime_local_code_exception",
    "runtime_unknown_exception",
    "verification_failure",
    "infrastructure",
    "clarification",
    "scan_unknown",
]

_UUID = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_QUOTED_ABSOLUTE_PATH = re.compile(r"(?i)([\"'])(?:[A-Z]:[\\/]|\\\\)[^\"']+\1")
_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/][^\r\n,;\"'<>|]+")
_UNC_PATH = re.compile(r"(?i)(?<![A-Za-z0-9_])\\\\[^\r\n,;\"'<>|]+")
_POSIX_PATH = re.compile(r"(?<![\w*])/(?:[^/\s]+/)+[^\s,;:\"']*")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|access[_-]?key(?:_id)?|authorization|"
    r"password|passwd|secret|token|credential|private[_-]?key|"
    r"client[_-]?secret)[A-Z0-9_]*)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SECRET_TOKEN = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{12,}\b|\bAKIA[A-Z0-9]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bAIza[A-Za-z0-9_-]{20,}\b|"
    r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b)"
)
_STANDALONE_NUMBER = re.compile(
    r"(?<![A-Za-z_])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
    r"(?![A-Za-z_])"
)
_WHITESPACE = re.compile(r"\s+")
_NEGATION = re.compile(
    r"(?i)\b(?:no|not|without|exclude|avoid|never|do\s+not|must\s+not)\b|"
    r"不要|不得|不能|禁止|避免|无需|不使用|无须"
)
_CONSTRAINT = re.compile(
    r"(?i)\b(?:no|not|without|exclude|avoid|never|do\s+not|must\s+not|"
    r"only|exactly|preserve|unchanged|required|must)\b|"
    r"不要|不得|不能|禁止|避免|无需|不使用|无须|仅|只|必须|保持|不变"
)
_RUNTIME_VERSION = re.compile(r"(?i)\bAbaqus(?:/\w+)?\s+2024\b")
_COMPOSITE = re.compile(
    r"(?i)\b(?:and|then|while|as\s+well\s+as|along\s+with)\b|"
    r"同时|并且|以及|然后|且|；|;"
)
_EXACT_SYMBOL = re.compile(
    r"(?:(?i:\*[A-Z][A-Z0-9_-]*|"
    r"\b(?:CPS|CPE|CAX|C3D|DC3D|AC3D|B|S|M3D|COH|SC|R3D)\d+[A-Z0-9]*\b)|"
    r"\b(?:U|U[1-3]|UR[1-3]|RF[1-3]|RM[1-3]|SF[1-3]?|SM[1-3]?|"
    r"S|E|PE|PEEQ)\b)"
)
_EXCEPTION = re.compile(
    r"(?i)(?P<type>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception))\s*:\s*"
    r"(?P<message>.+)"
)
_ABAQUS_ERROR = re.compile(r"(?i)\babaqus(?:/\w+)?\s+error\s*:\s*(?P<message>.+)")
_SOLVER_RECORD = re.compile(r"(?i)^\s*\*{2,}\s*(?:error|warning)\s*:")
_INFRASTRUCTURE = re.compile(
    r"(?i)(?:timed?\s*out|timeout|deadline exceeded|gateway|connection (?:reset|"
    r"refused|aborted)|service unavailable|Model(?:HTTP|API)Error|"
    r"could not obtain (?:an? )?license|license (?:checkout|server|manager) "
    r"(?:failed|error|unavailable|down|refused)|no space left|disk full)"
)
_MISSING_FILE = re.compile(
    r"(?i)(?:FileNotFoundError|no such file or directory|cannot find the file|"
    r"could not open (?:file|input)|missing file)"
)
_API_EXCEPTION = re.compile(
    r"(?i)(?:AttributeError|has no attribute|unexpected keyword argument|"
    r"keyword error|takes no keyword arguments?|unknown keyword|"
    r"required positional argument)"
)
_CONCEPT_EXCEPTION = re.compile(
    r"(?i)(?:unknown element type|not (?:available|supported|allowed).{0,80}"
    r"(?:element|procedure|analysis|output variable)|incompatible.{0,80}"
    r"(?:element|section|material)|missing (?:section|property)|"
    r"output variable.{0,80}(?:invalid|unavailable|unknown))"
)
_LOCAL_EXCEPTION = re.compile(
    r"(?i)(?:SyntaxError|IndentationError|NameError|UnboundLocalError|ImportError|"
    r"ModuleNotFoundError|KeyError|IndexError|ValueError|AssertionError|"
    r"ZeroDivisionError|FileNotFoundError|PermissionError)"
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _LegacyRequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

    run_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    workspace: Path
    state_dir: Path


class ArtifactDigest(_FrozenModel):
    path: str = Field(min_length=1)
    role: ArtifactRole
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScanIssue(_FrozenModel):
    path: str = Field(min_length=1)
    line_number: int | None = Field(default=None, ge=1)
    code: Literal[
        "invalid_request_json",
        "request_state_dir_mismatch",
        "invalid_amendment_json",
        "invalid_execution_journal",
        "missing_goal",
        "invalid_jsonl",
        "workspace_outside_run_root",
        "workspace_missing",
        "unsafe_symlink",
        "read_error",
    ]


class GoalRecord(_FrozenModel):
    run_ref: str = Field(min_length=1)
    task_revision: int = Field(ge=0)
    sample: str = Field(min_length=1)
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    non_ascii: bool
    has_negation: bool
    has_constraint: bool
    composite: bool
    runtime_version_qualified: bool
    exact_symbols: tuple[str, ...] = ()


class QueryGroup(_FrozenModel):
    sample: str = Field(min_length=1)
    normalized_signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_sha256: tuple[str, ...] = Field(min_length=1)
    occurrences: int = Field(ge=1)
    counted_occurrences: int = Field(ge=0)
    run_refs: tuple[str, ...] = Field(min_length=1)
    source_paths: tuple[str, ...] = Field(min_length=1)
    trigger_sources: tuple[str, ...] = ()
    trigger_outcomes: tuple[str, ...] = ()
    match_types: tuple[str, ...] = ()
    task_revisions: tuple[int, ...] = ()
    legacy_context_occurrences: int = Field(ge=0)


class SignalFamily(_FrozenModel):
    kind: SignalKind
    owner: RuntimeOwner | None = None
    expected_lane: ExpectedLane
    rag_candidate: bool
    exclusion_reason: ExclusionReason | None = None
    sample: str = Field(min_length=1)
    normalized_signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    exact_signature_sha256: tuple[str, ...] = Field(min_length=1)
    occurrences: int = Field(ge=1)
    run_refs: tuple[str, ...] = Field(min_length=1)
    source_paths: tuple[str, ...] = Field(min_length=1)
    evidence_confidence: tuple[EvidenceConfidence, ...] = Field(min_length=1)


class RunRecord(_FrozenModel):
    run_ref: str = Field(min_length=1)
    request_path: str = Field(min_length=1)
    request_valid: bool
    request_schema: Literal["current", "legacy"]
    execution_journal_present: bool
    task_revisions: int = Field(ge=0)
    goal_signature_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    concept_lookups: int = Field(ge=0)
    api_lookups: int = Field(ge=0)
    diagnostic_retrievals: int = Field(ge=0)
    solver_diagnostics: int = Field(ge=0)
    runtime_exceptions: int = Field(ge=0)
    verification_failures: int = Field(ge=0)
    infrastructure_events: int = Field(ge=0)
    clarifications: int = Field(ge=0)


class OwnerCount(_FrozenModel):
    owner: RuntimeOwner
    occurrences: int = Field(ge=0)
    normalized_families: int = Field(ge=0)


class CoverageMatrixRow(_FrozenModel):
    surface: CoverageSurface
    expected_lane: ExpectedLane
    rag_candidate: bool
    observation_count: int = Field(ge=0)
    unique_signatures: int = Field(ge=0)
    runs_observed: int = Field(ge=0)
    benchmark_case_count: int = Field(ge=0)
    status: Literal["observed", "not_observed", "non_rag_observed"]


class BenchmarkCoverage(_FrozenModel):
    dataset_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(gt=0)
    need_group_count: int = Field(gt=0)
    surface_counts: dict[str, int]


class InventorySummary(_FrozenModel):
    runs: int = Field(ge=0)
    valid_requests: int = Field(ge=0)
    legacy_requests: int = Field(ge=0)
    execution_journal_runs: int = Field(ge=0)
    unique_goals: int = Field(ge=0)
    task_revision_goals: int = Field(ge=0)
    non_ascii_goals: int = Field(ge=0)
    negated_goals: int = Field(ge=0)
    constrained_goals: int = Field(ge=0)
    composite_goals: int = Field(ge=0)
    runtime_version_qualified_goals: int = Field(ge=0)
    concept_query_occurrences: int = Field(ge=0)
    unique_concept_queries: int = Field(ge=0)
    api_lookup_occurrences: int = Field(ge=0)
    unique_api_queries: int = Field(ge=0)
    diagnostic_retrieval_occurrences: int = Field(ge=0)
    unique_diagnostic_retrieval_queries: int = Field(ge=0)
    solver_diagnostic_occurrences: int = Field(ge=0)
    solver_exact_signatures: int = Field(ge=0)
    solver_normalized_families: int = Field(ge=0)
    runtime_exception_occurrences: int = Field(ge=0)
    runtime_exact_signatures: int = Field(ge=0)
    runtime_normalized_families: int = Field(ge=0)
    runtime_owner_counts: tuple[OwnerCount, ...]
    verification_failures: int = Field(ge=0)
    infrastructure_events: int = Field(ge=0)
    clarifications: int = Field(ge=0)
    rag_candidate_occurrences: int = Field(ge=0)
    scan_issues: int = Field(ge=0)


class RetrievalInputInventoryReport(_FrozenModel):
    schema_version: Literal["1"] = "1"
    scanner_version: Literal["retrieval-input-inventory-v1"] = SCANNER_VERSION
    scanner_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signal_occurrence_unit: Literal["unique_normalized_family_per_run"] = (
        "unique_normalized_family_per_run"
    )
    limitations: tuple[str, ...] = (
        "Signal occurrences count each normalized family at most once per run; "
        "they are not raw event frequencies.",
        "Unknown-failure enumeration is complete only for runs with a strict "
        "execution_results.jsonl journal; legacy raw streams remain regex-derived.",
        "Concept trigger attribution is reported only when persisted by the host; "
        "historical traces without context are explicitly counted as legacy.",
        "The scanned run root contains evaluator and development runs, not a "
        "representative production-traffic sample.",
    )
    created_at: str = Field(min_length=1)
    run_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: InventorySummary
    runs: tuple[RunRecord, ...]
    goals: tuple[GoalRecord, ...]
    concept_queries: tuple[QueryGroup, ...]
    api_queries: tuple[QueryGroup, ...]
    diagnostic_retrieval_queries: tuple[QueryGroup, ...]
    solver_diagnostic_families: tuple[SignalFamily, ...]
    runtime_exception_families: tuple[SignalFamily, ...]
    event_families: tuple[SignalFamily, ...]
    benchmarks: tuple[BenchmarkCoverage, ...]
    coverage_matrix: tuple[CoverageMatrixRow, ...]
    artifacts: tuple[ArtifactDigest, ...]
    issues: tuple[ScanIssue, ...]


@dataclass
class _QueryAccumulator:
    sample: str
    occurrences: int = 0
    counted_occurrences: int = 0
    raw_hashes: set[str] = field(default_factory=set)
    run_refs: set[str] = field(default_factory=set)
    source_paths: set[str] = field(default_factory=set)
    trigger_sources: set[str] = field(default_factory=set)
    trigger_outcomes: set[str] = field(default_factory=set)
    match_types: set[str] = field(default_factory=set)
    task_revisions: set[int] = field(default_factory=set)
    legacy_context_occurrences: int = 0


@dataclass
class _SignalAccumulator:
    kind: SignalKind
    owner: RuntimeOwner | None
    expected_lane: ExpectedLane
    rag_candidate: bool
    exclusion_reason: ExclusionReason | None
    sample: str
    occurrences: int = 0
    exact_hashes: set[str] = field(default_factory=set)
    run_refs: set[str] = field(default_factory=set)
    source_paths: set[str] = field(default_factory=set)
    evidence_confidence: set[EvidenceConfidence] = field(default_factory=set)


@dataclass
class _RunCounts:
    concept_lookups: int = 0
    ascii_concept_lookups: int = 0
    exact_symbol_lookups: int = 0
    api_lookups: int = 0
    diagnostic_retrievals: int = 0
    solver_diagnostics: int = 0
    runtime_exceptions: int = 0
    verification_failures: int = 0
    infrastructure_events: int = 0
    clarifications: int = 0


@dataclass
class _InventoryBuilder:
    root: Path
    artifacts: dict[str, ArtifactDigest] = field(default_factory=dict)
    issues: list[ScanIssue] = field(default_factory=list)
    goals: list[GoalRecord] = field(default_factory=list)
    concept_queries: dict[str, _QueryAccumulator] = field(default_factory=dict)
    api_queries: dict[str, _QueryAccumulator] = field(default_factory=dict)
    diagnostic_retrieval_queries: dict[str, _QueryAccumulator] = field(
        default_factory=dict
    )
    solver_signals: dict[str, _SignalAccumulator] = field(default_factory=dict)
    runtime_signals: dict[str, _SignalAccumulator] = field(default_factory=dict)
    event_signals: dict[str, _SignalAccumulator] = field(default_factory=dict)
    run_records: list[RunRecord] = field(default_factory=list)
    exact_symbol_lookup_occurrences: int = 0
    exact_symbol_lookup_signatures: set[str] = field(default_factory=set)
    cross_language_lookup_occurrences: int = 0
    cross_language_query_signatures: set[str] = field(default_factory=set)
    surface_runs: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def artifact(self, path: Path, role: ArtifactRole) -> bytes | None:
        relative = _relative_path(self.root, path)
        if path.is_symlink():
            self.issues.append(ScanIssue(path=relative, code="unsafe_symlink"))
            return None
        try:
            raw = path.read_bytes()
        except OSError:
            self.issues.append(ScanIssue(path=relative, code="read_error"))
            return None
        self.artifacts[relative] = ArtifactDigest(
            path=relative,
            role=role,
            size_bytes=len(raw),
            sha256=_sha256(raw),
        )
        return raw


def build_inventory(
    run_root: Path,
    *,
    benchmark_paths: Sequence[Path] = (),
) -> RetrievalInputInventoryReport:
    """从 CodingRun 证据中构建检索输入面清单，不读取工作区外部路径。"""
    root = run_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("run_root must be a directory")
    builder = _InventoryBuilder(root=root)
    request_paths = tuple(
        path
        for path in sorted(root.rglob("request.json"), key=lambda item: item.as_posix())
        if path.is_file()
    )
    for request_path in request_paths:
        _scan_run(builder, request_path)
    return _build_report(
        builder,
        len(builder.run_records),
        _load_benchmark_coverage(benchmark_paths),
    )


def write_report_create_new(
    report: RetrievalInputInventoryReport,
    output: Path,
) -> None:
    """以原子排他创建方式写报告，已有证据不会被覆盖。"""
    _assert_report_values_safe(report.model_dump(mode="json"))
    expanded = output.expanduser()
    expanded.parent.mkdir(parents=True, exist_ok=True)
    destination = expanded.parent.resolve(strict=True) / expanded.name
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    encoded = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _scan_run(builder: _InventoryBuilder, request_path: Path) -> None:
    request_relative = _relative_path(builder.root, request_path)
    raw_request = builder.artifact(request_path, "request")
    if raw_request is None:
        return
    request_schema: Literal["current", "legacy"]
    try:
        request: CodingRunRequest | _LegacyRequestEnvelope = (
            CodingRunRequest.model_validate_json(raw_request)
        )
        request_schema = "current"
    except ValueError:
        try:
            request = _LegacyRequestEnvelope.model_validate_json(raw_request)
        except ValueError:
            builder.issues.append(
                ScanIssue(path=request_relative, code="invalid_request_json")
            )
            return
        request_schema = "legacy"
    state_dir = request_path.parent
    if request.state_dir.resolve(strict=False) != state_dir.resolve(strict=False):
        builder.issues.append(
            ScanIssue(path=request_relative, code="request_state_dir_mismatch")
        )
        return
    run_dir = state_dir.parent if state_dir.name == "state" else state_dir
    run_ref = _relative_path(builder.root, run_dir)
    counts = _RunCounts()
    goal_record = _goal_record(run_ref, request.goal, task_revision=0)
    goal_records = [goal_record]
    builder.goals.append(goal_record)
    _record_goal_surfaces(builder, goal_record)
    revision_goals = _scan_amendments(
        builder,
        state_dir / "amendments.json",
        run_ref,
        expected_run_id=request.run_id,
    )
    for revision_goal in revision_goals:
        builder.goals.append(revision_goal)
        goal_records.append(revision_goal)
        _record_goal_surfaces(builder, revision_goal)
    _scan_lookup_file(
        builder,
        state_dir / "concept_lookups.jsonl",
        run_ref,
        "concept_lookup",
        builder.concept_queries,
        counts,
        goals_by_revision={goal.task_revision: goal for goal in goal_records},
    )
    _scan_lookup_file(
        builder,
        state_dir / "api_lookups.jsonl",
        run_ref,
        "api_lookup",
        builder.api_queries,
        counts,
    )
    _scan_diagnostic_retrieval_file(
        builder,
        state_dir / "diagnostic_retrievals.jsonl",
        run_ref,
        counts,
    )
    _scan_events(builder, state_dir / "events.jsonl", run_ref, counts)
    execution_journal = state_dir / "execution_results.jsonl"
    _scan_execution_journal(
        builder,
        execution_journal,
        run_ref,
        counts,
    )

    scan_roots = {state_dir.resolve()}
    workspace_path = request.workspace.resolve(strict=False)
    if not workspace_path.is_relative_to(builder.root):
        builder.issues.append(
            ScanIssue(
                path=request_relative,
                code="workspace_outside_run_root",
            )
        )
    elif not workspace_path.is_dir():
        builder.issues.append(
            ScanIssue(path=request_relative, code="workspace_missing")
        )
    else:
        scan_roots.add(workspace_path)

    scanned: set[Path] = set()
    for scan_root in sorted(scan_roots, key=lambda item: item.as_posix()):
        stream_files: tuple[tuple[str, Literal["stderr", "stdout"]], ...] = (
            ("stderr.bin", "stderr"),
            ("stdout.bin", "stdout"),
        )
        for filename, role in stream_files:
            for stream_path in sorted(
                scan_root.rglob(filename), key=lambda item: item.as_posix()
            ):
                resolved = stream_path.resolve()
                if resolved in scanned or not stream_path.is_file():
                    continue
                scanned.add(resolved)
                _scan_runtime_stream(builder, stream_path, run_ref, counts, role=role)
        for suffix in ("*.dat", "*.msg", "*.sta"):
            for log_path in sorted(
                scan_root.rglob(suffix), key=lambda item: item.as_posix()
            ):
                resolved = log_path.resolve()
                if resolved in scanned or not log_path.is_file():
                    continue
                scanned.add(resolved)
                _scan_solver_log(builder, log_path, run_ref, counts)

    counts.solver_diagnostics = _run_family_count(builder.solver_signals, run_ref)
    counts.runtime_exceptions = _run_family_count(builder.runtime_signals, run_ref)
    counts.verification_failures = _run_family_count(
        builder.event_signals,
        run_ref,
        kind="verification_failure",
    )
    counts.infrastructure_events = _run_family_count(
        builder.event_signals,
        run_ref,
        kind="infrastructure",
    )
    counts.clarifications = _run_family_count(
        builder.event_signals,
        run_ref,
        kind="clarification",
    )

    builder.run_records.append(
        RunRecord(
            run_ref=run_ref,
            request_path=request_relative,
            request_valid=True,
            request_schema=request_schema,
            execution_journal_present=execution_journal.is_file(),
            task_revisions=len(revision_goals),
            goal_signature_sha256=(
                goal_record.normalized_signature_sha256
                if goal_record is not None
                else None
            ),
            concept_lookups=counts.concept_lookups,
            api_lookups=counts.api_lookups,
            diagnostic_retrievals=counts.diagnostic_retrievals,
            solver_diagnostics=counts.solver_diagnostics,
            runtime_exceptions=counts.runtime_exceptions,
            verification_failures=counts.verification_failures,
            infrastructure_events=counts.infrastructure_events,
            clarifications=counts.clarifications,
        )
    )


def _scan_amendments(
    builder: _InventoryBuilder,
    path: Path,
    run_ref: str,
    *,
    expected_run_id: str | None,
) -> list[GoalRecord]:
    if not path.is_file():
        return []
    raw = builder.artifact(path, "amendments")
    if raw is None:
        return []
    relative = _relative_path(builder.root, path)
    try:
        ledger = CodingRunAmendmentLedger.model_validate_json(raw)
    except ValueError:
        builder.issues.append(ScanIssue(path=relative, code="invalid_amendment_json"))
        return []
    if expected_run_id is not None and ledger.run_id != expected_run_id:
        builder.issues.append(ScanIssue(path=relative, code="invalid_amendment_json"))
        return []
    records: list[GoalRecord] = []
    for item in ledger.amendments:
        if item.kind != "task_revision":
            continue
        assert item.goal is not None
        records.append(
            _goal_record(run_ref, item.goal, task_revision=item.task_revision)
        )
    return records


def _scan_lookup_file(
    builder: _InventoryBuilder,
    path: Path,
    run_ref: str,
    role: Literal["concept_lookup", "api_lookup"],
    target: dict[str, _QueryAccumulator],
    counts: _RunCounts,
    *,
    goals_by_revision: dict[int, GoalRecord] | None = None,
) -> None:
    if not path.is_file():
        return
    raw = builder.artifact(path, role)
    if raw is None:
        return
    relative = _relative_path(builder.root, path)
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            raw_payload = json.loads(raw_line)
            trace = (
                ConceptLookupTrace.model_validate_json(raw_line)
                if role == "concept_lookup"
                else ApiLookupTrace.model_validate_json(raw_line)
            )
        except (ValueError, json.JSONDecodeError):
            builder.issues.append(
                ScanIssue(
                    path=relative,
                    line_number=line_number,
                    code="invalid_jsonl",
                )
            )
            continue
        payload = trace.model_dump(mode="json")
        query = trace.query
        normalized = _normalized_signature(query, replace_numbers=False)
        signature_hash = _sha256(normalized.encode("utf-8"))
        group = target.setdefault(
            signature_hash,
            _QueryAccumulator(sample=_sanitize_text(query)),
        )
        group.occurrences += 1
        group.counted_occurrences += int(payload.get("counted") is True)
        group.raw_hashes.add(_sha256(query.encode("utf-8")))
        group.run_refs.add(run_ref)
        group.source_paths.add(relative)
        context = payload.get("context")
        has_persisted_context = (
            role == "concept_lookup"
            and isinstance(raw_payload, dict)
            and "context" in raw_payload
        )
        if (
            role == "concept_lookup"
            and has_persisted_context
            and isinstance(context, dict)
        ):
            trigger_source = context.get("trigger_source")
            trigger_outcome = context.get("trigger_outcome")
            if isinstance(trigger_source, str) and trigger_source:
                group.trigger_sources.add(trigger_source)
            if isinstance(trigger_outcome, str) and trigger_outcome:
                group.trigger_outcomes.add(trigger_outcome)
            task_revision = context.get("task_revision")
            if isinstance(task_revision, int):
                group.task_revisions.add(task_revision)
        elif role == "concept_lookup":
            group.legacy_context_occurrences += 1
        ranked_hits = payload.get("ranked_hits")
        if isinstance(ranked_hits, list):
            group.match_types.update(
                str(item["match"])
                for item in ranked_hits
                if isinstance(item, dict) and isinstance(item.get("match"), str)
            )
        if role == "concept_lookup":
            counts.concept_lookups += 1
            counts.ascii_concept_lookups += int(query.isascii())
            builder.surface_runs["concept_lookup"].add(run_ref)
            linked_goal: GoalRecord | None = None
            if goals_by_revision is not None:
                if has_persisted_context and isinstance(context, dict):
                    revision = context.get("task_revision")
                    if isinstance(revision, int):
                        linked_goal = goals_by_revision.get(revision)
                elif len(goals_by_revision) == 1:
                    linked_goal = next(iter(goals_by_revision.values()))
            if linked_goal is not None and linked_goal.non_ascii and query.isascii():
                builder.cross_language_lookup_occurrences += 1
                builder.cross_language_query_signatures.add(signature_hash)
                builder.surface_runs["cross_language_concept_lookup"].add(run_ref)
            exact_terms = payload.get("exact_terms")
            if isinstance(exact_terms, list) and any(
                isinstance(term, str) and term.strip() for term in exact_terms
            ):
                counts.exact_symbol_lookups += 1
                builder.exact_symbol_lookup_occurrences += 1
                builder.exact_symbol_lookup_signatures.add(signature_hash)
                builder.surface_runs["exact_symbol_lookup"].add(run_ref)
        else:
            counts.api_lookups += 1
            builder.surface_runs["api_lookup"].add(run_ref)


def _scan_diagnostic_retrieval_file(
    builder: _InventoryBuilder,
    path: Path,
    run_ref: str,
    counts: _RunCounts,
) -> None:
    if not path.is_file():
        return
    raw = builder.artifact(path, "diagnostic_retrieval")
    if raw is None:
        return
    relative = _relative_path(builder.root, path)
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            trace = DiagnosticRetrievalTrace.model_validate_json(raw_line)
        except ValueError:
            builder.issues.append(
                ScanIssue(
                    path=relative,
                    line_number=line_number,
                    code="invalid_jsonl",
                )
            )
            continue
        normalized = _normalized_signature(trace.query, replace_numbers=False)
        signature_hash = _sha256(normalized.encode("utf-8"))
        group = builder.diagnostic_retrieval_queries.setdefault(
            signature_hash,
            _QueryAccumulator(sample=_sanitize_text(trace.query)),
        )
        group.occurrences += 1
        group.counted_occurrences += int(trace.counted)
        group.raw_hashes.add(trace.query_sha256)
        group.run_refs.add(run_ref)
        group.source_paths.add(relative)
        group.trigger_sources.add("solver")
        group.trigger_outcomes.add(trace.status)
        group.match_types.update(hit.match for hit in trace.ranked_hits)
        group.task_revisions.add(trace.task_revision)
        counts.diagnostic_retrievals += 1
        builder.surface_runs["diagnostic_advisory"].add(run_ref)


def _scan_events(
    builder: _InventoryBuilder,
    path: Path,
    run_ref: str,
    counts: _RunCounts,
) -> None:
    if not path.is_file():
        return
    raw = builder.artifact(path, "events")
    if raw is None:
        return
    relative = _relative_path(builder.root, path)
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            builder.issues.append(
                ScanIssue(
                    path=relative,
                    line_number=line_number,
                    code="invalid_jsonl",
                )
            )
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
            builder.issues.append(
                ScanIssue(
                    path=relative,
                    line_number=line_number,
                    code="invalid_jsonl",
                )
            )
            continue
        event = str(payload["event"])
        data = payload.get("data")
        if (
            event == "verification"
            and isinstance(data, dict)
            and data.get("passed") is False
        ):
            text = _selected_event_text(data, ("failure_stage", "feedback"))
            _add_signal(
                builder.event_signals,
                kind="verification_failure",
                owner=None,
                lane="task_oracle",
                rag_candidate=False,
                exclusion_reason="verification_contract",
                text=text or "verification failed",
                raw=raw_line,
                run_ref=run_ref,
                source_path=relative,
                confidence="structured",
            )
            counts.verification_failures += 1
            builder.surface_runs["verification_failure"].add(run_ref)
        elif event == "clarification":
            text = (
                _selected_event_text(
                    data,
                    ("field_id", "kind", "question", "impact"),
                )
                if isinstance(data, dict)
                else "clarification"
            )
            _add_signal(
                builder.event_signals,
                kind="clarification",
                owner=None,
                lane="none",
                rag_candidate=False,
                exclusion_reason="clarification",
                text=text or "clarification",
                raw=raw_line,
                run_ref=run_ref,
                source_path=relative,
                confidence="structured",
            )
            counts.clarifications += 1
            builder.surface_runs["clarification"].add(run_ref)
        elif event in {
            "infrastructure_error",
            "infrastructure_interrupted",
            "resume_needs_reconcile",
            "resume_workspace_changed",
            "budget_exhaustion_verification_error",
        }:
            text = (
                _selected_event_text(data, ("type", "message", "stage"))
                if isinstance(data, dict)
                else event
            )
            _add_signal(
                builder.event_signals,
                kind="infrastructure",
                owner=None,
                lane="none",
                rag_candidate=False,
                exclusion_reason="timeout_or_infrastructure",
                text=text or event,
                raw=raw_line,
                run_ref=run_ref,
                source_path=relative,
                confidence="structured",
            )
            counts.infrastructure_events += 1
            builder.surface_runs["infrastructure"].add(run_ref)


def _scan_runtime_stream(
    builder: _InventoryBuilder,
    path: Path,
    run_ref: str,
    counts: _RunCounts,
    *,
    role: Literal["stderr", "stdout"],
) -> None:
    raw = builder.artifact(path, role)
    if raw is None:
        return
    relative = _relative_path(builder.root, path)
    for raw_line in raw.splitlines():
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        if _INFRASTRUCTURE.search(text):
            _add_signal(
                builder.event_signals,
                kind="infrastructure",
                owner=None,
                lane="none",
                rag_candidate=False,
                exclusion_reason="timeout_or_infrastructure",
                text=text,
                raw=raw_line,
                run_ref=run_ref,
                source_path=relative,
            )
            counts.infrastructure_events += 1
            builder.surface_runs["infrastructure"].add(run_ref)
            continue
        match = _EXCEPTION.search(text) or _ABAQUS_ERROR.search(text)
        if match is None:
            continue
        owner, lane, rag_candidate, exclusion = _runtime_owner(text)
        _add_signal(
            builder.runtime_signals,
            kind="runtime_exception",
            owner=owner,
            lane=lane,
            rag_candidate=rag_candidate,
            exclusion_reason=exclusion,
            text=text,
            raw=raw_line,
            run_ref=run_ref,
            source_path=relative,
        )
        counts.runtime_exceptions += 1
        _record_runtime_owner_surface(builder, owner, run_ref)


def _record_runtime_owner_surface(
    builder: _InventoryBuilder,
    owner: RuntimeOwner,
    run_ref: str,
) -> None:
    surface = {
        "concept_candidate": "runtime_concept_exception",
        "api_catalog": "runtime_api_exception",
        "local_code": "runtime_local_code_exception",
        "unknown": "runtime_unknown_exception",
    }[owner]
    builder.surface_runs[surface].add(run_ref)


def _scan_execution_journal(
    builder: _InventoryBuilder,
    path: Path,
    run_ref: str,
    counts: _RunCounts,
) -> None:
    if not path.is_file():
        return
    raw = builder.artifact(path, "execution_journal")
    if raw is None:
        return
    relative = _relative_path(builder.root, path)
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            result = AbaqusExecutionResult.model_validate_json(raw_line)
        except ValueError:
            builder.issues.append(
                ScanIssue(
                    path=relative,
                    line_number=line_number,
                    code="invalid_execution_journal",
                )
            )
            continue
        failure_observed = False
        for diagnostic in result.diagnostics:
            text = diagnostic.message.strip()
            if _SOLVER_RECORD.search(text) is None:
                text = f"***{diagnostic.level.upper()}: {text}"
            _add_signal(
                builder.solver_signals,
                kind="solver_diagnostic",
                owner=None,
                lane="bm25_diagnostic",
                rag_candidate=True,
                exclusion_reason=None,
                text=text,
                raw=diagnostic.model_dump_json().encode("utf-8"),
                run_ref=run_ref,
                source_path=relative,
                confidence="structured",
            )
            counts.solver_diagnostics += 1
            builder.surface_runs["solver_diagnostic"].add(run_ref)
            failure_observed = failure_observed or diagnostic.level == "error"
        if result.stage_succeeded:
            continue
        if result.timed_out:
            _add_signal(
                builder.event_signals,
                kind="infrastructure",
                owner=None,
                lane="none",
                rag_candidate=False,
                exclusion_reason="timeout_or_infrastructure",
                text=f"{result.invocation.value} timed out",
                raw=raw_line,
                run_ref=run_ref,
                source_path=relative,
                confidence="structured",
            )
            counts.infrastructure_events += 1
            builder.surface_runs["infrastructure"].add(run_ref)
            continue
        candidates = [
            line.strip()
            for text in (result.stderr, result.stdout)
            for line in text.splitlines()
            if (_EXCEPTION.search(line) or _ABAQUS_ERROR.search(line))
        ]
        for text in candidates:
            owner, lane, rag_candidate, exclusion = _runtime_owner(text)
            _add_signal(
                builder.runtime_signals,
                kind="runtime_exception",
                owner=owner,
                lane=lane,
                rag_candidate=rag_candidate,
                exclusion_reason=exclusion,
                text=text,
                raw=text.encode("utf-8"),
                run_ref=run_ref,
                source_path=relative,
                confidence="structured",
            )
            counts.runtime_exceptions += 1
            _record_runtime_owner_surface(builder, owner, run_ref)
            failure_observed = True
        if not failure_observed:
            text = (
                next(
                    (
                        line.strip()
                        for value in (result.stderr, result.stdout)
                        for line in value.splitlines()
                        if line.strip()
                    ),
                    "",
                )
                or f"{result.invocation.value} failed without a classified message"
            )
            _add_signal(
                builder.runtime_signals,
                kind="runtime_exception",
                owner="unknown",
                lane="unknown",
                rag_candidate=False,
                exclusion_reason="unknown_owner",
                text=text,
                raw=raw_line,
                run_ref=run_ref,
                source_path=relative,
                confidence="structured",
            )
            counts.runtime_exceptions += 1
            _record_runtime_owner_surface(builder, "unknown", run_ref)


def _scan_solver_log(
    builder: _InventoryBuilder,
    path: Path,
    run_ref: str,
    counts: _RunCounts,
) -> None:
    raw = builder.artifact(path, "solver_log")
    if raw is None:
        return
    relative = _relative_path(builder.root, path)
    for raw_line in raw.splitlines():
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text or _SOLVER_RECORD.search(text) is None:
            continue
        if _INFRASTRUCTURE.search(text) or _MISSING_FILE.search(text):
            _add_signal(
                builder.event_signals,
                kind="infrastructure",
                owner=None,
                lane="none",
                rag_candidate=False,
                exclusion_reason="timeout_or_infrastructure",
                text=text,
                raw=raw_line,
                run_ref=run_ref,
                source_path=relative,
            )
            counts.infrastructure_events += 1
            builder.surface_runs["infrastructure"].add(run_ref)
            continue
        _add_signal(
            builder.solver_signals,
            kind="solver_diagnostic",
            owner=None,
            lane="bm25_diagnostic",
            rag_candidate=True,
            exclusion_reason=None,
            text=text,
            raw=raw_line,
            run_ref=run_ref,
            source_path=relative,
        )
        counts.solver_diagnostics += 1
        builder.surface_runs["solver_diagnostic"].add(run_ref)


def _runtime_owner(
    text: str,
) -> tuple[RuntimeOwner, ExpectedLane, bool, ExclusionReason | None]:
    if _MISSING_FILE.search(text):
        return "local_code", "runtime_evidence", False, "missing_file"
    if _API_EXCEPTION.search(text):
        return "api_catalog", "api_catalog", False, "api_exception"
    if _CONCEPT_EXCEPTION.search(text):
        return "concept_candidate", "concept_html", True, None
    if _LOCAL_EXCEPTION.search(text):
        return "local_code", "runtime_evidence", False, "local_code"
    return "unknown", "unknown", False, "unknown_owner"


def _add_signal(
    target: dict[str, _SignalAccumulator],
    *,
    kind: SignalKind,
    owner: RuntimeOwner | None,
    lane: ExpectedLane,
    rag_candidate: bool,
    exclusion_reason: ExclusionReason | None,
    text: str,
    raw: bytes,
    run_ref: str,
    source_path: str,
    confidence: EvidenceConfidence = "legacy_inferred",
) -> None:
    normalized = _normalized_signature(text, replace_numbers=True)
    signature_hash = _sha256(normalized.encode("utf-8"))
    group_key = f"{kind}:{owner}:{signature_hash}"
    group = target.setdefault(
        group_key,
        _SignalAccumulator(
            kind=kind,
            owner=owner,
            expected_lane=lane,
            rag_candidate=rag_candidate,
            exclusion_reason=exclusion_reason,
            sample=_sanitize_text(text),
        ),
    )
    if run_ref not in group.run_refs:
        group.occurrences += 1
    del raw
    group.exact_hashes.add(_sha256(_exact_signal_signature(text).encode("utf-8")))
    group.run_refs.add(run_ref)
    group.source_paths.add(source_path)
    group.evidence_confidence.add(confidence)


def _run_family_count(
    signals: dict[str, _SignalAccumulator],
    run_ref: str,
    *,
    kind: SignalKind | None = None,
) -> int:
    return sum(
        run_ref in signal.run_refs and (kind is None or signal.kind == kind)
        for signal in signals.values()
    )


def _exact_signal_signature(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def _goal_record(run_ref: str, goal: str, *, task_revision: int) -> GoalRecord:
    sanitized_goal = _sanitize_text(goal)
    normalized = _normalized_signature(sanitized_goal, replace_numbers=False)
    symbols = tuple(
        dict.fromkeys(
            match.group(0).strip() for match in _EXACT_SYMBOL.finditer(sanitized_goal)
        )
    )
    return GoalRecord(
        run_ref=run_ref,
        task_revision=task_revision,
        sample=_sanitize_text(goal),
        raw_sha256=_sha256(goal.encode("utf-8")),
        normalized_signature_sha256=_sha256(normalized.encode("utf-8")),
        non_ascii=not goal.isascii(),
        has_negation=_NEGATION.search(goal) is not None,
        has_constraint=_CONSTRAINT.search(goal) is not None,
        composite=_COMPOSITE.search(goal) is not None,
        runtime_version_qualified=_RUNTIME_VERSION.search(goal) is not None,
        exact_symbols=symbols,
    )


def _record_goal_surfaces(builder: _InventoryBuilder, goal: GoalRecord) -> None:
    builder.surface_runs["semantic_goal"].add(goal.run_ref)
    if goal.exact_symbols:
        builder.surface_runs["exact_symbol_goal"].add(goal.run_ref)
    if goal.composite:
        builder.surface_runs["composite_goal"].add(goal.run_ref)
    if goal.has_negation:
        builder.surface_runs["negated_goal"].add(goal.run_ref)
    if goal.has_constraint:
        builder.surface_runs["constraint_heavy_goal"].add(goal.run_ref)
    if goal.runtime_version_qualified:
        builder.surface_runs["runtime_version_qualified_goal"].add(goal.run_ref)
    if goal.task_revision:
        builder.surface_runs["task_revision_goal"].add(goal.run_ref)


def _build_report(
    builder: _InventoryBuilder,
    runs_discovered: int,
    benchmarks: tuple[BenchmarkCoverage, ...],
) -> RetrievalInputInventoryReport:
    concept_groups = _query_groups(builder.concept_queries)
    api_groups = _query_groups(builder.api_queries)
    diagnostic_groups = _query_groups(builder.diagnostic_retrieval_queries)
    solver_families = _signal_families(builder.solver_signals)
    runtime_families = _signal_families(builder.runtime_signals)
    event_families = _signal_families(builder.event_signals)
    owner_counts = tuple(
        OwnerCount(
            owner=owner,
            occurrences=sum(
                item.occurrences for item in runtime_families if item.owner == owner
            ),
            normalized_families=sum(item.owner == owner for item in runtime_families),
        )
        for owner in (
            "concept_candidate",
            "api_catalog",
            "local_code",
            "unknown",
        )
    )
    coverage = _coverage_matrix(
        builder,
        concept_groups=concept_groups,
        api_groups=api_groups,
        diagnostic_groups=diagnostic_groups,
        solver_families=solver_families,
        runtime_families=runtime_families,
        event_families=event_families,
        benchmarks=benchmarks,
    )
    artifacts = tuple(sorted(builder.artifacts.values(), key=lambda item: item.path))
    root_binding = hashlib.sha256()
    for artifact in artifacts:
        encoded_path = artifact.path.encode("utf-8")
        root_binding.update(len(encoded_path).to_bytes(8, "big"))
        root_binding.update(encoded_path)
        root_binding.update(bytes.fromhex(artifact.sha256))
    verification_failures = sum(
        item.occurrences
        for item in event_families
        if item.kind == "verification_failure"
    )
    infrastructure_events = sum(
        item.occurrences for item in event_families if item.kind == "infrastructure"
    )
    clarifications = sum(
        item.occurrences for item in event_families if item.kind == "clarification"
    )
    rag_candidates = (
        sum(group.occurrences for group in concept_groups)
        + sum(item.occurrences for item in solver_families if item.rag_candidate)
        + sum(item.occurrences for item in runtime_families if item.rag_candidate)
    )
    return RetrievalInputInventoryReport(
        created_at=datetime.now(UTC).isoformat(),
        scanner_sha256=_sha256(Path(__file__).read_bytes()),
        run_root_sha256=root_binding.hexdigest(),
        summary=InventorySummary(
            runs=runs_discovered,
            valid_requests=sum(record.request_valid for record in builder.run_records),
            legacy_requests=sum(
                record.request_schema == "legacy" for record in builder.run_records
            ),
            execution_journal_runs=sum(
                record.execution_journal_present for record in builder.run_records
            ),
            unique_goals=len(
                {goal.normalized_signature_sha256 for goal in builder.goals}
            ),
            task_revision_goals=sum(bool(goal.task_revision) for goal in builder.goals),
            non_ascii_goals=sum(goal.non_ascii for goal in builder.goals),
            negated_goals=sum(goal.has_negation for goal in builder.goals),
            constrained_goals=sum(goal.has_constraint for goal in builder.goals),
            composite_goals=sum(goal.composite for goal in builder.goals),
            runtime_version_qualified_goals=sum(
                goal.runtime_version_qualified for goal in builder.goals
            ),
            concept_query_occurrences=sum(
                group.occurrences for group in concept_groups
            ),
            unique_concept_queries=len(concept_groups),
            api_lookup_occurrences=sum(group.occurrences for group in api_groups),
            unique_api_queries=len(api_groups),
            diagnostic_retrieval_occurrences=sum(
                group.occurrences for group in diagnostic_groups
            ),
            unique_diagnostic_retrieval_queries=len(diagnostic_groups),
            solver_diagnostic_occurrences=sum(
                item.occurrences for item in solver_families
            ),
            solver_exact_signatures=len(
                {
                    exact_hash
                    for item in solver_families
                    for exact_hash in item.exact_signature_sha256
                }
            ),
            solver_normalized_families=len(solver_families),
            runtime_exception_occurrences=sum(
                item.occurrences for item in runtime_families
            ),
            runtime_exact_signatures=len(
                {
                    exact_hash
                    for item in runtime_families
                    for exact_hash in item.exact_signature_sha256
                }
            ),
            runtime_normalized_families=len(runtime_families),
            runtime_owner_counts=owner_counts,
            verification_failures=verification_failures,
            infrastructure_events=infrastructure_events,
            clarifications=clarifications,
            rag_candidate_occurrences=rag_candidates,
            scan_issues=len(builder.issues),
        ),
        runs=tuple(sorted(builder.run_records, key=lambda item: item.run_ref)),
        goals=tuple(
            sorted(
                builder.goals,
                key=lambda item: (item.run_ref, item.normalized_signature_sha256),
            )
        ),
        concept_queries=concept_groups,
        api_queries=api_groups,
        diagnostic_retrieval_queries=diagnostic_groups,
        solver_diagnostic_families=solver_families,
        runtime_exception_families=runtime_families,
        event_families=event_families,
        benchmarks=benchmarks,
        coverage_matrix=coverage,
        artifacts=artifacts,
        issues=tuple(
            sorted(
                builder.issues,
                key=lambda item: (item.path, item.line_number or 0, item.code),
            )
        ),
    )


def _query_groups(source: dict[str, _QueryAccumulator]) -> tuple[QueryGroup, ...]:
    return tuple(
        QueryGroup(
            sample=item.sample,
            normalized_signature_sha256=signature_hash,
            raw_sha256=tuple(sorted(item.raw_hashes)),
            occurrences=item.occurrences,
            counted_occurrences=item.counted_occurrences,
            run_refs=tuple(sorted(item.run_refs)),
            source_paths=tuple(sorted(item.source_paths)),
            trigger_sources=tuple(sorted(item.trigger_sources)),
            trigger_outcomes=tuple(sorted(item.trigger_outcomes)),
            match_types=tuple(sorted(item.match_types)),
            task_revisions=tuple(sorted(item.task_revisions)),
            legacy_context_occurrences=item.legacy_context_occurrences,
        )
        for signature_hash, item in sorted(source.items())
    )


def _signal_families(
    source: dict[str, _SignalAccumulator],
) -> tuple[SignalFamily, ...]:
    families: list[SignalFamily] = []
    for key, item in sorted(source.items()):
        signature_hash = key.rsplit(":", maxsplit=1)[-1]
        families.append(
            SignalFamily(
                kind=item.kind,
                owner=item.owner,
                expected_lane=item.expected_lane,
                rag_candidate=item.rag_candidate,
                exclusion_reason=item.exclusion_reason,
                sample=item.sample,
                normalized_signature_sha256=signature_hash,
                exact_signature_sha256=tuple(sorted(item.exact_hashes)),
                occurrences=item.occurrences,
                run_refs=tuple(sorted(item.run_refs)),
                source_paths=tuple(sorted(item.source_paths)),
                evidence_confidence=tuple(sorted(item.evidence_confidence)),
            )
        )
    return tuple(families)


def _coverage_matrix(
    builder: _InventoryBuilder,
    *,
    concept_groups: tuple[QueryGroup, ...],
    api_groups: tuple[QueryGroup, ...],
    diagnostic_groups: tuple[QueryGroup, ...],
    solver_families: tuple[SignalFamily, ...],
    runtime_families: tuple[SignalFamily, ...],
    event_families: tuple[SignalFamily, ...],
    benchmarks: tuple[BenchmarkCoverage, ...],
) -> tuple[CoverageMatrixRow, ...]:
    goal_sets = {
        "semantic_goal": list(builder.goals),
        "exact_symbol_goal": [goal for goal in builder.goals if goal.exact_symbols],
        "composite_goal": [goal for goal in builder.goals if goal.composite],
        "negated_goal": [goal for goal in builder.goals if goal.has_negation],
        "constraint_heavy_goal": [
            goal for goal in builder.goals if goal.has_constraint
        ],
        "runtime_version_qualified_goal": [
            goal for goal in builder.goals if goal.runtime_version_qualified
        ],
        "task_revision_goal": [goal for goal in builder.goals if goal.task_revision],
    }
    exact_lookup_count = builder.exact_symbol_lookup_occurrences
    benchmark_counts: Counter[str] = Counter()
    for benchmark in benchmarks:
        benchmark_counts.update(benchmark.surface_counts)
    definitions: tuple[tuple[CoverageSurface, ExpectedLane, bool, int, int], ...] = (
        (
            "semantic_goal",
            "query_formulation",
            False,
            len(goal_sets["semantic_goal"]),
            len(
                {
                    item.normalized_signature_sha256
                    for item in goal_sets["semantic_goal"]
                }
            ),
        ),
        (
            "exact_symbol_goal",
            "query_formulation",
            False,
            len(goal_sets["exact_symbol_goal"]),
            len(
                {
                    item.normalized_signature_sha256
                    for item in goal_sets["exact_symbol_goal"]
                }
            ),
        ),
        (
            "composite_goal",
            "query_formulation",
            False,
            len(goal_sets["composite_goal"]),
            len(
                {
                    item.normalized_signature_sha256
                    for item in goal_sets["composite_goal"]
                }
            ),
        ),
        (
            "negated_goal",
            "query_formulation",
            False,
            len(goal_sets["negated_goal"]),
            len(
                {item.normalized_signature_sha256 for item in goal_sets["negated_goal"]}
            ),
        ),
        (
            "cross_language_concept_lookup",
            "query_formulation",
            False,
            builder.cross_language_lookup_occurrences,
            len(builder.cross_language_query_signatures),
        ),
        (
            "constraint_heavy_goal",
            "query_formulation",
            False,
            len(goal_sets["constraint_heavy_goal"]),
            len(
                {
                    item.normalized_signature_sha256
                    for item in goal_sets["constraint_heavy_goal"]
                }
            ),
        ),
        (
            "runtime_version_qualified_goal",
            "query_formulation",
            False,
            len(goal_sets["runtime_version_qualified_goal"]),
            len(
                {
                    item.normalized_signature_sha256
                    for item in goal_sets["runtime_version_qualified_goal"]
                }
            ),
        ),
        (
            "task_revision_goal",
            "query_formulation",
            False,
            len(goal_sets["task_revision_goal"]),
            len(
                {
                    item.normalized_signature_sha256
                    for item in goal_sets["task_revision_goal"]
                }
            ),
        ),
        (
            "concept_lookup",
            "concept_html",
            True,
            sum(item.occurrences for item in concept_groups),
            len(concept_groups),
        ),
        (
            "exact_symbol_lookup",
            "exact_symbol",
            True,
            exact_lookup_count,
            len(builder.exact_symbol_lookup_signatures),
        ),
        (
            "multi_document_lookup",
            "concept_html",
            True,
            0,
            0,
        ),
        (
            "api_lookup",
            "api_catalog",
            False,
            sum(item.occurrences for item in api_groups),
            len(api_groups),
        ),
        (
            "diagnostic_advisory",
            "diagnostic_hybrid",
            True,
            sum(item.occurrences for item in diagnostic_groups),
            len(diagnostic_groups),
        ),
        (
            "solver_diagnostic",
            "bm25_diagnostic",
            True,
            sum(item.occurrences for item in solver_families),
            len(solver_families),
        ),
        (
            "runtime_concept_exception",
            "concept_html",
            True,
            sum(
                item.occurrences
                for item in runtime_families
                if item.owner == "concept_candidate"
            ),
            sum(item.owner == "concept_candidate" for item in runtime_families),
        ),
        (
            "runtime_api_exception",
            "api_catalog",
            False,
            sum(
                item.occurrences
                for item in runtime_families
                if item.owner == "api_catalog"
            ),
            sum(item.owner == "api_catalog" for item in runtime_families),
        ),
        (
            "runtime_local_code_exception",
            "runtime_evidence",
            False,
            sum(
                item.occurrences
                for item in runtime_families
                if item.owner == "local_code"
            ),
            sum(item.owner == "local_code" for item in runtime_families),
        ),
        (
            "runtime_unknown_exception",
            "unknown",
            False,
            sum(
                item.occurrences for item in runtime_families if item.owner == "unknown"
            ),
            sum(item.owner == "unknown" for item in runtime_families),
        ),
        (
            "verification_failure",
            "task_oracle",
            False,
            sum(
                item.occurrences
                for item in event_families
                if item.kind == "verification_failure"
            ),
            sum(item.kind == "verification_failure" for item in event_families),
        ),
        (
            "infrastructure",
            "none",
            False,
            sum(
                item.occurrences
                for item in event_families
                if item.kind == "infrastructure"
            ),
            sum(item.kind == "infrastructure" for item in event_families),
        ),
        (
            "clarification",
            "none",
            False,
            sum(
                item.occurrences
                for item in event_families
                if item.kind == "clarification"
            ),
            sum(item.kind == "clarification" for item in event_families),
        ),
        ("scan_unknown", "unknown", False, len(builder.issues), len(builder.issues)),
    )
    rows: list[CoverageMatrixRow] = []
    for surface, lane, rag_candidate, count, unique in definitions:
        rows.append(
            CoverageMatrixRow(
                surface=surface,
                expected_lane=lane,
                rag_candidate=rag_candidate,
                observation_count=count,
                unique_signatures=unique,
                runs_observed=len(builder.surface_runs.get(surface, set())),
                benchmark_case_count=benchmark_counts[surface],
                status=(
                    "not_observed"
                    if count == 0
                    else "observed"
                    if rag_candidate
                    else "non_rag_observed"
                ),
            )
        )
    return tuple(rows)


def _load_benchmark_coverage(
    paths: Sequence[Path],
) -> tuple[BenchmarkCoverage, ...]:
    reports: list[BenchmarkCoverage] = []
    for raw_path in paths:
        source = raw_path.resolve(strict=True)
        cases: list[dict[str, object]] = []
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid benchmark JSONL at {source}:{line_number}"
                ) from exc
            if not isinstance(item, dict) or not isinstance(item.get("query"), str):
                raise ValueError(
                    f"benchmark case lacks a query at {source}:{line_number}"
                )
            cases.append(item)
        if not cases:
            raise ValueError(f"benchmark is empty: {source}")
        surface_counts: Counter[str] = Counter()
        need_groups: set[str] = set()
        for item in cases:
            group = item.get("need_group_id")
            if isinstance(group, str) and group:
                need_groups.add(group)
                surface_counts["solver_diagnostic"] += 1
                continue
            case_id = item.get("case_id")
            if isinstance(case_id, str):
                need_groups.add(case_id)
            kind = item.get("query_kind")
            stratum = item.get("stratum")
            if kind == "identifier_lookup":
                surface_counts["exact_symbol_lookup"] += 1
            elif kind in {"concept", "identifier_context"}:
                surface_counts["concept_lookup"] += 1
            elif stratum in {"concept", "identifier_context", "multi_document"}:
                surface_counts["concept_lookup"] += 1
            else:
                surface_counts["scan_unknown"] += 1
            if (
                item.get("requires_multiple_documents") is True
                or stratum == "multi_document"
            ):
                surface_counts["multi_document_lookup"] += 1
        dataset_ids = {
            str(item["dataset_id"])
            for item in cases
            if isinstance(item.get("dataset_id"), str)
        }
        reports.append(
            BenchmarkCoverage(
                dataset_id=(
                    next(iter(dataset_ids)) if len(dataset_ids) == 1 else source.stem
                ),
                path=source.name,
                sha256=_sha256(source.read_bytes()),
                case_count=len(cases),
                need_group_count=len(need_groups),
                surface_counts=dict(sorted(surface_counts.items())),
            )
        )
    return tuple(reports)


def _selected_event_text(data: object, keys: tuple[str, ...]) -> str:
    if not isinstance(data, dict):
        return ""
    values = [
        str(data[key]) for key in keys if isinstance(data.get(key), (str, int, float))
    ]
    return " | ".join(values)


def _sanitize_text(value: str) -> str:
    text = _BEARER.sub("Bearer <REDACTED>", value)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<REDACTED>", text)
    text = _SECRET_TOKEN.sub("<REDACTED>", text)
    text = _QUOTED_ABSOLUTE_PATH.sub("<PATH>", text)
    text = _UNC_PATH.sub("<PATH>", text)
    text = _WINDOWS_PATH.sub("<PATH>", text)
    text = _POSIX_PATH.sub("<PATH>", text)
    text = _UUID.sub("<UUID>", text)
    sanitized = _WHITESPACE.sub(" ", text).strip()
    if (
        _SECRET_TOKEN.search(sanitized)
        or _WINDOWS_PATH.search(sanitized)
        or _UNC_PATH.search(sanitized)
    ):
        raise ValueError("sensitive value remained after inventory redaction")
    return sanitized


def _assert_report_values_safe(value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _assert_report_values_safe(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_report_values_safe(item)
        return
    if not isinstance(value, str):
        return
    assignment = _SECRET_ASSIGNMENT.search(value)
    bearer = _BEARER.search(value)
    if (
        _SECRET_TOKEN.search(value)
        or _QUOTED_ABSOLUTE_PATH.search(value)
        or _WINDOWS_PATH.search(value)
        or _UNC_PATH.search(value)
        or _POSIX_PATH.search(value)
        or _UUID.search(value)
        or (assignment is not None and "<REDACTED>" not in assignment.group(0))
        or (bearer is not None and "<REDACTED>" not in bearer.group(0))
    ):
        raise ValueError("inventory report contains an unredacted sensitive value")


def _normalized_signature(value: str, *, replace_numbers: bool) -> str:
    normalized = _sanitize_text(value).casefold()
    if replace_numbers:
        normalized = _STANDALONE_NUMBER.sub("<n>", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def _relative_path(root: Path, path: Path) -> str:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("evidence path escaped run_root")
    relative = resolved.relative_to(root).as_posix()
    return relative or "."


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory real CodingRun retrieval inputs without changing runs."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = build_inventory(args.run_root, benchmark_paths=args.benchmark)
    output = _resolve_output_path(args.output, forbidden_root=args.run_root)
    write_report_create_new(report, output)
    return 0


def _resolve_output_path(output: Path, *, forbidden_root: Path) -> Path:
    expanded = output.expanduser()
    root = forbidden_root.resolve(strict=True)
    candidate = expanded.parent.resolve(strict=False) / expanded.name
    if candidate == root or candidate.is_relative_to(root):
        raise ValueError("inventory output must be outside run_root")
    expanded.parent.mkdir(parents=True, exist_ok=True)
    destination = expanded.parent.resolve(strict=True) / expanded.name
    if destination == root or destination.is_relative_to(root):
        raise ValueError("inventory output must be outside run_root")
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    return destination


if __name__ == "__main__":
    raise SystemExit(main())
