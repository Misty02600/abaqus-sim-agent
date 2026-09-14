"""宿主锁下的查证事件日志；hash链/head检测损坏，不宣称防恶意重写或DB事务。"""

import copy
import hashlib
import json
import os
from pathlib import Path

from .document_policy_contracts import JournalEntry, PolicyCheckpoint, PolicyEvent
from .document_policy_state import DocumentPolicyIntegrityError, PolicyState
from .persistence import write_text_atomic
from .run_lock import CodingRunFileLock

ZERO = "0" * 64
MAX_BYTES = 32 * 1024 * 1024


def entry_digest(entry: JournalEntry) -> str:
    return hashlib.sha256(
        json.dumps(
            entry.model_dump(mode="json", exclude={"sha256"}),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class PolicyJournal:
    def __init__(self, state_dir: Path, lock: CodingRunFileLock):
        lock.assert_held(state_dir)
        self.state_dir, self.lock = state_dir.resolve(), lock
        self.path = self.state_dir / "document_policy.jsonl"
        self.head_path = self.state_dir / "document_policy.head.json"
        for path in (self.path, self.head_path):
            if path.is_symlink() or path.is_junction():
                raise DocumentPolicyIntegrityError(
                    "policy files cannot be reparse points"
                )
        self.state = PolicyState()
        self.entries: list[JournalEntry] = []
        self._bytes = b""
        self._poisoned = False
        self._head_bytes: bytes | None = None

    def create(self) -> None:
        self.lock.assert_held(self.state_dir)
        if self.head_path.exists():
            raise DocumentPolicyIntegrityError("policy head already exists")
        with self.path.open("xb"):
            pass

    def restore(self, checkpoint: PolicyCheckpoint | None) -> None:
        """整份日志先验证，再恢复；落盘事件可领先旧head/外部快照，但不得落后。

        Note:
            缺少head或损坏的尾行不猜测修复。append已fsync而head尚未更新的完整
            事件可重放；checkpoint用于与将来CodingRun resume_state建立额外锚点。
        """
        self.lock.assert_held(self.state_dir)
        try:
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError("policy journal exceeds byte budget")
            payload = self.path.read_bytes()
            if not payload or not payload.endswith(b"\n"):
                raise ValueError("empty or partial policy journal")
            state = PolicyState()
            entries = []
            states_at_checkpoints = {}
            head_bytes = self.head_path.read_bytes()
            head = PolicyCheckpoint.model_validate_json(head_bytes)
            wanted = {head.sequence, checkpoint.sequence if checkpoint else -1}
            for line in payload.splitlines():
                entry = JournalEntry.model_validate_json(line)
                if (
                    entry.sequence != len(entries)
                    or entry.previous_sha256
                    != (entries[-1].sha256 if entries else ZERO)
                    or entry.sha256 != entry_digest(entry)
                ):
                    raise ValueError("policy sequence/hash chain changed")
                state.apply(entry.event)
                entries.append(entry)
                if entry.sequence in wanted:
                    states_at_checkpoints[entry.sequence] = (state.revision, state.used)
            if state.configuration is None or state.revision < 0:
                raise ValueError("policy initialization/revision is incomplete")
            for expected in (head, checkpoint):
                if expected is None:
                    continue
                if (
                    expected.run_id != state.configuration.run_id
                    or expected.sequence >= len(entries)
                ):
                    raise ValueError("policy checkpoint identity or sequence missing")
                if entries[
                    expected.sequence
                ].sha256 != expected.head_sha256 or states_at_checkpoints[
                    expected.sequence
                ] != (expected.task_revision, expected.cumulative_lookup_calls):
                    raise ValueError("policy checkpoint does not match journal prefix")
        except Exception as exc:
            raise DocumentPolicyIntegrityError(
                "cannot safely restore document policy"
            ) from exc
        self.state, self.entries, self._bytes = state, entries, payload
        self._head_bytes = head_bytes

    def assert_current(self) -> None:
        self.lock.assert_held(self.state_dir)
        if self._poisoned or self.path.read_bytes() != self._bytes:
            raise DocumentPolicyIntegrityError(
                "policy session is poisoned or journal changed externally"
            )
        current_head = self.head_path.read_bytes() if self.head_path.exists() else None
        if current_head != self._head_bytes:
            raise DocumentPolicyIntegrityError("policy head changed externally")

    def checkpoint(self) -> PolicyCheckpoint:
        self.assert_current()
        if self.state.configuration is None or not self.entries:
            raise DocumentPolicyIntegrityError("policy has no checkpoint")
        return PolicyCheckpoint(
            run_id=self.state.configuration.run_id,
            sequence=len(self.entries) - 1,
            head_sha256=self.entries[-1].sha256,
            task_revision=max(0, self.state.revision),
            cumulative_lookup_calls=self.state.used,
        )

    def append(self, event: PolicyEvent) -> None:
        self.assert_current()
        next_state = copy.deepcopy(self.state)
        entry = JournalEntry(
            sequence=len(self.entries),
            previous_sha256=self.entries[-1].sha256 if self.entries else ZERO,
            event=event,
            sha256=ZERO,
        )
        # JSON mode revalidates nested objects; model_copy/model_construct cannot bypass input validation.
        entry = JournalEntry.model_validate_json(entry.model_dump_json())
        next_state.apply(entry.event)
        entry = entry.model_copy(update={"sha256": entry_digest(entry)})
        payload = (entry.model_dump_json() + "\n").encode()
        if len(self._bytes) + len(payload) > MAX_BYTES:
            raise DocumentPolicyIntegrityError("policy journal byte budget exhausted")
        try:
            with self.path.open("ab", buffering=0) as stream:
                if stream.write(payload) != len(payload):
                    raise OSError("short policy append")
                os.fsync(stream.fileno())
            self._bytes += payload
            self.entries.append(entry)
            self.state = next_state
            if self.state.revision >= 0:
                write_text_atomic(self.head_path, self.checkpoint().model_dump_json())
                self._head_bytes = self.head_path.read_bytes()
        except BaseException:
            self._poisoned = True
            raise
