from dataclasses import dataclass
from uuid import uuid4

from abaqus_sim_agent.domain.models import (
    ArtifactManifest,
    JobHandle,
    JobPhase,
    JobResult,
)


@dataclass
class _MockJob:
    handle: JobHandle
    phase: JobPhase
    artifact: ArtifactManifest


class MockSimulationExecutor:
    def __init__(self) -> None:
        self._jobs: dict[str, _MockJob] = {}
        self._job_ids_by_key: dict[str, str] = {}

    async def submit(
        self,
        artifact: ArtifactManifest,
        *,
        idempotency_key: str,
    ) -> JobHandle:
        if existing_job_id := self._job_ids_by_key.get(idempotency_key):
            return self._jobs[existing_job_id].handle

        handle = JobHandle(job_id=str(uuid4()), idempotency_key=idempotency_key)
        self._jobs[handle.job_id] = _MockJob(
            handle=handle,
            phase=JobPhase.QUEUED,
            artifact=artifact,
        )
        self._job_ids_by_key[idempotency_key] = handle.job_id
        return handle

    async def get_status(self, handle: JobHandle) -> JobPhase:
        return self._get_job(handle).phase

    async def cancel(self, handle: JobHandle) -> None:
        self._get_job(handle).phase = JobPhase.CANCELLED

    async def get_logs(self, handle: JobHandle) -> list[str]:
        job = self._get_job(handle)
        return [f"mock job {job.handle.job_id}: {job.phase}"]

    async def get_result(self, handle: JobHandle) -> JobResult:
        job = self._get_job(handle)
        return JobResult(
            job_id=handle.job_id,
            phase=job.phase,
            artifact_paths={"input": job.artifact.input_file},
            summary="mock execution result",
        )

    async def metadata(self, handle: JobHandle) -> dict[str, str]:
        job = self._get_job(handle)
        return {"generation_id": job.artifact.generation_id}

    def set_phase(self, handle: JobHandle, phase: JobPhase) -> None:
        self._get_job(handle).phase = phase

    def _get_job(self, handle: JobHandle) -> _MockJob:
        try:
            return self._jobs[handle.job_id]
        except KeyError as exc:
            raise LookupError(f"unknown job: {handle.job_id}") from exc
