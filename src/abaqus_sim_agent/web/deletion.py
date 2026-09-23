"""永久删除宿主会话记录，保留工程目录和最小的删除回执。"""

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..coding.contracts import CodingRunRequest
from ..coding.resume import (
    RUNTIME_TOOL_NAMES,
    STEP_PERSISTENCE_DIRECTORY,
    create_step_store,
)
from ..coding.run_lock import CodingRunFileLock
from .contracts import WebError
from .storage import RunRegistry, plain_path, save_model


class DeletionReceipt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    run_id: str
    workspace: str
    state: Literal["purging", "deleted"]
    created_at: datetime


class DeletionStopReceipt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    run_id: str
    settled: bool = False


class RunDeletionStore:
    def __init__(self, registry: RunRegistry):
        self.registry = registry

    def path(self, run_id: str) -> Path:
        self.registry.directory(
            run_id
        )  # Validate the identifier before constructing paths.
        return plain_path(
            self.registry.root / "control" / "deletions" / f"{run_id}.json",
            directory=False,
        )

    def load(self, run_id: str) -> DeletionReceipt | None:
        path = self.path(run_id)
        if not path.exists():
            return None
        result = DeletionReceipt.model_validate_json(path.read_bytes())
        if result.run_id != run_id:
            raise WebError("deletion_corrupt", "删除回执与会话不一致。")
        return result

    def incomplete(self) -> tuple[DeletionReceipt, ...]:
        directory = plain_path(
            self.registry.root / "control" / "deletions", directory=True
        )
        receipts = []
        for path in directory.glob("*.json"):
            receipt = self.load(path.stem)
            if receipt is not None and receipt.state == "purging":
                receipts.append(receipt)
        return tuple(receipts)

    def _stop_path(self, run_id: str) -> Path:
        return plain_path(
            self.registry.directory(run_id) / "deletion-stop.json", directory=False
        )

    def stop_pending(self, run_id: str) -> bool:
        path = self._stop_path(run_id)
        if not path.exists():
            return False
        receipt = DeletionStopReceipt.model_validate_json(path.read_bytes())
        if receipt.run_id != run_id:
            raise WebError("deletion_corrupt", "删除收尾记录与会话不一致。")
        return not receipt.settled

    def begin_stop(self, run_id: str) -> None:
        save_model(self._stop_path(run_id), DeletionStopReceipt(run_id=run_id))

    def confirm_stopped(self, run_id: str) -> None:
        if self._stop_path(run_id).exists():
            save_model(
                self._stop_path(run_id),
                DeletionStopReceipt(run_id=run_id, settled=True),
            )

    @staticmethod
    def check_inactive_execution(request: CodingRunRequest) -> None:
        if not (request.state_dir / "resume_state.json").exists():
            return

        async def check() -> None:
            step_root = plain_path(
                request.state_dir / STEP_PERSISTENCE_DIRECTORY, directory=True
            )
            if not step_root.exists():
                raise WebError(
                    "delete_unresolved", "执行记录不完整，暂时无法删除会话。"
                )
            store = create_step_store(request.state_dir)
            runs = await store.list_runs(conversation_id=request.run_id)
            if runs:
                effects = await store.list_unresolved_tool_effects(
                    run_id=runs[-1].run_id
                )
                if any(effect.tool_name in RUNTIME_TOOL_NAMES for effect in effects):
                    raise WebError(
                        "delete_unresolved", "存在未确认结束的外部执行，记录未删除。"
                    )

        try:
            with CodingRunFileLock(request.state_dir):
                asyncio.run(check())
        except (ValueError, OSError) as exc:
            raise WebError(
                "delete_unresolved", "执行记录不可用，暂时无法删除会话。"
            ) from exc

    def purge(self, run_id: str, workspace: str) -> DeletionReceipt:
        """执行已确认的删除，可重试中断的清理；只删除固定管理子目录。

        Note:
            调用方必须已等待本宿主执行完全退出并持有 Web 调度锁。先验证所有目标，
            在 state 锁内删除内容，最后关闭并删除锁文件。workspace 仅用于回执展示。
        """
        state = self.registry.state_dir(run_id)
        registration = self.registry.directory(run_id)
        for target, parent in ((state, "states"), (registration, "registry")):
            self._validate_tree(target, self.registry.root / parent)
        receipt = self.load(run_id)
        if receipt is not None and receipt.state == "deleted":
            return receipt
        with CodingRunFileLock(state):
            if receipt is None:
                receipt = DeletionReceipt(
                    run_id=run_id,
                    workspace=workspace,
                    state="purging",
                    created_at=datetime.now(UTC),
                )
                self.path(run_id).parent.mkdir(parents=True, exist_ok=True)
                save_model(self.path(run_id), receipt)
            for child in state.iterdir():
                if child.name != "coding_run.lock":
                    self._remove(child, state)
            if registration.exists():
                self._remove(registration, self.registry.root / "registry")
        # Request/resume files are already gone; another CLI cannot resume this run.
        plain_path(state / "coding_run.lock", directory=False).unlink(missing_ok=True)
        state.rmdir()
        receipt = receipt.model_copy(update={"state": "deleted"})
        save_model(self.path(run_id), receipt)
        return receipt

    @staticmethod
    def _validate_tree(target: Path, parent: Path) -> None:
        resolved = plain_path(target).resolve()
        allowed = plain_path(parent, directory=True).resolve()
        if resolved == allowed or not resolved.is_relative_to(allowed):
            raise WebError("unsafe_delete", "删除目标超出了会话记录范围。", 400)
        if target.is_dir():
            for child in target.iterdir():
                RunDeletionStore._validate_tree(child, target)

    @staticmethod
    def _remove(target: Path, parent: Path) -> None:
        RunDeletionStore._validate_tree(target, parent)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
