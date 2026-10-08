import hashlib
import os
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.contracts import ArtifactKind, ArtifactRef


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FileSnapshot(_StrictModel):
    relative_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceSnapshot(_StrictModel):
    files: tuple[FileSnapshot, ...] = ()

    def by_path(self) -> dict[str, FileSnapshot]:
        return {file.relative_path: file for file in self.files}


class ResolvedWorkspace(_StrictModel):
    workspace: Path
    state_dir: Path
    initial_snapshot: WorkspaceSnapshot


class WorkspaceResolutionError(ValueError):
    pass


def default_run_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "abaqus-sim-agent" / "runs"
    return Path.home() / ".local" / "share" / "abaqus-sim-agent" / "runs"


class WorkspaceResolver:
    def __init__(
        self,
        *,
        captured_cwd: Path | None = None,
        state_root: Path | None = None,
    ) -> None:
        self._captured_cwd = (captured_cwd or Path.cwd()).resolve()
        self._state_root = (state_root or self._default_state_root()).resolve()

    def resolve_interactive(
        self,
        *,
        run_id: str,
        workspace: Path | None = None,
        state_dir: Path | None = None,
    ) -> ResolvedWorkspace:
        resolved_workspace = self._resolve_existing_workspace(
            workspace or self._captured_cwd
        )
        resolved_state = self._resolve_state_dir(run_id, state_dir)
        self._check_state_is_outside_workspace(resolved_workspace, resolved_state)
        resolved_state.mkdir(parents=True, exist_ok=True)
        return ResolvedWorkspace(
            workspace=resolved_workspace,
            state_dir=resolved_state,
            initial_snapshot=self.snapshot(resolved_workspace),
        )

    def resolve_eval(
        self,
        *,
        run_id: str,
        workspace: Path | None,
        state_dir: Path | None = None,
    ) -> ResolvedWorkspace:
        if workspace is None:
            raise WorkspaceResolutionError(
                "eval runs require an explicitly supplied workspace"
            )
        resolved_workspace = self._resolve_existing_workspace(workspace)
        if any(resolved_workspace.iterdir()):
            raise WorkspaceResolutionError("eval workspace must be empty")
        resolved_state = self._resolve_state_dir(run_id, state_dir)
        self._check_state_is_outside_workspace(resolved_workspace, resolved_state)
        resolved_state.mkdir(parents=True, exist_ok=True)
        return ResolvedWorkspace(
            workspace=resolved_workspace,
            state_dir=resolved_state,
            initial_snapshot=self.snapshot(resolved_workspace),
        )

    def snapshot(self, workspace: Path) -> WorkspaceSnapshot:
        root = workspace.resolve()
        files = tuple(self._snapshot_files(root, root.rglob("*")))
        return WorkspaceSnapshot(files=files)

    @staticmethod
    def _default_state_root() -> Path:
        return default_run_root()

    def _resolve_state_dir(self, run_id: str, state_dir: Path | None) -> Path:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None:
            raise WorkspaceResolutionError("run_id contains unsupported characters")
        return (state_dir or self._state_root / run_id).resolve()

    @staticmethod
    def _resolve_existing_workspace(workspace: Path) -> Path:
        resolved = workspace.resolve()
        if not str(resolved).isascii():
            raise WorkspaceResolutionError("workspace path must contain only ASCII")
        if not resolved.exists():
            raise WorkspaceResolutionError("workspace does not exist")
        if not resolved.is_dir():
            raise WorkspaceResolutionError("workspace must be a directory")
        if not os.access(resolved, os.W_OK):
            raise WorkspaceResolutionError("workspace is not writable")
        return resolved

    @staticmethod
    def _check_state_is_outside_workspace(workspace: Path, state_dir: Path) -> None:
        if state_dir == workspace or state_dir.is_relative_to(workspace):
            raise WorkspaceResolutionError("state directory must be outside workspace")

    @staticmethod
    def _snapshot_files(
        root: Path,
        paths: Iterable[Path],
    ) -> Iterable[FileSnapshot]:
        for path in sorted(paths, key=lambda item: item.as_posix()):
            if path.is_symlink() or not path.is_file():
                continue
            relative_path = path.relative_to(root).as_posix()
            yield FileSnapshot(
                relative_path=relative_path,
                size_bytes=path.stat().st_size,
                sha256=_sha256(path),
            )


def artifact_evidence_from_snapshots(
    initial: WorkspaceSnapshot,
    current: WorkspaceSnapshot,
) -> tuple[tuple[ArtifactRef, ...], dict[str, bool]]:
    """将工作区快照转换为带终态哈希和新鲜度的产物证据。

    Args:
        initial: Agent 开始运行前的工作区快照。
        current: 要写入终态证据的当前工作区快照。

    Returns:
        当前文件的产物引用，以及按相对路径索引的新鲜度。新建文件或哈希发生变化
        的文件标记为新鲜；初始快照中存在且内容未变的文件标记为不新鲜。
    """
    before = initial.by_path()
    artifacts: list[ArtifactRef] = []
    freshness: dict[str, bool] = {}
    for item in current.files:
        previous = before.get(item.relative_path)
        fresh = previous is None or previous.sha256 != item.sha256
        freshness[item.relative_path] = fresh
        artifacts.append(
            ArtifactRef(
                relative_path=item.relative_path,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                kind=_artifact_kind(item.relative_path),
                fresh=fresh,
            )
        )
    return tuple(artifacts), freshness


def _artifact_kind(path: str) -> ArtifactKind:
    if path == "results.json":
        return ArtifactKind.RESULTS
    suffix = Path(path).suffix.casefold()
    return {
        ".py": ArtifactKind.SCRIPT,
        ".cae": ArtifactKind.CAE,
        ".inp": ArtifactKind.INPUT,
        ".odb": ArtifactKind.ODB,
        ".log": ArtifactKind.LOG,
        ".msg": ArtifactKind.LOG,
        ".sta": ArtifactKind.LOG,
        ".dat": ArtifactKind.LOG,
    }.get(suffix, ArtifactKind.OTHER)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
