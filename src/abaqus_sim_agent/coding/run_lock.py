from __future__ import annotations

import os
from errno import EACCES, EAGAIN
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self


class CodingRunAlreadyLocked(RuntimeError):
    pass


class CodingRunFileLock:
    """持有一个由操作系统在进程退出时自动释放的非阻塞文件锁。"""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "coding_run.lock"
        self._stream: BinaryIO | None = None

    def __enter__(self) -> Self:
        if self._stream is not None:
            raise RuntimeError("CodingRunFileLock is already held by this instance")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b", buffering=0)
        try:
            os.set_inheritable(stream.fileno(), False)
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            _lock_nonblocking(stream)
        except BaseException:
            stream.close()
            raise
        self._stream = stream
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            _unlock(stream)
        finally:
            stream.close()

    def assert_held(self, state_dir: Path) -> None:
        """确认调用方仍持有同一state的锁，不重复获取已持有的OS锁。"""
        if (
            self._stream is None
            or self._stream.closed
            or self.path.resolve().parent != state_dir.resolve()
        ):
            raise RuntimeError("the caller must hold this state's CodingRun lock")


def _lock_nonblocking(stream: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in {EACCES, EAGAIN}:
            raise
        raise CodingRunAlreadyLocked(
            "CodingRun is already active for state directory: "
            f"{Path(stream.name).parent}"
        ) from exc


def _unlock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
