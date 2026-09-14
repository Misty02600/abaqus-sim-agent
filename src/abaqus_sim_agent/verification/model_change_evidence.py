from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from abaqus_sim_agent.coding.workspace import FileSnapshot, WorkspaceSnapshot
from abaqus_sim_agent.execution import (
    AbaqusExecutionResult,
    AbaqusInvocation,
    ArtifactChangeKind,
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorkspaceDiffEntry(_FrozenModel):
    path: str
    kind: Literal["created", "modified", "deleted"]
    before: FileSnapshot | None = None
    after: FileSnapshot | None = None


class WorkspaceChangeReport(_FrozenModel):
    source_run_id: str
    changes: tuple[WorkspaceDiffEntry, ...]
    build_model_patch: str
    extract_results_patch: str


class ModelChangeExecutionProvenance(_FrozenModel):
    cae_generated_current_model: bool
    cae_did_not_solve: bool
    solver_generated_current_odb: bool
    extraction_generated_current_results: bool

    @property
    def passed(self) -> bool:
        return all(
            (
                self.cae_generated_current_model,
                self.cae_did_not_solve,
                self.solver_generated_current_odb,
                self.extraction_generated_current_results,
            )
        )


def build_workspace_change_report(
    initial: WorkspaceSnapshot,
    current: WorkspaceSnapshot,
    *,
    source_run_id: str,
    seed_texts: Mapping[str, str],
    workspace: Path,
) -> WorkspaceChangeReport:
    return WorkspaceChangeReport(
        source_run_id=source_run_id,
        changes=_workspace_changes(initial, current),
        build_model_patch=_text_patch(
            seed_texts.get("build_model.py", ""),
            _read_text(workspace / "build_model.py"),
            "build_model.py",
        ),
        extract_results_patch=_text_patch(
            seed_texts.get("extract_results.py", ""),
            _read_text(workspace / "extract_results.py"),
            "extract_results.py",
        ),
    )


def persist_workspace_change_evidence(
    state_dir: Path,
    initial: WorkspaceSnapshot,
    current: WorkspaceSnapshot,
    report: WorkspaceChangeReport,
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "initial_snapshot.json").write_text(
        initial.model_dump_json(indent=2), encoding="utf-8"
    )
    (state_dir / "final_snapshot.json").write_text(
        current.model_dump_json(indent=2), encoding="utf-8"
    )
    (state_dir / "workspace_diff.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    (state_dir / "build_model.patch").write_text(
        report.build_model_patch, encoding="utf-8"
    )
    (state_dir / "extract_results.patch").write_text(
        report.extract_results_patch, encoding="utf-8"
    )


def write_workspace_change_evidence(
    state_dir: Path,
    initial: WorkspaceSnapshot,
    current: WorkspaceSnapshot,
    *,
    source_run_id: str,
    seed_texts: Mapping[str, str],
    workspace: Path,
) -> WorkspaceChangeReport:
    report = build_workspace_change_report(
        initial,
        current,
        source_run_id=source_run_id,
        seed_texts=seed_texts,
        workspace=workspace,
    )
    persist_workspace_change_evidence(state_dir, initial, current, report)
    return report


def model_change_execution_provenance(
    executions: Sequence[AbaqusExecutionResult],
    current: WorkspaceSnapshot,
    *,
    artifact_stem: str = "plate_tension",
    build_script: str = "build_model.py",
    extract_script: str = "extract_results.py",
) -> ModelChangeExecutionProvenance:
    current_by_path = current.by_path()
    cae_results = [
        result
        for result in executions
        if result.invocation is AbaqusInvocation.CAE_NOGUI and result.succeeded
    ]
    cae_generated = any(
        _result_matches_current(
            result,
            current_by_path,
            (f"{artifact_stem}.cae", f"{artifact_stem}.inp"),
        )
        and any(f"noGUI={build_script}" in argument for argument in result.command)
        for result in cae_results
    )
    cae_did_not_solve = all(
        not any(
            change.path.casefold().endswith((".odb", ".sta", ".msg", ".dat"))
            for change in result.artifact_changes
        )
        for result in cae_results
    )
    solver_generated = any(
        _result_matches_current(result, current_by_path, (f"{artifact_stem}.odb",))
        and f"job={artifact_stem}" in result.command
        and f"input={artifact_stem}.inp" in result.command
        for result in executions
        if result.invocation is AbaqusInvocation.SOLVER_JOB and result.succeeded
    )
    extraction_generated = any(
        _result_matches_current(result, current_by_path, ("results.json",))
        and extract_script in result.command
        for result in executions
        if result.invocation is AbaqusInvocation.ABAQUS_PYTHON and result.succeeded
    )
    return ModelChangeExecutionProvenance(
        cae_generated_current_model=cae_generated,
        cae_did_not_solve=cae_did_not_solve,
        solver_generated_current_odb=solver_generated,
        extraction_generated_current_results=extraction_generated,
    )


def status_completed(path: Path) -> bool:
    if not path.is_file():
        return False
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")
    return "analysis has completed successfully" in text.casefold()


def _workspace_changes(
    initial: WorkspaceSnapshot,
    current: WorkspaceSnapshot,
) -> tuple[WorkspaceDiffEntry, ...]:
    before = initial.by_path()
    after = current.by_path()
    changes: list[WorkspaceDiffEntry] = []
    for path in sorted(before.keys() | after.keys()):
        old = before.get(path)
        new = after.get(path)
        if old is None:
            changes.append(WorkspaceDiffEntry(path=path, kind="created", after=new))
        elif new is None:
            changes.append(WorkspaceDiffEntry(path=path, kind="deleted", before=old))
        elif old.sha256 != new.sha256:
            changes.append(
                WorkspaceDiffEntry(
                    path=path,
                    kind="modified",
                    before=old,
                    after=new,
                )
            )
    return tuple(changes)


def _text_patch(before: str, after: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"seed/{path}",
            tofile=f"final/{path}",
        )
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        return ""


def _result_matches_current(
    result: AbaqusExecutionResult,
    current: Mapping[str, FileSnapshot],
    paths: tuple[str, ...],
) -> bool:
    after_hashes = {
        change.path: change.after.sha256
        for change in result.artifact_changes
        if change.kind in {ArtifactChangeKind.CREATED, ArtifactChangeKind.MODIFIED}
        and change.after is not None
    }
    return all(
        path in current
        and path in after_hashes
        and after_hashes[path] == current[path].sha256
        for path in paths
    )
