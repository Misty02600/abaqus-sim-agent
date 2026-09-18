import asyncio

import pytest

from abaqus_sim_agent.coding.active_wall import ActiveWallClock


@pytest.mark.asyncio
async def test_pause_excludes_wait_and_reschedules_active_timeout() -> None:
    clock = ActiveWallClock()
    clock.reset(2.0)

    async with asyncio.timeout(0.05) as timeout:
        clock.attach_timeout(timeout)
        await asyncio.sleep(0.01)
        async with clock.pause():
            await asyncio.sleep(0.06)
        await asyncio.sleep(0.01)

    assert 2.0 <= clock.elapsed_seconds < 2.05


@pytest.mark.asyncio
async def test_active_timeout_still_expires_outside_pause() -> None:
    clock = ActiveWallClock()
    clock.reset()

    async def exceed_limit() -> None:
        async with asyncio.timeout(0.01) as timeout:
            clock.attach_timeout(timeout)
            await asyncio.sleep(0.03)

    with pytest.raises(TimeoutError):
        await exceed_limit()
