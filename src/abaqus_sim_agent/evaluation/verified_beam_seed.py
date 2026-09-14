from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.amendments import load_amendment_ledger
from abaqus_sim_agent.coding.contracts import (
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
)
from abaqus_sim_agent.evaluation.beam_tension import GOLD_REQUEST
from abaqus_sim_agent.evaluation.verified_plate_seed import (
    sha256_bytes,
    sha256_file,
)
from abaqus_sim_agent.verification.beam_tension import BeamTensionVerification

BEAM_SOURCE_ARTIFACTS = (
    "build_model.py",
    "extract_results.py",
    "beam_tension.cae",
    "beam_tension.inp",
    "beam_tension.odb",
    "results.json",
)
BEAM_AGENT_VISIBLE_SEED_FILES = ("build_model.py", "extract_results.py")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BeamSeedProvenance(_FrozenModel):
    source_run_id: str
    source_run_dir: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: dict[str, str]


@dataclass(frozen=True, slots=True)
class VerifiedBeamSeed:
    run_dir: Path
    workspace: Path
    state_dir: Path
    request: CodingRunRequest
    outcome: CodingRunOutcome
    verification: BeamTensionVerification
    provenance: BeamSeedProvenance
    seed_files: Mapping[str, bytes]

    @property
    def seed_texts(self) -> dict[str, str]:
        return {
            path: content.decode("utf-8") for path, content in self.seed_files.items()
        }


def load_verified_beam_seed(
    source_run_dir: Path,
    *,
    forbidden_root: Path | None = None,
) -> VerifiedBeamSeed:
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
    verification = BeamTensionVerification.model_validate_json(
        verification_path.read_text(encoding="utf-8")
    )
    if request.workspace != workspace or request.state_dir != state_dir:
        raise ValueError("source request paths do not match the supplied run directory")
    if "".join(request.goal.split()) != "".join(GOLD_REQUEST.split()):
        raise ValueError("source run is not the frozen from-scratch B31 Gold")
    if load_amendment_ledger(state_dir, run_id=request.run_id).sequence != 0:
        raise ValueError("source B31 run contains post-Gold amendments")
    if (
        outcome.run_id != request.run_id
        or outcome.status is not CodingRunStatus.COMPLETED
        or outcome.task_revision != 0
        or outcome.amendment_sequence != 0
    ):
        raise ValueError("source CodingRun is not the original completed revision")
    if outcome.clarifications:
        raise ValueError("source B31 run unexpectedly requested clarification")
    if not verification.passed:
        raise ValueError("source B31 verification did not pass")

    outcome_artifacts = {
        artifact.relative_path: artifact for artifact in outcome.artifacts
    }
    artifact_hashes: dict[str, str] = {}
    seed_files: dict[str, bytes] = {}
    for relative in BEAM_SOURCE_ARTIFACTS:
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
        if relative in BEAM_AGENT_VISIBLE_SEED_FILES:
            content.decode("utf-8")
            seed_files[relative] = content

    provenance = BeamSeedProvenance(
        source_run_id=outcome.run_id,
        source_run_dir=str(run_dir),
        request_sha256=sha256_file(request_path),
        outcome_sha256=sha256_file(outcome_path),
        verification_sha256=sha256_file(verification_path),
        artifact_sha256=artifact_hashes,
    )
    return VerifiedBeamSeed(
        run_dir=run_dir,
        workspace=workspace,
        state_dir=state_dir,
        request=request,
        outcome=outcome,
        verification=verification,
        provenance=provenance,
        seed_files=MappingProxyType(seed_files),
    )
