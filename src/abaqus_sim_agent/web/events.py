"""有界、可丢弃的视图失效通知；断线不改变工作段。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ViewEvents:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subscribers: dict[str, set[asyncio.Queue[None]]] = {}

    def notify(self, run_id: str) -> None:
        loop = self.loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._notify, run_id)

    def _notify(self, run_id: str) -> None:
        for queue in tuple(self.subscribers.get(run_id, ())):
            if queue.empty():
                queue.put_nowait(None)

    @asynccontextmanager
    async def subscribe(self, run_id: str) -> AsyncIterator[asyncio.Queue[None]]:
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self.subscribers.setdefault(run_id, set()).add(queue)
        try:
            yield queue
        finally:
            subscribers = self.subscribers.get(run_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self.subscribers.pop(run_id, None)
