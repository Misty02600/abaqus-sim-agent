"""单进程评测内共享的Abaqus运行闸门；不是系统级许可证协调器。"""

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path

from abaqus_sim_agent.execution.abaqus2024 import ProcessOutput, ProcessStrategy


class SerialRuntime:
    def __init__(self, semaphore: asyncio.Semaphore, process: ProcessStrategy):
        self.semaphore = semaphore
        self.process = process
        self.wait_seconds = 0.0

    async def run(
        self, command: Sequence[str], *, cwd: Path, timeout_seconds: float
    ) -> ProcessOutput:
        """等待计入调用方的wall预算；取消等待不启动进程，清理完成前不释放占用。"""
        started = time.perf_counter()
        try:
            await self.semaphore.acquire()
        finally:
            self.wait_seconds += time.perf_counter() - started
        try:
            return await self.process.run(
                command, cwd=cwd, timeout_seconds=timeout_seconds
            )
        finally:
            self.semaphore.release()
