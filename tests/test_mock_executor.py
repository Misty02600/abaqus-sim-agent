import pytest

from abaqus_sim_agent.domain.models import ArtifactManifest, JobPhase
from abaqus_sim_agent.execution.mock import MockSimulationExecutor


def make_artifact() -> ArtifactManifest:
    return ArtifactManifest(
        generation_id="generation-1",
        input_file="artifacts/model.inp",
        content_sha256="0" * 64,
    )


@pytest.mark.asyncio
async def test_submit_is_idempotent_for_the_same_key() -> None:
    executor = MockSimulationExecutor()

    first = await executor.submit(
        make_artifact(), idempotency_key="task-1:generation-1"
    )
    second = await executor.submit(
        make_artifact(), idempotency_key="task-1:generation-1"
    )

    assert second == first


@pytest.mark.asyncio
async def test_job_can_be_resumed_and_completed() -> None:
    executor = MockSimulationExecutor()
    handle = await executor.submit(
        make_artifact(), idempotency_key="task-1:generation-1"
    )

    executor.set_phase(handle, JobPhase.RUNNING)
    assert await executor.get_status(handle) is JobPhase.RUNNING

    executor.set_phase(handle, JobPhase.SUCCEEDED)
    result = await executor.get_result(handle)

    assert result.phase is JobPhase.SUCCEEDED
    assert result.artifact_paths["input"] == "artifacts/model.inp"


@pytest.mark.asyncio
async def test_cancel_changes_visible_status() -> None:
    executor = MockSimulationExecutor()
    handle = await executor.submit(
        make_artifact(), idempotency_key="task-1:generation-1"
    )

    await executor.cancel(handle)

    assert await executor.get_status(handle) is JobPhase.CANCELLED
