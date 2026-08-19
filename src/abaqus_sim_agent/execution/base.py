from collections.abc import Mapping, Sequence
from typing import Protocol

from abaqus_sim_agent.domain.models import (
    ArtifactManifest,
    JobHandle,
    JobPhase,
    JobResult,
)


class SimulationExecutor(Protocol):
    async def submit(
        self,
        artifact: ArtifactManifest,
        *,
        idempotency_key: str,
    ) -> JobHandle: ...

    async def get_status(self, handle: JobHandle) -> JobPhase: ...

    async def cancel(self, handle: JobHandle) -> None: ...

    async def get_logs(self, handle: JobHandle) -> Sequence[str]: ...

    async def get_result(self, handle: JobHandle) -> JobResult: ...

    async def metadata(self, handle: JobHandle) -> Mapping[str, str]: ...
