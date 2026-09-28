from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.amendments import load_amendment_ledger
from abaqus_sim_agent.coding.contracts import (
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
)
from abaqus_sim_agent.coding.workspace import ResolvedWorkspace, WorkspaceResolver
from abaqus_sim_agent.evaluation.seed_contracts import PLATE_SOURCE_GOAL as GOLD_REQUEST
from abaqus_sim_agent.verification.plate_reference import PlateTensionVerification

SOURCE_ARTIFACTS = (
    "build_model.py",
    "extract_results.py",
    "plate_tension.cae",
    "plate_tension.inp",
    "plate_tension.odb",
    "results.json",
)
AGENT_VISIBLE_SEED_FILES = ("build_model.py", "extract_results.py")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class VerifiedModelSeed(Protocol):
    @property
    def outcome(self) -> CodingRunOutcome: ...

    @property
    def provenance(self) -> BaseModel: ...

    @property
    def seed_files(self) -> Mapping[str, bytes]: ...

    @property
    def seed_texts(self) -> dict[str, str]: ...


class PlateSeedProvenance(_FrozenModel):
    source_run_id: str
    source_run_dir: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: dict[str, str]


@dataclass(frozen=True, slots=True)
class VerifiedPlateSeed:
    run_dir: Path
    workspace: Path
    state_dir: Path
    request: CodingRunRequest
    outcome: CodingRunOutcome
    verification: PlateTensionVerification
    provenance: PlateSeedProvenance
    seed_files: Mapping[str, bytes]

    @property
    def seed_texts(self) -> dict[str, str]:
        return {
            path: content.decode("utf-8") for path, content in self.seed_files.items()
        }


def load_verified_plate_seed(
    source_run_dir: Path,
    *,
    forbidden_root: Path | None = None,
) -> VerifiedPlateSeed:
    run_dir = source_run_dir.resolve(strict=True)
    if forbidden_root is not None:
        forbidden = forbidden_root.resolve()
        if (
            run_dir == forbidden
            or run_dir.is_relative_to(forbidden)
            or forbidden.is_relative_to(run_dir)
        ):
            raise ValueError("source run must be outside the new evaluation root")
    workspace = (run_dir / "workspace").resolve(strict=True)
    state_dir = (run_dir / "state").resolve(strict=True)
    request_path = state_dir / "request.json"
    outcome_path = state_dir / "outcome.json"
    verification_path = state_dir / "verification.json"
    request = CodingRunRequest.model_validate_json(
        request_path.read_text(encoding="utf-8")
    )
    outcome = CodingRunOutcome.model_validate_json(
        outcome_path.read_text(encoding="utf-8")
    )
    verification = PlateTensionVerification.model_validate_json(
        verification_path.read_text(encoding="utf-8")
    )
    if request.workspace != workspace or request.state_dir != state_dir:
        raise ValueError("source request paths do not match the supplied run directory")
    if "".join(request.goal.split()) != "".join(GOLD_REQUEST.split()):
        raise ValueError("source run is not the frozen from-scratch plate Gold")
    if load_amendment_ledger(state_dir, run_id=request.run_id).sequence != 0:
        raise ValueError("source plate run contains post-Gold amendments")
    if (
        outcome.run_id != request.run_id
        or outcome.status is not CodingRunStatus.COMPLETED
        or outcome.task_revision != 0
        or outcome.amendment_sequence != 0
    ):
        raise ValueError("source CodingRun is not the original completed revision")
    if not verification.passed:
        raise ValueError("source plate verification did not pass")
    if not (
        len(outcome.clarifications) == 1
        and outcome.clarifications[0].field_id == "plate_thickness"
        and outcome.clarifications[0].answer == "1 mm"
        and outcome.clarifications[0].abaqus_calls_before_answer == 0
    ):
        raise ValueError("source run did not satisfy the plate thickness contract")

    outcome_artifacts = {
        artifact.relative_path: artifact for artifact in outcome.artifacts
    }
    artifact_hashes: dict[str, str] = {}
    seed_files: dict[str, bytes] = {}
    for relative in SOURCE_ARTIFACTS:
        unresolved = workspace / relative
        if unresolved.is_symlink():
            raise ValueError(f"source artifact must not be a symlink: {relative}")
        source = unresolved.resolve(strict=True)
        if not source.is_file() or not source.is_relative_to(workspace):
            raise ValueError(f"invalid source artifact: {relative}")
        content = source.read_bytes()
        artifact = outcome_artifacts.get(relative)
        digest = sha256_bytes(content)
        if artifact is None or not artifact.fresh or artifact.sha256 != digest:
            raise ValueError(f"source artifact evidence mismatch: {relative}")
        artifact_hashes[relative] = digest
        if relative in AGENT_VISIBLE_SEED_FILES:
            content.decode("utf-8")
            seed_files[relative] = content
    provenance = PlateSeedProvenance(
        source_run_id=outcome.run_id,
        source_run_dir=str(run_dir),
        request_sha256=sha256_file(request_path),
        outcome_sha256=sha256_file(outcome_path),
        verification_sha256=sha256_file(verification_path),
        artifact_sha256=artifact_hashes,
    )
    return VerifiedPlateSeed(
        run_dir=run_dir,
        workspace=workspace,
        state_dir=state_dir,
        request=request,
        outcome=outcome,
        verification=verification,
        provenance=provenance,
        seed_files=MappingProxyType(seed_files),
    )


def create_seeded_workspace(
    run_root: Path,
    run_id: str,
    seed: VerifiedModelSeed,
) -> ResolvedWorkspace:
    run_dir = run_root / run_id
    workspace = run_dir / "workspace"
    state_dir = run_dir / "state"
    workspace.mkdir(parents=True, exist_ok=False)
    resolver = WorkspaceResolver(state_root=run_root)
    empty = resolver.resolve_eval(
        run_id=run_id,
        workspace=workspace,
        state_dir=state_dir,
    )
    for relative, content in seed.seed_files.items():
        (empty.workspace / relative).write_bytes(content)
    initial = resolver.snapshot(empty.workspace)
    (empty.state_dir / "seed_provenance.json").write_text(
        seed.provenance.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return ResolvedWorkspace(
        workspace=empty.workspace,
        state_dir=empty.state_dir,
        initial_snapshot=initial,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
