from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import perf_counter


@dataclass(slots=True)
class ActiveWallClock:
    """累计宿主主动工作时间，并暂停外层 active-wall timeout。"""

    _prior_elapsed: float = 0.0
    _started_at: float = field(default_factory=perf_counter)
    _excluded_seconds: float = 0.0
    _pause_depth: int = 0
    _pause_started_at: float | None = None
    _paused_timeout_remaining: float | None = None
    _timeout: asyncio.Timeout | None = None

    def reset(self, elapsed_seconds: float = 0.0) -> None:
        if elapsed_seconds < 0:
            raise ValueError("active wall elapsed time cannot be negative")
        self._prior_elapsed = elapsed_seconds
        self._started_at = perf_counter()
        self._excluded_seconds = 0.0
        self._pause_depth = 0
        self._pause_started_at = None
        self._paused_timeout_remaining = None
        self._timeout = None

    def attach_timeout(self, timeout: asyncio.Timeout) -> None:
        self._timeout = timeout

    @property
    def elapsed_seconds(self) -> float:
        excluded = self._excluded_seconds
        if self._pause_started_at is not None:
            excluded += perf_counter() - self._pause_started_at
        return self._prior_elapsed + max(
            0.0,
            perf_counter() - self._started_at - excluded,
        )

    def remaining_seconds(self, limit_seconds: float | None) -> float | None:
        if limit_seconds is None:
            return None
        return max(0.0, limit_seconds - self.elapsed_seconds)

    @asynccontextmanager
    async def pause(self) -> AsyncIterator[None]:
        if self._pause_depth == 0:
            self._pause_started_at = perf_counter()
            timeout = self._timeout
            if timeout is not None and (deadline := timeout.when()) is not None:
                self._paused_timeout_remaining = max(
                    0.0,
                    deadline - asyncio.get_running_loop().time(),
                )
                timeout.reschedule(None)
        self._pause_depth += 1
        try:
            yield
        finally:
            self._pause_depth -= 1
            if self._pause_depth == 0:
                assert self._pause_started_at is not None
                self._excluded_seconds += perf_counter() - self._pause_started_at
                self._pause_started_at = None
                if (
                    self._timeout is not None
                    and self._paused_timeout_remaining is not None
                ):
                    self._timeout.reschedule(
                        asyncio.get_running_loop().time()
                        + self._paused_timeout_remaining
                    )
                self._paused_timeout_remaining = None
