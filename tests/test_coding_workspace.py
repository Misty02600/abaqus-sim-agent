import hashlib
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.workspace import (
    WorkspaceResolutionError,
    WorkspaceResolver,
    artifact_evidence_from_snapshots,
)


def test_interactive_defaults_to_captured_cwd_and_injected_state_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    state_root = tmp_path / "app-state"
    workspace.mkdir()
    resolver = WorkspaceResolver(captured_cwd=workspace, state_root=state_root)

    resolved = resolver.resolve_interactive(run_id="run-001")

    assert resolved.workspace == workspace.resolve()
    assert resolved.state_dir == (state_root / "run-001").resolve()
    assert resolved.state_dir.is_dir()
    assert not resolved.state_dir.is_relative_to(resolved.workspace)


def test_interactive_explicit_workspace_overrides_captured_cwd(
    tmp_path: Path,
) -> None:
    captured = tmp_path / "captured"
    explicit = tmp_path / "explicit"
    captured.mkdir()
    explicit.mkdir()
    resolver = WorkspaceResolver(
        captured_cwd=captured,
        state_root=tmp_path / "state-root",
    )

    resolved = resolver.resolve_interactive(run_id="run-001", workspace=explicit)

    assert resolved.workspace == explicit.resolve()


def test_eval_requires_explicit_empty_workspace(tmp_path: Path) -> None:
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")

    with pytest.raises(WorkspaceResolutionError, match="explicitly supplied"):
        resolver.resolve_eval(run_id="run-001", workspace=None)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "old.odb").write_bytes(b"stale")
    with pytest.raises(WorkspaceResolutionError, match="must be empty"):
        resolver.resolve_eval(run_id="run-001", workspace=nonempty)


def test_eval_snapshot_starts_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")

    resolved = resolver.resolve_eval(run_id="run-001", workspace=workspace)

    assert resolved.initial_snapshot.files == ()


def test_workspace_path_must_be_ascii(tmp_path: Path) -> None:
    workspace = tmp_path / "工作区"
    workspace.mkdir()
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")

    with pytest.raises(WorkspaceResolutionError, match="ASCII"):
        resolver.resolve_interactive(run_id="run-001", workspace=workspace)


def test_state_directory_cannot_be_nested_in_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")

    with pytest.raises(WorkspaceResolutionError, match="outside workspace"):
        resolver.resolve_interactive(
            run_id="run-001",
            workspace=workspace,
            state_dir=workspace / "state",
        )

    assert not (workspace / "state").exists()


def test_initial_snapshot_records_relative_path_size_and_hash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "scripts"
    nested.mkdir(parents=True)
    content = b"print('hello')\n"
    (nested / "build_model.py").write_bytes(content)
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")

    resolved = resolver.resolve_interactive(run_id="run-001", workspace=workspace)

    [file] = resolved.initial_snapshot.files
    assert file.relative_path == "scripts/build_model.py"
    assert file.size_bytes == len(content)
    assert file.sha256 == hashlib.sha256(content).hexdigest()


def test_artifact_evidence_uses_current_hash_and_initial_snapshot_freshness(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "build_model.py"
    unchanged = workspace / "existing.inp"
    script.write_text("old content", encoding="utf-8")
    unchanged.write_text("unchanged", encoding="utf-8")
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")
    initial = resolver.snapshot(workspace)

    final_content = "final content"
    script.write_text(final_content, encoding="utf-8")
    (workspace / "results.json").write_text("{}", encoding="utf-8")
    current = resolver.snapshot(workspace)

    artifacts, freshness = artifact_evidence_from_snapshots(initial, current)

    by_path = {artifact.relative_path: artifact for artifact in artifacts}
    assert freshness == {
        "build_model.py": True,
        "existing.inp": False,
        "results.json": True,
    }
    assert (
        by_path["build_model.py"].sha256
        == hashlib.sha256(final_content.encode()).hexdigest()
    )
    assert by_path["build_model.py"].fresh
    assert not by_path["existing.inp"].fresh


def test_nonexistent_workspace_is_rejected(tmp_path: Path) -> None:
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")

    with pytest.raises(WorkspaceResolutionError, match="does not exist"):
        resolver.resolve_interactive(
            run_id="run-001",
            workspace=tmp_path / "missing",
        )


def test_run_id_cannot_escape_state_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolver = WorkspaceResolver(state_root=tmp_path / "state-root")

    with pytest.raises(WorkspaceResolutionError, match="unsupported characters"):
        resolver.resolve_interactive(run_id="..", workspace=workspace)
